"""Append-only caption updates with explicit recovery to a new Markdown file."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4


class CaptionJournal:
    def __init__(self, path: Path, title: str, started: str):
        self.path, self.title, self.started = path, title, started
        self.count = 0
        self.tail = None
        self.initialized = False
        self.created = False

    def sync(self, entries):
        tail = entries[-1] if entries else None
        if self.initialized and len(entries) == self.count and tail == self.tail:
            return
        # Capture currently only appends or revises the last entry. Include the
        # previously saved tail to retain revisions followed by new entries.
        start = max(0, self.count - 1)
        record = {"start_index": start, "entries": [asdict(e) for e in entries[start:]]}
        lines = []
        if not self.initialized:
            lines.append({"version": 1, "title": self.title, "started": self.started})
        lines.append(record)
        payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in lines).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # New journals must not append to a file left by another recording.
        mode = "a+b" if self.created else "x+b"
        with self.path.open(mode) as handle:
            self.created = True
            before = handle.tell()
            try:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                handle.truncate(before)
                raise
        self.initialized = True
        self.count, self.tail = len(entries), tail


def recover_journal(path: Path) -> Path:
    import teams_caption_notes as capture

    transcript = capture.Transcript()
    header = None
    with path.open("rb") as handle:
        for line in handle:
            # A killed process may leave a partially written final record.
            if not line.endswith(b"\n"):
                break
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("The journal contains a damaged complete record; recovery was not saved.") from exc
            if header is None:
                if item.get("version") != 1 or not item.get("started"):
                    raise ValueError("This is not a supported caption journal.")
                header = item
                continue
            start = item["start_index"]
            if not isinstance(start, int) or not 0 <= start <= len(transcript.entries):
                raise ValueError("Journal entries are out of sequence.")
            transcript.entries[start:] = [capture.Caption(**entry) for entry in item["entries"]]
    if header is None:
        raise ValueError("The journal has no complete header.")
    output = path.with_name(f"{path.stem}-recovered-{uuid4().hex[:8]}.md")
    content = capture.render_markdown(transcript, header["title"], header["started"])
    content += "\nRecovered from the last complete journal record; the newest unfinished update may be missing.\n"
    capture.atomic_write(output, content)
    return output
