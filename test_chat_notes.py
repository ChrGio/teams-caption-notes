import json
import tempfile
import threading
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import teams_chat as chat
import notes_library as notes
from caption_journal import CaptionJournal, recover_journal
from teams_caption_notes import Transcript


def response(payload=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = payload
    return result


def message(ident="1", body="Hello team", **extra):
    return {"id": ident, "createdDateTime": "2026-09-11T16:00:00Z",
            "lastModifiedDateTime": "2026-09-11T16:00:00Z",
            "from": {"user": {"displayName": "Ada"}},
            "body": body if isinstance(body, dict) else {"contentType": "text", "content": body}, **extra}


class GraphTests(unittest.TestCase):
    def client(self, responses):
        session = Mock()
        session.get.side_effect = responses
        return chat.GraphClient(Mock(return_value="test-token"), session=session)

    def test_pagination_preserves_all_chats(self):
        client = self.client([response({"value": [{"id": "a", "topic": "A"}], "@odata.nextLink": chat.GRAPH + "/me/chats?next=1"}), response({"value": [{"id": "b", "topic": "B"}]})])
        self.assertEqual([c["id"] for c in client.chats()], ["a", "b"])

    def test_does_not_send_token_to_foreign_pagination_host(self):
        client = self.client([response({"value": [], "@odata.nextLink": "https://example.com/steal"})])
        with self.assertRaises(chat.ChatError):
            client.chats()
        self.assertEqual(client.session.get.call_count, 1)

    def test_throttle_retry(self):
        client = self.client([response(status=429, headers={"Retry-After": "0"}), response({"value": []})])
        self.assertEqual(client.chats(), [])
        self.assertEqual(client.session.get.call_count, 2)

    def test_unauthorized_refreshes_token_once(self):
        client = self.client([response(status=401), response({"value": []})])
        client.chats()
        client.get_token.assert_called_with(False)

    def test_permission_failure_is_explicit(self):
        with self.assertRaisesRegex(chat.ChatError, "403"):
            self.client([response(status=403)]).chats()

    def test_cancel_prevents_network_call(self):
        client = self.client([])
        client.cancel.set()
        with self.assertRaises(chat.Cancelled):
            client.chats()
        client.session.get.assert_not_called()

    def test_date_filter_stops_after_older_page(self):
        older = message("old", createdDateTime="2026-08-01T16:00:00Z")
        client = self.client([response({"value": [message(), older], "@odata.nextLink": chat.GRAPH + "/chats/a/messages?next=2"})])
        day = chat.parse_time(message()["createdDateTime"]).astimezone().date()
        self.assertEqual([m["id"] for m in client.messages("a", day, day)], ["1"])
        self.assertEqual(client.session.get.call_count, 1)

    def test_html_and_deletion(self):
        html = message(body={"contentType": "html", "content": "<p>Hello &amp; team</p><script>bad</script><p>Next</p>"})
        self.assertEqual(chat.message_text(html), "Hello & team\n\nNext")
        self.assertEqual(chat.message_text(message(deletedDateTime="2026-09-11T17:00:00Z")), "[Message deleted in Teams]")


class ExportTests(unittest.TestCase):
    def test_latest_edit_wins_and_dates_group_into_notes(self):
        client = Mock(cancel=threading.Event())
        client.messages.return_value = iter([message(), message(body="Corrected", lastModifiedDateTime="2026-09-11T17:00:00Z")])
        with tempfile.TemporaryDirectory() as temp:
            folder, complete = chat.export_chats(client, [{"id": "chat", "topic": "Project A"}], date(2026, 9, 11), date(2026, 9, 11), Path(temp) / "export")
            text = next(folder.glob("Chats-*.md")).read_text(encoding="utf-8")
            self.assertTrue(complete)
            self.assertEqual(text.count("Corrected"), 1)
            self.assertNotIn("Hello team", text)
            self.assertIn("Ada", text)

    def test_interrupted_download_saves_partial_messages(self):
        def messages(*args):
            yield message()
            raise chat.ChatError("network interrupted")
        client = Mock(cancel=threading.Event())
        client.messages.side_effect = messages
        with tempfile.TemporaryDirectory() as temp:
            folder, complete = chat.export_chats(client, [{"id": "chat", "topic": "Project A"}], date(2026, 9, 11), date(2026, 9, 11), Path(temp) / "export")
            self.assertFalse(complete)
            self.assertIn("PARTIAL", (folder / "COLLECTION.md").read_text())
            self.assertIn("Hello team", next(folder.glob("Chats-*.md")).read_text())


class NotesTests(unittest.TestCase):
    def test_splitting_is_lossless_including_large_single_line(self):
        source = ("Ω paragraph\n" * 300) + "x" * 20000
        parts = notes.split_text(source, 1000)
        self.assertEqual("".join(parts), source)
        self.assertTrue(all(len(part) <= 1000 for part in parts))

    def test_project_and_body_search_deduplicates_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "meeting.md"
            path.write_text("release Friday", encoding="utf-8")
            found, errors = notes.search_notes([temp, temp], "sample Friday", {str(path.resolve()): "Sample"})
            self.assertEqual(len(found), 1)
            self.assertFalse(errors)

    def test_bundle_records_all_sources_and_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "meeting.md"
            path.write_text("Ω" * 1500, encoding="utf-8")
            out = notes.prepare_bundle([path], Path(temp) / "prompts", limit=1000)
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["parts"], 2)
            self.assertEqual(len(list(out.glob("part-*.txt"))), 2)
            self.assertTrue((out / "combine-summaries.txt").exists())


