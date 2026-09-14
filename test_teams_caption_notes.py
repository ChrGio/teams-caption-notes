import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teams_caption_notes import (
    Transcript,
    authoritative_caption_sources,
    atomic_write,
    caption_strings,
    is_active_meeting_window,
    meeting_title_from_window,
    parse_caption,
    render_markdown,
    recovery_destinations,
    safe_filename_component,
    session_destination,
)


class FakeRect:
    def __init__(self, left=0, top=0, right=100, bottom=100):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class FakeControl:
    def __init__(self, name, kind="GroupControl", children=(), rect=None):
        self.Name = name
        self.ControlTypeName = kind
        self.IsOffscreen = False
        self.BoundingRectangle = rect or FakeRect()
        self._children = list(children)

    def GetChildren(self):
        return self._children


class CaptionParsingTests(unittest.TestCase):
    def test_parses_colon_speaker(self):
        self.assertEqual(parse_caption("Ada Lovelace: Hello team"), ("Ada Lovelace", "Hello team"))

    def test_parses_multiline_speaker(self):
        self.assertEqual(parse_caption("Ada Lovelace\nHello team"), ("Ada Lovelace", "Hello team"))

    def test_does_not_treat_spoken_salutation_as_speaker(self):
        self.assertEqual(
            parse_caption("Hi, Jordan.\nThe sample report is ready."),
            (None, "Hi, Jordan. The sample report is ready."),
        )

    def test_ignores_control_noise(self):
        self.assertIsNone(parse_caption("Mute"))

    def test_extracts_caption_descendants_and_deduplicates_parent_text(self):
        leaf = FakeControl("Ada: Hello team", "TextControl")
        duplicate = FakeControl("Ada: Hello team", "TextControl")
        caption_region = FakeControl("Live captions", children=(leaf, duplicate))
        window = FakeControl("Planning | Microsoft Teams", children=(caption_region,))
        self.assertEqual(caption_strings(window), ["Ada: Hello team"])

    def test_ignores_hide_captions_command(self):
        command = FakeControl("Hide live captions (Alt+Shift+C)", "TextControl")
        caption_region = FakeControl("Live captions", children=(command,))
        window = FakeControl("Planning | Microsoft Teams", children=(caption_region,))
        self.assertEqual(caption_strings(window), [])

    def test_extracts_pinned_webview_speaker_rows(self):
        rows = (
            FakeControl("Example, Jamie", "TextControl", rect=FakeRect(200, 100, 400, 120)),
            FakeControl("The next item is ready for review.", "TextControl", rect=FakeRect(200, 121, 700, 145)),
            FakeControl("Example, Jamie", "TextControl", rect=FakeRect(200, 146, 400, 166)),
            FakeControl("Yeah.", "TextControl", rect=FakeRect(200, 167, 400, 190)),
        )
        caption_region = FakeControl("Live Captions", children=rows)
        header = FakeControl("Example Organization", "TextControl", rect=FakeRect(700, 5, 750, 25))
        document = FakeControl("", "DocumentControl", children=(header, caption_region))
        window = FakeControl(
            "Captions | Standup | Microsoft Teams",
            children=(document,),
        )
        self.assertEqual(
            caption_strings(window),
            ["Example, Jamie\nThe next item is ready for review.", "Example, Jamie\nYeah."],
        )

    def test_detects_meeting_controls_but_not_calendar(self):
        controls = FakeControl("Meeting controls", "ToolBarControl")
        meeting = FakeControl("Standup | Microsoft Teams", children=(controls,))
        calendar = FakeControl("Calendar | Microsoft Teams")
        self.assertTrue(is_active_meeting_window(meeting))
        self.assertFalse(is_active_meeting_window(calendar))

    def test_detached_caption_viewer_is_the_only_authoritative_source(self):
        meeting = FakeControl("Team standup | Microsoft Teams")
        viewer = FakeControl("Captions | Team standup | Microsoft Teams")
        sources = authoritative_caption_sources([(meeting, []), (viewer, [])])
        self.assertEqual([source[0] for source in sources], [viewer])


