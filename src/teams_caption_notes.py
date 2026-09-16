#!/usr/bin/env python3
"""Capture visible Microsoft Teams live captions through Windows UI Automation.

This tool does not record audio or join meetings. Teams captions must already be
enabled in a meeting the signed-in user is attending.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Sequence
from caption_journal import CaptionJournal
from meeting_presence import (COMPACT_TITLES, MeetingPresence, MeetingSelector,
                              MeetingSurface, WindowIdentity, native_window_name)


LOG = logging.getLogger("teams-caption-notes")
CAPTION_MARKER = re.compile(r"\b(live captions?|closed captions?|captions?|transcript)\b", re.I)
TEAMS_TITLE = re.compile(r"\bteams\b", re.I)
NOISE = re.compile(
    r"^((hide|show) live captions?(\s*\([^)]*\))?|mute|unmute|camera|people|chat|react|raise|share|more|leave|hang up|"
    r"meeting controls|show conversation|view|apps|copilot|microphone|speaker)$",
    re.I,
)
CAPTION_VIEWER_TITLE = re.compile(r"^\s*captions?\s*(?:\||$)", re.I)
HOLD_STATUS = re.compile(r"^live captions? (?:are |is )?paused while on hold[.!]?$", re.I)


@dataclass(frozen=True)
class Caption:
    timestamp: str
    speaker: str | None
    text: str


def application_dir() -> Path:
    """Directory containing the source script or frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def clean_text(value: str) -> str:
    value = value.replace("\u200b", " ").replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def normalized(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def parse_caption(value: str) -> tuple[str | None, str] | None:
    """Parse common Teams accessibility-name caption shapes."""
    value = clean_text(value)
    if not value or len(value) < 2 or NOISE.fullmatch(value) or HOLD_STATUS.fullmatch(value):
        return None

    lines = value.splitlines()
    # A Teams control can expose several spoken lines in one accessibility
    # value. Do not assume the first line is a name: a salutation such as
    # A spoken greeting such as "Hi, Jordan." must not become a speaker label.
    if len(lines) >= 2 and is_probable_speaker_label(lines[0]):
        speaker = lines[0].rstrip(":")
        text = " ".join(lines[1:]).strip()
        if text and not NOISE.fullmatch(text) and not HOLD_STATUS.fullmatch(text):
            return speaker, text
        if HOLD_STATUS.fullmatch(text):
            return None

    match = re.match(r"^([^:\n]{1,80}):\s+(.+)$", value, re.S)
    if match and is_probable_speaker_label(match.group(1)):
        if HOLD_STATUS.fullmatch(match.group(2).strip()):
            return None
        return match.group(1).strip(), match.group(2).strip()

    return None, value.replace("\n", " ")


class Transcript:
    """Merge rolling UI caption updates into stable transcript entries."""

    def __init__(self, recent_limit: int = 500) -> None:
        self.entries: list[Caption] = []
        self._recent: list[str] = []
        self._recent_text: list[str] = []
        self._recent_limit = recent_limit
        self._active_speaker: str | None = None

    def ingest(self, speaker: str | None, text: str, timestamp: str | None = None) -> bool:
        parsed = parse_caption(f"{speaker}\n{text}" if speaker else text)
        if not parsed:
            return False
        speaker, text = parsed
        # Some Teams caption surfaces expose the name and utterance as separate
        # UIA updates. Keep a strict Lastname, Firstname-style label as state and
        # attach it to subsequent speech instead of writing it as a transcript line.
        if speaker is None and is_strict_speaker_label(text):
            self._active_speaker = text
            return False
        if speaker:
            self._active_speaker = speaker
        elif self._active_speaker:
            speaker = self._active_speaker
        fingerprint = normalized(f"{speaker or ''} {text}")
        if not fingerprint or fingerprint in self._recent:
            return False

        # A lagging Teams surface can repeat a complete utterance under the
        # next active speaker. Suppress substantial verbatim repeats across
        # speakers, while allowing genuine short replies such as "OK".
        text_fingerprint = normalized(text)
        if len(text_fingerprint.split()) >= 5 and text_fingerprint in self._recent_text:
            return False

        stamp = timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
        if self.entries:
            previous = self.entries[-1]
            same_speaker = normalized(previous.speaker or "") == normalized(speaker or "")
            old_text, new_text = normalized(previous.text), normalized(text)
            # Teams frequently exposes a growing partial sentence. Replace that
            # final partial entry instead of writing every intermediate update.
            if same_speaker and old_text and new_text.startswith(old_text):
                self.entries[-1] = Caption(previous.timestamp, speaker, text)
                self._remember(fingerprint)
                self._remember_text(text_fingerprint)
                return True
            if same_speaker and new_text and old_text.startswith(new_text):
                return False

        self.entries.append(Caption(stamp, speaker, text))
        self._remember(fingerprint)
        self._remember_text(text_fingerprint)
        return True

    def _remember(self, fingerprint: str) -> None:
        self._recent.append(fingerprint)
        if len(self._recent) > self._recent_limit:
            del self._recent[: len(self._recent) - self._recent_limit]

    def _remember_text(self, fingerprint: str) -> None:
        self._recent_text.append(fingerprint)
        if len(self._recent_text) > self._recent_limit:
            del self._recent_text[: len(self._recent_text) - self._recent_limit]


def process_image_name(pid: int) -> str:
    """Return a Windows process image without adding a psutil dependency."""
    if os.name != "nt":
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.casefold()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def safe_attr(control: object, name: str, default=None):
    try:
        value = getattr(control, name, default)
        return value() if callable(value) else value
    except Exception:
        return default


def walk_controls(root: object, max_depth: int = 32, max_nodes: int = 20000) -> Iterator[tuple[object, int, tuple[str, ...]]]:
    """Iteratively walk a UIA subtree; Teams can have a very large tree."""
    stack: list[tuple[object, int, tuple[str, ...]]] = [(root, 0, ())]
    seen = 0
    while stack and seen < max_nodes:
        control, depth, ancestors = stack.pop()
        seen += 1
        name = clean_text(str(safe_attr(control, "Name", "") or ""))
        yield control, depth, ancestors
        if depth >= max_depth:
            continue
        try:
            children = list(control.GetChildren())
        except Exception:
            children = []
        next_ancestors = (*ancestors[-5:], name) if name else ancestors[-6:]
        for child in reversed(children):
            stack.append((child, depth + 1, next_ancestors))


def is_teams_window(control: object) -> bool:
    title = str(safe_attr(control, "Name", "") or "")
    pid = int(safe_attr(control, "ProcessId", 0) or 0)
    image = process_image_name(pid)
    return bool(TEAMS_TITLE.search(title) or image in {"ms-teams.exe", "teams.exe"})


def find_teams_windows(auto_module: object) -> list[object]:
    root = auto_module.GetRootControl()
    windows = []
    for control in root.GetChildren():
        # Minimized/offscreen windows still establish that a known call exists.
        if is_teams_window(control):
            windows.append(control)
    return windows


def rect_tuple(control: object) -> tuple[float, float, float, float] | None:
    rect = safe_attr(control, "BoundingRectangle")
    if not rect:
        return None
    try:
        return float(rect.left), float(rect.top), float(rect.right), float(rect.bottom)
    except Exception:
        return None


def looks_like_speaker(value: str, occurrences: dict[str, int]) -> bool:
    """Recognize the speaker-label rows emitted by the Teams caption WebView."""
    value = clean_text(value)
    key = normalized(value)
    if not value or len(value) > 80 or any(mark in value for mark in ("|", "@", "http")):
        return False
    if is_strict_speaker_label(value):
        return True
    # Teams repeats a label above each utterance. Repetition is a safer signal
    # for names such as "John Smith" than capitalization alone.
    return occurrences.get(key, 0) >= 2 and 1 <= len(value.split()) <= 6 and not value.endswith(('.', '?', '!'))


def is_strict_speaker_label(value: str) -> bool:
    """Match Teams' standalone corporate-directory speaker labels."""
    value = clean_text(value)
    if value.endswith((".", "?", "!")):
        return False
    first = value.split(",", 1)[0].casefold().strip()
    if first in {"hi", "hey", "hello", "ok", "okay", "yes", "no", "thanks"}:
        return False
    return bool(
        re.fullmatch(
            r"[A-Z][\w'’.-]*(?:[ -][A-Z][\w'’.-]*)*,\s*"
            r"[A-Z][\w'’.-]*(?:[ -][A-Z][\w'’.-]*)*"
            r"(?:\s+\((?i:Contr|Guest|External)\))?",
            value,
        )
    )


def is_probable_speaker_label(value: str) -> bool:
    """Recognize a name without confusing ordinary caption prose for one."""
    value = clean_text(value).rstrip(":")
    if is_strict_speaker_label(value):
        return True
    if not value or len(value) > 80 or len(value.split()) > 6:
        return False
    if value.endswith((".", "?", "!", ",")):
        return False
    # These are common beginnings of spoken sentences and greetings, not names.
    first = value.split()[0].casefold().strip(",")
    if first in {"hi", "hey", "hello", "ok", "okay", "yes", "no", "thanks", "thank"}:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'’.-]*", value)
    return 1 <= len(words) <= 6 and all(word[0].isupper() for word in words)


def meeting_title_from_window(window_title: str) -> str | None:
    """Extract the meeting name without accepting normal Teams app pages."""
    parts = [part.strip() for part in clean_text(window_title).split("|")]
    if not parts:
        return None
    if CAPTION_VIEWER_TITLE.search(window_title):
        return parts[1] if len(parts) > 1 and parts[1] else "Meeting"
    first = parts[0]
    app_pages = {"activity", "chat", "calendar", "calls", "onedrive", "teams", "microsoft teams"}
    return first if first.casefold() not in app_pages else None


def pinned_viewer_strings(window: object, nodes: list[tuple[object, int, tuple[str, ...]]]) -> list[str]:
    """Extract speaker/utterance pairs from Teams' detached caption WebView."""
    window_name = clean_text(str(safe_attr(window, "Name", "") or ""))
    items: list[tuple[tuple[float, float], str]] = []
    allowed = {"TextControl", "ListItemControl", "DataItemControl", "CustomControl", "GroupControl"}
    for control, depth, ancestors in nodes:
        name = clean_text(str(safe_attr(control, "Name", "") or ""))
        ctype = str(safe_attr(control, "ControlTypeName", "") or "")
        if not name or name == window_name or ctype not in allowed or bool(safe_attr(control, "IsOffscreen", False)):
            continue
        if not any(CAPTION_MARKER.fullmatch(clean_text(ancestor)) for ancestor in ancestors):
            continue
        if NOISE.fullmatch(name) or CAPTION_MARKER.search(name) or "@" in name or " | " in name:
            continue
        try:
            has_children = bool(control.GetChildren())
        except Exception:
            has_children = False
        # Prefer browser accessibility leaf nodes so parent groups do not emit
        # the same speaker and sentence a second time.
        if has_children:
            continue
        rect = rect_tuple(control)
        position = (rect[1], rect[0]) if rect else (float(depth), 0.0)
        items.append((position, name))

    ordered: list[str] = []
    seen_positioned: set[tuple[tuple[float, float], str]] = set()
    for position, value in sorted(items, key=lambda item: item[0]):
        marker = (position, normalized(value))
        if marker not in seen_positioned:
            seen_positioned.add(marker)
            ordered.append(value)

    counts: dict[str, int] = {}
    for value in ordered:
        key = normalized(value)
        counts[key] = counts.get(key, 0) + 1

    paired: list[str] = []
    index = 0
    while index < len(ordered):
        value = ordered[index]
        if looks_like_speaker(value, counts) and index + 1 < len(ordered):
            following = ordered[index + 1]
            if not looks_like_speaker(following, counts):
                paired.append(f"{value}\n{following}")
                index += 2
                continue
        # Retain unpaired speech rather than silently losing a caption when a
        # Teams build omits or virtualizes its speaker label.
        if not looks_like_speaker(value, counts):
            paired.append(value)
        index += 1
    return paired


def caption_strings(
    window: object,
    positional_fallback: bool = False,
    nodes: list[tuple[object, int, tuple[str, ...]]] | None = None,
) -> list[str]:
    """Read text under caption-labelled UIA regions.

    The optional fallback examines text in the lower portion of the meeting
    window. It is disabled by default because it can include unrelated UI text.
    """
    nodes = nodes if nodes is not None else list(walk_controls(window))
    window_name = clean_text(str(safe_attr(window, "Name", "") or ""))
    if CAPTION_VIEWER_TITLE.search(window_name):
        return pinned_viewer_strings(window, nodes)
    marker_depths: list[tuple[int, tuple[str, ...], str]] = []
    for control, depth, ancestors in nodes:
        name = clean_text(str(safe_attr(control, "Name", "") or ""))
        if name and CAPTION_MARKER.search(name):
            marker_depths.append((depth, ancestors, name))

    result: list[tuple[tuple[float, float], str]] = []
    wrect = rect_tuple(window)
    for control, depth, ancestors in nodes:
        name = clean_text(str(safe_attr(control, "Name", "") or ""))
        if not name or CAPTION_MARKER.fullmatch(name) or NOISE.fullmatch(name):
            continue
        if re.match(r"^(hide|show) live captions?", name, re.I):
            continue
        ctype = str(safe_attr(control, "ControlTypeName", "") or "")
        if ctype not in {"TextControl", "DocumentControl", "GroupControl", "PaneControl"}:
            continue
        if bool(safe_attr(control, "IsOffscreen", False)):
            continue
        ancestry = " ".join(ancestors)
        in_caption_region = bool(CAPTION_MARKER.search(ancestry))
        rect = rect_tuple(control)
        if not in_caption_region and positional_fallback and rect and wrect:
            _, wtop, _, wbottom = wrect
            midpoint = (rect[1] + rect[3]) / 2
            in_caption_region = midpoint >= wtop + (wbottom - wtop) * 0.55
        if in_caption_region:
            position = (rect[1], rect[0]) if rect else (float(depth), 0.0)
            result.append((position, name))

    # Parent and child UIA controls can expose identical names.
    ordered: list[str] = []
    seen: set[str] = set()
    for _, value in sorted(result, key=lambda item: item[0]):
        key = normalized(value)
        if key and key not in seen:
            seen.add(key)
            ordered.append(value)
    return ordered


def authoritative_caption_sources(
    active_data: list[tuple[object, list[tuple[object, int, tuple[str, ...]]]]],
) -> list[tuple[object, list[tuple[object, int, tuple[str, ...]]]]]:
    """Prefer Teams' detached Captions viewer over duplicate meeting surfaces.

    Teams can expose the same rolling caption history in both the meeting
    window and its pinned viewer. Reading both causes lagging history to be
    replayed under a later speaker, so only one source family is authoritative.
    """
    viewers = [
        item
        for item in active_data
        if CAPTION_VIEWER_TITLE.search(clean_text(str(safe_attr(item[0], "Name", "") or "")))
    ]
    if viewers:
        # The caller scopes these to one selected meeting. Multiple viewers
        # still cannot be distinguished reliably, so do not combine them.
        return viewers if len(viewers) == 1 else []
    if not active_data:
        return []

    def caption_score(item) -> int:
        window, nodes = item
        title = clean_text(str(safe_attr(window, "Name", "") or ""))
        score = 10 if CAPTION_MARKER.search(title) else 0
        for control, _, ancestors in nodes:
            name = clean_text(str(safe_attr(control, "Name", "") or ""))
            if CAPTION_MARKER.search(name) or any(CAPTION_MARKER.search(parent) for parent in ancestors):
                score += 1
        return score

    return [max(active_data, key=caption_score)]


def meeting_is_held(window: object, nodes) -> bool:
    """Recognize explicit call status, not absence of speech or a quiet UI."""
    if not meeting_title_from_window(str(safe_attr(window, "Name", "") or "")):
        return False
    for control, _, _ in nodes:
        if bool(safe_attr(control, "IsOffscreen", False)):
            continue
        name = clean_text(str(safe_attr(control, "Name", "") or ""))
        if HOLD_STATUS.fullmatch(name):
            return True
        if (safe_attr(control, "ControlTypeName", "") == "ButtonControl"
                and normalized(name) in {"resume", "resume call", "resume meeting"}):
            return True
    return False


def is_active_meeting_window(
    window: object,
    nodes: list[tuple[object, int, tuple[str, ...]]] | None = None,
) -> bool:
    """Identify a joined call without trusting Teams' always-open main window."""
    window_name = clean_text(str(safe_attr(window, "Name", "") or ""))
    if CAPTION_VIEWER_TITLE.search(window_name):
        return True
    nodes = nodes if nodes is not None else list(walk_controls(window))
    names = {
        normalized(str(safe_attr(control, "Name", "") or ""))
        for control, _, _ in nodes
    }
    if "meeting controls" in names or any(name.startswith("elapsed time ") for name in names):
        return True
    if (meeting_title_from_window(window_name) or "").casefold() in COMPACT_TITLES:
        buttons = {normalized(str(safe_attr(c, "Name", "") or "")) for c, _, _ in nodes
                   if safe_attr(c, "ControlTypeName", "") == "ButtonControl"}
        end_call = any(re.match(r"^(leave|hang up|end call)(\b|$)", name) for name in buttons)
        media = any(re.match(r"^(mute|unmute|microphone|camera|turn on camera|turn off camera)(\b|$)", name)
                    for name in buttons)
        return end_call and media
    return False


def meeting_identity(window: object) -> WindowIdentity:
    name = clean_text(str(safe_attr(window, "Name", "") or ""))
    return WindowIdentity(int(safe_attr(window, "NativeWindowHandle", 0) or 0),
                          int(safe_attr(window, "ProcessId", 0) or 0),
                          name.casefold(), (meeting_title_from_window(name) or "").casefold())


def known_meeting_window_open(identity: WindowIdentity) -> bool:
    exists, name = native_window_name(identity)
    if not exists:
        return False
    if name is None:
        return True  # Existing but unreadable is unknown, never confirmed departure.
    current = WindowIdentity(identity.hwnd, identity.pid, clean_text(name).casefold(),
                             (meeting_title_from_window(name) or "").casefold())
    return identity.matches(current)


def safe_filename_component(value: str, max_length: int = 80) -> str:
    """Turn a meeting title into a readable Windows-safe filename component."""
    value = unicodedata.normalize("NFKC", clean_text(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", "_", value).strip(" ._")
    return value[:max_length].rstrip(" ._") or "Meeting"


def session_destination(
    requested: Path | None,
    started: datetime,
    session_number: int,
    meeting_title: str = "Meeting",
    transcript_dir: Path | None = None,
) -> Path:
    """Avoid overwriting a requested filename when the watcher sees many calls."""
    if requested is None:
        safe_title = safe_filename_component(meeting_title)
        folder = transcript_dir or application_dir() / "transcripts"
        candidate = folder / f"{safe_title}-{started:%Y%m%d-%H%M%S}.md"
    elif session_number == 1:
        candidate = requested
    else:
        candidate = requested.with_name(f"{requested.stem}-{started:%Y%m%d-%H%M%S}{requested.suffix}")
    destination = candidate
    suffix = 2
    while destination.exists():
        destination = candidate.with_name(f"{candidate.stem}-{suffix}{candidate.suffix}")
        suffix += 1
    return destination


def render_markdown(transcript: Transcript, meeting_title: str, started: str) -> str:
    lines = [
        f"# Teams meeting transcript — {meeting_title}",
        "",
        f"- Capture started: {started}",
        f"- Last updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "- Source: Microsoft Teams live captions (Windows UI Automation)",
        "",
        "## Transcript",
        "",
    ]
    for entry in transcript.entries:
        clock = datetime.fromisoformat(entry.timestamp).strftime("%H:%M:%S")
        who = f" **{entry.speaker}:**" if entry.speaker else ""
        lines.append(f"- `{clock}`{who} {entry.text}")
    lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, content: str, attempts: int = 5, retry_delay: float = 0.15) -> None:
    """Atomically replace a file, tolerating short-lived Windows/OneDrive locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_delay * (attempt + 1))
    assert last_error is not None
    raise last_error


def recovery_destinations(path: Path) -> list[Path]:
    """Return writable alternatives when the intended transcript is locked."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    recovered_name = f"{path.stem}-recovered-{stamp}{path.suffix}"
    local_root = Path(os.environ.get("LOCALAPPDATA", str(application_dir())))
    return [
        path.with_name(recovered_name),
        local_root / "Teams Caption Notes" / "transcripts" / recovered_name,
    ]


def diagnostic_dump(auto_module: object, destination: Path) -> int:
    payload = []
    for window in find_teams_windows(auto_module):
        item = {"window": safe_attr(window, "Name", ""), "process_id": safe_attr(window, "ProcessId", 0), "controls": []}
        for control, depth, ancestors in walk_controls(window):
            name = clean_text(str(safe_attr(control, "Name", "") or ""))
            if name:
                item["controls"].append({
                    "depth": depth,
                    "type": safe_attr(control, "ControlTypeName", ""),
                    "name": name,
                    "ancestors": list(ancestors),
                    "rectangle": rect_tuple(control),
                })
        payload.append(item)
    atomic_write(destination, json.dumps(payload, ensure_ascii=False, indent=2))
    return len(payload)


def import_uiautomation():
    if os.name != "nt":
        raise RuntimeError("This program requires Windows.")
    try:
        import uiautomation as auto  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Missing dependency. Run: python -m pip install -r requirements.txt") from exc
    return auto


def run(
    args: argparse.Namespace,
    stop_event: threading.Event | None = None,
    event_callback=None,
    transcript_saved_callback=None,
) -> int:
    last_write_warning = 0.0

    def emit(event: str, message: str) -> None:
        if sys.stdout is not None:
            print(message)
        if event_callback is not None:
            event_callback(event, message)

    def persist_transcript(final: bool = False) -> bool:
        """Write current state without ever terminating caption capture."""
        nonlocal destination, last_write_warning, last_markdown_write, markdown_dirty
        if transcript is None or destination is None:
            return True
        journal_ok = True
        if journal is not None:
            try:
                journal.sync(transcript.entries)
            except OSError as exc:
                journal_ok = False
                if time.monotonic() - last_write_warning >= 30:
                    emit("write_blocked", f"Recovery journal could not be written; retrying Markdown save: {exc}")
                    last_write_warning = time.monotonic()
        if not final and journal_ok and (not markdown_dirty or time.monotonic() - last_markdown_write < 10):
            return True
        content = render_markdown(transcript, title, started)
        try:
            atomic_write(destination, content)
            last_markdown_write = time.monotonic()
            markdown_dirty = False
            return True
        except OSError as exc:
            LOG.warning("Transcript write blocked for %s: %s", destination, exc)
            if not final:
                now = time.monotonic()
                if now - last_write_warning >= 30:
                    emit(
                        "write_blocked",
                        f"Transcript file is locked; capture is continuing in memory and will retry: {destination}",
                    )
                    last_write_warning = now
                return False
            for recovery in recovery_destinations(destination):
                try:
                    atomic_write(recovery, content)
                    original = destination
                    destination = recovery
                    emit(
                        "write_recovered",
                        f"Original transcript was locked. Saved recovery copy to {recovery.resolve()} (original: {original})",
                    )
                    return True
                except OSError:
                    LOG.exception("Could not save recovery transcript to %s", recovery)
            emit(
                "write_blocked",
                f"Could not save the transcript yet; temporary data remains beside {destination}. See the log.",
            )
            return False

    auto = import_uiautomation()
    if args.diagnose:
        count = diagnostic_dump(auto, args.diagnose)
        emit("diagnostic", f"Wrote UI Automation data for {count} Teams window(s) to {args.diagnose}")
        return 0 if count else 2

    transcript: Transcript | None = None
    journal: CaptionJournal | None = None
    last_markdown_write = 0.0
    markdown_dirty = False
    destination: Path | None = None
    started = ""
    session_number = 0
    last_status = 0.0
    title = "Meeting"
    scan_failures = 0
    last_scan_warning = 0.0
    presence = MeetingPresence()
    selector = MeetingSelector()
    visibility_state = "active"
    emit("watching", "Watching Microsoft Teams. Capture starts when you join a meeting and stops when you leave.")
    emit("watching", "Keep Teams live captions enabled. Press Ctrl+C to stop the watcher.")

    def finish_meeting(reason):
        nonlocal transcript, journal, destination, title, presence, visibility_state
        if not persist_transcript(final=True):
            return False
        emit("meeting_ended", f"{reason}; saved {len(transcript.entries)} entries to {destination.resolve()}")
        if transcript_saved_callback is not None:
            transcript_saved_callback(destination, title)
        transcript, journal, destination = None, None, None
        title = "Meeting"
        presence = MeetingPresence()
        visibility_state = "active"
        return True

    try:
        while stop_event is None or not stop_event.is_set():
            try:
                windows = find_teams_windows(auto)
                window_data = [(window, [] if bool(safe_attr(window, "IsOffscreen", False))
                                else list(walk_controls(window))) for window in windows]
                active_data = [
                    (window, nodes)
                    for window, nodes in window_data
                    if not bool(safe_attr(window, "IsOffscreen", False))
                    and (is_active_meeting_window(window, nodes) or meeting_is_held(window, nodes))
                ]
            except ctypes.COMError:
                scan_failures += 1
                now = time.monotonic()
                # An unreadable desktop is unknown state, NOT a meeting departure.
                if transcript is not None:
                    presence.uncertain()
                    persist_transcript(final=True)
                selector.uncertain()
                delay = min(30.0, 2.0 ** min(scan_failures, 5))
                if scan_failures == 1 or now - last_scan_warning >= 60:
                    LOG.warning("UI Automation scan failed; retrying in %.0fs (attempt %d)",
                                delay, scan_failures, exc_info=True)
                    emit("scan_retrying", f"Windows caption scan unavailable; retrying automatically in {delay:.0f}s. See log.")
                    last_scan_warning = now
                if stop_event is not None:
                    if stop_event.wait(delay):
                        break
                else:
                    time.sleep(delay)
                continue
            now = time.monotonic()
            recovered_scan = bool(scan_failures)
            if scan_failures:
                # Start the leave grace period afresh after an outage.
                if transcript is not None:
                    presence.uncertain()
                emit("scan_recovered", f"Windows caption scan recovered after {scan_failures} failed attempt(s).")
                scan_failures = 0

            observed_identities = [meeting_identity(window) for window in windows]
            selector.observe_retired(observed_identities, known_meeting_window_open, now, args.leave_grace)
            surfaces = [MeetingSurface(meeting_identity(window),
                                       bool(CAPTION_VIEWER_TITLE.search(str(safe_attr(window, "Name", "") or ""))),
                                       meeting_is_held(window, nodes)) for window, nodes in active_data]
            selection = selector.select(surfaces, observed_identities)
            if transcript is not None and selection.changed:
                if not finish_meeting("A different meeting was selected; continuing in a separate transcript"):
                    if stop_event is not None:
                        stop_event.wait(args.interval)
                    else:
                        time.sleep(args.interval)
                    continue
                if args.exit_after_meeting:
                    return 0

            # Ownership changes only after the old transcript has been saved.
            selector.commit(selection)
            selected_ids = {surface.identity for surface in selection.surfaces}
            active_data = [(window, nodes) for window, nodes in active_data
                           if meeting_identity(window) in selected_ids]
            active_identities = [meeting_identity(window) for window, _ in active_data]
            if recovered_scan and active_data and transcript is not None and not selection.held:
                emit("recording", "Caption scanning resumed for the current meeting.")

            if active_data:
                if transcript is None:
                    session_number += 1
                    started_dt = datetime.now().astimezone()
                    started = started_dt.isoformat(timespec="seconds")
                    transcript = Transcript()
                    title_candidates = [
                        meeting_title_from_window(str(safe_attr(window, "Name", "") or ""))
                        for window, _ in active_data
                    ]
                    title = next((item for item in title_candidates if item), "Meeting")
                    destination = session_destination(args.output, started_dt, session_number, title)
                    from uuid import uuid4
                    journal = CaptionJournal(destination.with_name(f"{destination.stem}-{uuid4().hex[:8]}.captions.jsonl"), title, started)
                    markdown_dirty = True
                    last_status = now
                    persist_transcript(final=True)
                    emit("meeting_started", f"Meeting joined; capture started: {destination.resolve()}")

                presence.remember(active_identities)
                if visibility_state != "active" and not selection.held:
                    emit("meeting_visibility_restored", "Meeting controls visible again; continuing the same transcript.")
                visibility_state = "active" if not selection.held else visibility_state

            presence_state = "active"
            if selection.ambiguous or (transcript is not None and selection.held):
                presence.uncertain()
                state = "held" if selection.held else "ambiguous"
                if visibility_state != state:
                    emit("meeting_visibility_lost", "Meeting is on hold; caption capture paused."
                         if selection.held else "Multiple meeting surfaces are ambiguous; waiting without combining captions.")
                    visibility_state = state
            elif transcript is not None and not active_data:
                presence_state = presence.observe([meeting_identity(w) for w in windows],
                                                  known_meeting_window_open, now, args.leave_grace)
                if presence_state != visibility_state:
                    reason = ("Known meeting window still exists; waiting for caption controls without splitting the transcript."
                              if presence_state == "uncertain" else
                              f"Known meeting windows not found; confirming closure for {args.leave_grace:g}s.")
                    if presence_state != "ended":
                        emit("meeting_visibility_lost", reason)
                    visibility_state = presence_state

            changed = False
            # A toolbar can disappear while caption text remains accessible.
            caption_data = [] if selection.held or selection.ambiguous else active_data or [
                (window, nodes) for window, nodes in window_data
                if transcript is not None and not bool(safe_attr(window, "IsOffscreen", False))
                and selector.allow_caption(meeting_identity(window))
                and not meeting_is_held(window, nodes)
            ]
            for window, nodes in authoritative_caption_sources(caption_data):
                window_title = clean_text(str(safe_attr(window, "Name", "") or ""))
                candidate_title = meeting_title_from_window(window_title)
                if candidate_title and (title == "Meeting" or CAPTION_VIEWER_TITLE.search(window_title)):
                    title = candidate_title
                for value in caption_strings(window, args.allow_positional_fallback, nodes):
                    parsed = parse_caption(value)
                    if parsed and transcript is not None and transcript.ingest(*parsed):
                        changed = True
                        accepted = transcript.entries[-1]
                        speaker_prefix = f"{accepted.speaker}: " if accepted.speaker else ""
                        emit("caption", f"[{datetime.now():%H:%M:%S}] {speaker_prefix}{accepted.text}")
            if changed:
                markdown_dirty = True
            if transcript is not None and destination is not None:
                persist_transcript()

            if transcript is not None and presence_state == "ended":
                assert destination is not None
                if not finish_meeting(f"Meeting windows closed for at least {args.leave_grace:g}s"):
                    # Retain the in-memory meeting and retry instead of clearing
                    # it and losing captions after an unusually broad I/O failure.
                    if stop_event is not None:
                        stop_event.wait(args.interval)
                    else:
                        time.sleep(args.interval)
                    continue
                selector.end_session()
                last_status = now
                if args.exit_after_meeting:
                    return 0

            if not changed and args.status_interval and now - last_status >= args.status_interval:
                if selection.ambiguous or selection.held:
                    emit("meeting_visibility_lost", "Meeting is on hold; caption capture paused."
                         if selection.held else "Multiple meeting surfaces are ambiguous; waiting without combining captions.")
                elif transcript is not None:
                    if transcript.entries:
                        emit("recording", "No new captions since the last status check; capture is still running.")
                    else:
                        emit(
                            "recording",
                            "Meeting detected, but no live-caption text has appeared yet. "
                            "Confirm captions are enabled and the meeting window is not minimized."
                        )
                else:
                    emit("watching", "Waiting to join a Teams meeting.")
                last_status = now
            if stop_event is not None:
                if stop_event.wait(args.interval):
                    break
            else:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        emit("stopping", "Stopping watcher.")
    finally:
        if transcript is not None and destination is not None:
            if persist_transcript(final=True):
                emit("meeting_ended", f"Saved {len(transcript.entries)} transcript entries to {destination.resolve()}")
                if transcript_saved_callback is not None:
                    transcript_saved_callback(destination, title)
    emit("stopped", "Teams caption watcher stopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Markdown output path (default: transcripts/teams-TIMESTAMP.md)")
    parser.add_argument("--interval", type=float, default=0.75, help="Polling interval in seconds (default: 0.75)")
    parser.add_argument("--status-interval", type=float, default=15.0, metavar="SECONDS", help="Print waiting status this often; 0 disables (default: 15)")
    parser.add_argument("--leave-grace", type=float, default=8.0, metavar="SECONDS", help="Confirm the meeting is gone for this long before ending capture (default: 8)")
    parser.add_argument("--exit-after-meeting", action="store_true", help="Exit after saving the first completed meeting instead of waiting for another")
    parser.add_argument("--allow-positional-fallback", action="store_true", help="Also inspect lower meeting-window text when Teams exposes no caption region; may include unrelated UI text")
    parser.add_argument("--diagnose", type=Path, metavar="FILE.json", help="Write the Teams accessibility tree once, then exit")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval < 0.2:
        raise SystemExit("--interval must be at least 0.2 seconds")
    if args.leave_grace < 1:
        raise SystemExit("--leave-grace must be at least 1 second")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    if not args.verbose:
        logging.getLogger("comtypes").setLevel(logging.WARNING)
    try:
        return run(args)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