class JournalTests(unittest.TestCase):
    def test_watcher_finalizes_markdown_and_journal(self):
        import itertools
        import teams_caption_notes as capture
        from test_teams_caption_notes import FakeControl
        active = FakeControl("Captions | Planning | Microsoft Teams")
        with tempfile.TemporaryDirectory() as temp:
            args = capture.build_parser().parse_args(["--output", str(Path(temp) / "meeting.md"), "--exit-after-meeting"])
            saved = Mock()
            with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
                capture, "find_teams_windows", side_effect=[[active], [], []]
            ), patch.object(capture, "caption_strings", return_value=["Ada: We should ship Friday"]), patch.object(
                capture.time, "monotonic", side_effect=itertools.count(0, 20)
            ), patch.object(capture.time, "sleep"), patch("builtins.print"):
                self.assertEqual(capture.run(args, transcript_saved_callback=saved), 0)
            self.assertIn("We should ship Friday", (Path(temp) / "meeting.md").read_text())
            journal = next(Path(temp).glob("*.jsonl"))
            self.assertIn("We should ship Friday", recover_journal(journal).read_text())
            saved.assert_called_once()

    def test_recovers_revised_tail_and_new_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = CaptionJournal(Path(temp) / "meeting.jsonl", "Planning", "2026-09-11T12:00:00+00:00")
            transcript = Transcript()
            transcript.ingest("Ada", "We should")
            journal.sync(transcript.entries)
            transcript.ingest("Ada", "We should ship Friday")
            transcript.ingest("Grace", "Agreed")
            journal.sync(transcript.entries)
            output = recover_journal(journal.path).read_text(encoding="utf-8")
            self.assertIn("We should ship Friday", output)
            self.assertIn("Grace", output)
            self.assertEqual(output.count("We should"), 1)

    def test_partial_last_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = CaptionJournal(Path(temp) / "meeting.jsonl", "Planning", "2026-09-11T12:00:00+00:00")
            transcript = Transcript()
            transcript.ingest("Ada", "saved phrase")
            journal.sync(transcript.entries)
            with journal.path.open("ab") as handle:
                handle.write(b'{"unfinished":')
            self.assertIn("saved phrase", recover_journal(journal.path).read_text())

    def test_no_duplicate_writes_when_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = CaptionJournal(Path(temp) / "meeting.jsonl", "Planning", "2026-09-11T12:00:00+00:00")
            journal.sync([])
            size = journal.path.stat().st_size
            journal.sync([])
            self.assertEqual(journal.path.stat().st_size, size)

    def test_write_failure_can_retry_initial_header(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = CaptionJournal(Path(temp) / "meeting.jsonl", "Planning", "2026-09-11T12:00:00+00:00")
            with patch("caption_journal.os.fsync", side_effect=OSError("temporarily locked")):
                with self.assertRaises(OSError):
                    journal.sync([])
            journal.sync([])
            self.assertTrue(recover_journal(journal.path).exists())


if __name__ == "__main__":
    unittest.main()
