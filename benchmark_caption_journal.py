"""Synthetic durability/performance check; no Teams or user data is accessed."""
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from caption_journal import CaptionJournal, recover_journal
from teams_caption_notes import Caption


def main():
    started = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
    # Four simulated hours, a finalized utterance every five seconds.
    entries = []
    with tempfile.TemporaryDirectory() as temp:
        journal = CaptionJournal(Path(temp) / "synthetic.jsonl", "Synthetic four-hour meeting", started.isoformat())
        before = time.perf_counter()
        for index in range(2880):
            entries.append(Caption((started + timedelta(seconds=index * 5)).isoformat(),
                                   "Synthetic Speaker", f"Sample utterance {index} with a decision and supporting context."))
            journal.sync(entries)
        elapsed = time.perf_counter() - before
        recovered = recover_journal(journal.path).read_text(encoding="utf-8")
        assert recovered.count("Synthetic Speaker") == 2880
        print(json.dumps({"simulated_hours": 4, "captions": len(entries),
                          "journal_bytes": journal.path.stat().st_size,
                          "write_seconds": round(elapsed, 3), "recovered_captions": 2880}))


if __name__ == "__main__":
    main()