class TranscriptTests(unittest.TestCase):
    def test_merges_growing_caption(self):
        transcript = Transcript()
        self.assertTrue(transcript.ingest("Ada", "We should"))
        self.assertTrue(transcript.ingest("Ada", "We should ship Friday"))
        self.assertEqual(len(transcript.entries), 1)
        self.assertEqual(transcript.entries[0].text, "We should ship Friday")

    def test_deduplicates_visible_history(self):
        transcript = Transcript()
        transcript.ingest("Ada", "Done")
        self.assertFalse(transcript.ingest("Ada", "Done"))

    def test_deduplicates_substantial_utterance_replayed_under_next_speaker(self):
        transcript = Transcript()
        text = "We should release the updated application tomorrow."
        self.assertTrue(transcript.ingest("Ada", text))
        self.assertFalse(transcript.ingest("Grace", text))
        self.assertEqual(len(transcript.entries), 1)

    def test_atomic_markdown_output(self):
        transcript = Transcript()
        transcript.ingest("Ada", "Decision made", "2026-09-08T09:30:00-04:00")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "note.md"
            atomic_write(target, render_markdown(transcript, "Planning", "2026-09-08T09:00:00-04:00"))
            text = target.read_text(encoding="utf-8")
        self.assertIn("**Ada:** Decision made", text)

    def test_atomic_write_retries_a_locked_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "note.md"
            real_replace = __import__("os").replace
            calls = 0

            def locked_once(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("file is open")
                return real_replace(source, destination)

            with patch("teams_caption_notes.os.replace", side_effect=locked_once):
                atomic_write(target, "saved", attempts=2, retry_delay=0)
            self.assertEqual(target.read_text(encoding="utf-8"), "saved")

    def test_recovery_filename_is_distinct(self):
        original = Path("transcripts/Standup.md")
        alternatives = recovery_destinations(original)
        self.assertIn("-recovered-", alternatives[0].name)
        self.assertNotEqual(alternatives[0], original)

    def test_empty_transcript_is_valid_markdown(self):
        text = render_markdown(Transcript(), "Meeting", "2026-09-08T09:00:00-04:00")
        self.assertIn("## Transcript", text)

    def test_standalone_speaker_label_applies_to_later_utterances(self):
        transcript = Transcript()
        self.assertFalse(transcript.ingest(None, "Example, Alex (Contr)"))
        self.assertTrue(transcript.ingest(None, "Testing."))
        self.assertEqual(transcript.entries[0].speaker, "Example, Alex (Contr)")

    def test_spoken_comma_phrase_is_not_a_speaker(self):
        transcript = Transcript()
        self.assertTrue(transcript.ingest(None, "Testing, testing"))
        self.assertIsNone(transcript.entries[0].speaker)

    def test_caption_window_supplies_meeting_title(self):
        self.assertEqual(
            meeting_title_from_window("Captions | Team standup | Example Organization | user@example.com | Microsoft Teams"),
            "Team standup",
        )
        self.assertIsNone(meeting_title_from_window("Calendar | Example Organization | user@example.com | Microsoft Teams"))

    def test_requested_output_is_not_overwritten_by_later_meeting(self):
        started = __import__("datetime").datetime(2026, 9, 8, 12, 30, 0)
        requested = Path("weekly.md")
        self.assertEqual(session_destination(requested, started, 1), requested)
        self.assertEqual(session_destination(requested, started, 2), Path("weekly-20260908-123000.md"))

    def test_default_output_contains_safe_meeting_name(self):
        started = __import__("datetime").datetime(2026, 9, 8, 12, 30, 0)
        self.assertEqual(
            session_destination(None, started, 1, 'Team: Standup / Operations?', Path("transcripts")),
            Path("transcripts/Team_Standup_Operations-20260908-123000.md"),
        )
        self.assertEqual(safe_filename_component("  <>  "), "Meeting")


if __name__ == "__main__":
    unittest.main()
