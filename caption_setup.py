"""Conservative, user-initiated Teams caption visibility setup through UIA.

The caller owns COM/UIA thread initialization. This module never clicks screen
coordinates, sends keys, focuses windows, starts recording/transcription, or
changes speaker identification, profanity filtering, or spoken language.
Selectors are deliberately exact and fail closed when Teams exposes a different
accessibility tree. Individual Windows COM calls cannot be forcibly timed out;
callers should keep this work off the GUI thread and provide cancellation.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time


PERSISTENT_LABELS = frozenset({
    "always show captions in my calls and meetings", "always show captions in my meetings",
})
MANUAL_ALWAYS = (
    "Open Teams > Settings and more > Settings > Accessibility. Turn on "
    "'Always show captions in my calls and meetings'. Speaker identification "
    "and profanity settings do not need to change."
)
MANUAL_CURRENT = (
    "In your joined Teams meeting, choose More > Language and speech > Show live captions. "
    "This does not start recording or transcription."
)
_BUTTONS = frozenset({"ButtonControl", "MenuItemControl", "SplitButtonControl"})
_NAVIGATION = _BUTTONS | {"TabItemControl", "ListItemControl", "TreeItemControl"}
_TOGGLES = frozenset({"CheckBoxControl", "ButtonControl", "CustomControl"})
_LEAVE = frozenset({"leave", "leave call", "leave meeting", "hang up"})
_MIC = frozenset({"mic", "mic (ctrl+shift+m)", "microphone", "microphone (ctrl+shift+m)",
                  "mute", "unmute", "mute (ctrl+shift+m)", "unmute (ctrl+shift+m)"})
_SHOW = frozenset({"show live captions", "show live captions (alt+shift+c)"})
_HIDE = frozenset({"hide live captions", "hide live captions (alt+shift+c)"})
_MORE = frozenset({"more actions", "more"})
_MORE_SETTINGS = frozenset({"settings and more", "settings and more...", "settings and more…"})
_LANGUAGE = frozenset({"language and speech"})
_INVOKE, _EXPAND, _SELECTION, _TOGGLE = 10000, 10005, 10010, 10015
_MAX_NODES, _MAX_DEPTH, _MAX_WINDOWS = 5000, 28, 100
_SECONDS = 20


@dataclass(frozen=True)
class SetupResult:
    state: str
    message: str
    verified: bool


@dataclass(frozen=True)
class _Window:
    key: tuple
    pid: int
    nodes: tuple


class _Unavailable(RuntimeError):
    pass


class _Cancelled(RuntimeError):
    pass


def process_image_name(pid: int) -> str:
    """Verify the executable image; a window title alone is never trusted."""
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return ""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return Path(image.value).name.casefold()
        return ""
    finally:
        kernel.CloseHandle(handle)


def _attr(control, name, default=None):
    try:
        return getattr(control, name, default)
    except Exception:
        return default


def _name(control) -> str:
    value = _attr(control, "Name", "")
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _visible(control) -> bool:
    return _attr(control, "IsOffscreen") is False


def _enabled(control) -> bool:
    return _visible(control) and _attr(control, "IsEnabled") is True


def _pattern(control, pattern_id):
    # GetPattern is on the base Control and returns a fresh supported pattern.
    try:
        return control.GetPattern(pattern_id)
    except Exception:
        return None


def _runtime(control) -> tuple:
    try:
        value = control.GetRuntimeId()
        if value and all(type(part) is int for part in value):
            return tuple(value)
    except Exception:
        pass
    raise _Unavailable("Control identity unavailable")


def _matches(control, names, types) -> bool:
    return _visible(control) and _name(control) in names and _attr(control, "ControlTypeName") in types


def _selected(control) -> bool:
    pattern = _pattern(control, _SELECTION)
    return pattern is not None and _attr(pattern, "IsSelected") is True


def _accessibility_page(window: _Window) -> bool:
    return any(_matches(node, {"accessibility"}, _NAVIGATION) and _selected(node)
               for node in window.nodes)


def _settings_page(window: _Window) -> bool:
    return (any(_matches(node, {"accessibility"}, _NAVIGATION) for node in window.nodes)
            and any(_matches(node, {"general"}, _NAVIGATION) for node in window.nodes))


def _joined(window: _Window) -> bool:
    return (any(_matches(node, _LEAVE, _BUTTONS) and _enabled(node) for node in window.nodes)
            and any(_matches(node, _MIC, _BUTTONS) and _enabled(node) for node in window.nodes))


class _Session:
    def __init__(self, auto, cancel):
        self.auto = auto
        self.cancel = cancel
        self.deadline = time.monotonic() + _SECONDS
        self.target_mutated = False

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise _Cancelled()
        if time.monotonic() >= self.deadline:
            raise _Unavailable("Setup time budget exhausted")

    def children(self, node, limit):
        self.check()
        result = []
        # GetChildren internally enumerates every sibling, so use its underlying
        # public sibling methods to make enumeration itself bounded/cancellable.
        child = node.GetFirstChildControl()
        while child is not None:
            self.check()
            if len(result) >= limit:
                raise _Unavailable("Accessibility tree exceeded its bounded scan")
            result.append(child)
            child = child.GetNextSiblingControl()
        return result

    def walk(self, root):
        pending = [(root, 0)]
        nodes = []
        seen = set()
        while pending:
            self.check()
            node, depth = pending.pop()
            identity = _runtime(node)
            if identity in seen:
                raise _Unavailable("Accessibility tree contains a cycle")
            seen.add(identity)
            nodes.append(node)
            if len(nodes) > _MAX_NODES:
                raise _Unavailable("Accessibility tree too large")
            if not _visible(node):
                continue
            children = self.children(node, _MAX_NODES - len(nodes) + 1)
            if children and depth >= _MAX_DEPTH:
                raise _Unavailable("Accessibility tree too deep")
            pending.extend((child, depth + 1) for child in reversed(children))
        return tuple(nodes)

    def windows(self, pid=None):
        self.check()
        roots = self.children(self.auto.GetRootControl(), _MAX_WINDOWS)
        windows = []
        for root in roots:
            self.check()
            process = _attr(root, "ProcessId", 0)
            if (not _visible(root) or type(process) is not int or process <= 0
                    or (pid is not None and process != pid)
                    or process_image_name(process) not in {"ms-teams.exe", "teams.exe"}):
                continue
            key = (process, _attr(root, "NativeWindowHandle", 0), _runtime(root))
            windows.append(_Window(key, process, self.walk(root)))
        return windows

    def fresh(self, window):
        matches = [current for current in self.windows(window.pid) if current.key == window.key]
        if len(matches) != 1:
            raise _Unavailable("The verified Teams window changed")
        return matches[0]

    def labelled(self, node):
        if _name(node) in PERSISTENT_LABELS:
            return True
        label = _attr(node, "LabeledBy")
        if label is None:
            element = _attr(node, "Element")
            label_element = _attr(element, "CurrentLabeledBy") if element is not None else None
            if label_element is not None:
                try:
                    label = self.auto.Control.CreateControlFromElement(label_element)
                except Exception:
                    return False
        return label is not None and _name(label) in PERSISTENT_LABELS

    def caption_toggles(self, window):
        return [node for node in window.nodes if _visible(node)
                and _attr(node, "ControlTypeName") in _TOGGLES and self.labelled(node)]

    def action(self, window, node, names, types, *, selection=False, joined=False, toggle=False):
        """Re-resolve root ownership, scope and the exact control before mutation."""
        self.check()
        fresh = self.fresh(window)
        if joined and not _joined(fresh):
            raise _Unavailable("Meeting is no longer confirmed joined")
        if toggle:
            if not _accessibility_page(fresh):
                raise _Unavailable("Accessibility page no longer confirmed")
            matches = self.caption_toggles(fresh)
        else:
            matches = [item for item in fresh.nodes if _matches(item, names, types)]
        if len(matches) != 1 or _runtime(matches[0]) != _runtime(node) or not _enabled(matches[0]):
            raise _Unavailable("Action is ambiguous, unavailable or changed")
        target = matches[0]
        if toggle:
            pattern = _pattern(target, _TOGGLE)
            if pattern is None or _attr(pattern, "ToggleState") != 0:
                raise _Unavailable("Caption toggle is not confirmed off")
            operation = pattern.Toggle
        else:
            pattern = _pattern(target, _SELECTION) if selection else None
            if pattern is not None:
                operation = pattern.Select
            else:
                # Only explicit navigation menus may be expanded. An Invoke on
                # an already-open menu could close it, so prefer its stateful
                # Expand pattern when available. Never collapse any control.
                navigation_names = _MORE_SETTINGS | _MORE | _LANGUAGE | {"settings"}
                expansion = _pattern(target, _EXPAND) if names and set(names) <= navigation_names else None
                if expansion is not None:
                    state = _attr(expansion, "ExpandCollapseState")
                    if state == 1:  # Expanded: leave it open and inspect fresh children.
                        self.pause()
                        return
                    if state != 0:  # Collapsed only; unknown/partial/leaf states fail closed.
                        raise _Unavailable("Navigation menu expansion state is unknown")
                    operation = expansion.Expand
                else:
                    pattern = _pattern(target, _INVOKE)
                    if pattern is None:
                        raise _Unavailable("Safe semantic action unavailable")
                    operation = pattern.Invoke
        self.check()
        if toggle or names == _SHOW:
            # Once invoked, do not retry a toggle even if the COM result is lost.
            self.target_mutated = True
        if operation(waitTime=0) is False:
            raise _Unavailable("The semantic action did not report success")
        self.pause()

    def pause(self):
        self.check()
        if self.cancel is not None:
            if self.cancel.wait(0.2):
                raise _Cancelled()
        else:
            time.sleep(0.2)
        self.check()


def _one(items):
    if len(items) != 1:
        raise _Unavailable("An unambiguous Teams target was not found")
    return items[0]


def _enable_toggle(session, window):
    current = session.fresh(window)
    if not _accessibility_page(current):
        raise _Unavailable("Accessibility page not selected")
    toggle = _one(session.caption_toggles(current))
    if not _enabled(toggle):
        raise _Unavailable("Caption setting is disabled")
    pattern = _pattern(toggle, _TOGGLE)
    state = _attr(pattern, "ToggleState") if pattern is not None else None
    if state == 1:
        return SetupResult("already_enabled", "Teams already has always-show captions turned on.", True)
    if state != 0:
        raise _Unavailable("The caption setting state could not be determined")
    session.action(current, toggle, PERSISTENT_LABELS, _TOGGLES, toggle=True)
    for _ in range(3):
        checked = session.fresh(current)
        if _accessibility_page(checked):
            candidate = _one(session.caption_toggles(checked))
            verified = _pattern(candidate, _TOGGLE)
            if verified is not None and _attr(verified, "ToggleState") == 1:
                return SetupResult("enabled", "Verified: Teams always-show captions is turned on.", True)
        session.pause()
    return SetupResult("unverified", "The caption setting was requested, but its new state could not be verified. " + MANUAL_ALWAYS, False)


def _always(session):
    pid = None
    completed = set()
    # At most three navigation actions: Settings and more, Settings, Accessibility.
    for _ in range(4):
        windows = session.windows(pid)
        direct = [window for window in windows if _accessibility_page(window)
                  and session.caption_toggles(window)]
        if direct:
            return _enable_toggle(session, _one(direct))
        settings = [window for window in windows if _settings_page(window)]
        if settings and "accessibility" not in completed:
            window = _one(settings)
            node = _one([node for node in window.nodes if _matches(node, {"accessibility"}, _NAVIGATION)])
            session.action(window, node, {"accessibility"}, _NAVIGATION, selection=True)
            completed.add("accessibility")
        elif "more" in completed and "settings" not in completed:
            menus = [(window, node) for window in windows for node in window.nodes
                     if _matches(node, {"settings"}, {"MenuItemControl"})]
            window, node = _one(menus)
            session.action(window, node, {"settings"}, {"MenuItemControl"})
            completed.add("settings")
        elif "more" not in completed:
            menus = [(window, node) for window in windows for node in window.nodes
                     if _matches(node, _MORE_SETTINGS, _BUTTONS) and not _joined(window)]
            window, node = _one(menus)
            session.action(window, node, _MORE_SETTINGS, _BUTTONS)
            completed.add("more")
        else:
            raise _Unavailable("The supported accessibility setting could not be found")
        pid = window.pid
    raise _Unavailable("Caption setup exceeded its navigation limit")


def _captions_visible(window):
    # A joined call plus the specific caption panel/Hide command is required.
    # A detached viewer alone never proves that the user is in a meeting.
    return any(_matches(node, _HIDE, _BUTTONS) and _enabled(node) for node in window.nodes) or any(
        _matches(node, {"live captions"}, {"PaneControl", "GroupControl"}) for node in window.nodes)


def _current(session):
    joined = [window for window in session.windows() if _joined(window)]
    window = _one(joined)
    completed = set()
    for _ in range(4):
        window = session.fresh(window)
        if not _joined(window):
            raise _Unavailable("A joined meeting is no longer visible")
        if _captions_visible(window):
            return SetupResult("already_enabled", "Live captions are already visible in the joined Teams meeting.", True)
        show = [node for node in window.nodes if _matches(node, _SHOW, _BUTTONS)]
        if show:
            node = _one(show)
            session.action(window, node, _SHOW, _BUTTONS, joined=True)
            for _ in range(3):
                verified = session.fresh(window)
                if _joined(verified) and _captions_visible(verified):
                    return SetupResult("enabled", "Verified: live captions are enabled in the current Teams meeting.", True)
                session.pause()
            return SetupResult("unverified", "Show live captions was requested, but caption visibility could not be verified. " + MANUAL_CURRENT, False)
        language = [node for node in window.nodes if _matches(node, _LANGUAGE, _BUTTONS)]
        if language and "language" not in completed:
            session.action(window, _one(language), _LANGUAGE, _BUTTONS, joined=True)
            completed.add("language")
        elif "more" not in completed:
            more = [node for node in window.nodes if _matches(node, _MORE, _BUTTONS)]
            session.action(window, _one(more), _MORE, _BUTTONS, joined=True)
            completed.add("more")
        else:
            raise _Unavailable("The supported live captions command was not found")
    raise _Unavailable("Current meeting caption setup exceeded its navigation limit")


def _run(auto, cancel, operation, worker, manual):
    session = _Session(auto, cancel)
    try:
        session.check()
        result = worker(session)
    except _Cancelled:
        result = SetupResult("cancelled", "Caption setup was cancelled. A preceding setup action may already have completed.", False)
    except Exception:
        if session.target_mutated:
            result = SetupResult("unverified", "A caption visibility change was requested but could not be verified. " + manual, False)
        else:
            result = SetupResult("unavailable", "Teams did not expose one unambiguous supported caption control. " + manual, False)
    # No control text, window titles, meeting names, or exception payloads are logged.
    logging.info("Caption setup operation=%s state=%s verified=%s", operation, result.state, result.verified)
    return result


def enable_always_show(auto, cancel: threading.Event | None = None) -> SetupResult:
    return _run(auto, cancel, "always_show", _always, MANUAL_ALWAYS)


def enable_current_meeting(auto, cancel: threading.Event | None = None) -> SetupResult:
    return _run(auto, cancel, "current_meeting", _current, MANUAL_CURRENT)
