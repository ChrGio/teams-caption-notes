"""Synthetic screen-sharing regressions; no live Teams or user data access."""
import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import teams_caption_notes as capture
from test_teams_caption_notes import FakeControl


def surface(title, hwnd=100, *, visible=True, controls=True, speech=(), resume=False):
    children = [FakeControl("Meeting controls", "ToolBarControl")] if controls else []
    if resume:
        children.append(FakeControl("Resume", "ButtonControl"))
    if speech:
        children.append(FakeControl("Live captions", children=[
            FakeControl(line, "TextControl") for line in speech]))
    window = FakeControl(title, children=children)
    window.NativeWindowHandle = hwnd
    window.ProcessId = 42
    window.IsOffscreen = not visible
    return window


def sharing_bar(*, resume=False):
    bar = surface("Sharing control bar | Microsoft Teams", 200,
                  speech=["Alex: Toolbar text must not become meeting speech."], resume=resume)
    bar._children.append(FakeControl("Elapsed time 00:42", "TextControl"))
    return bar


class SharingToolbarTests(unittest.TestCase):
    def run_frames(self, frames):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        args = capture.build_parser().parse_args([])
        args.status_interval = 0
        stop = Mock()
        stop.is_set.side_effect = [False] * len(frames) + [True]
        stop.wait.return_value = False
        events, saved = Mock(), Mock()
        with patch.object(capture, "application_dir", return_value=folder), patch.object(
            capture, "import_uiautomation", return_value=Mock()
        ), patch.object(capture, "find_teams_windows", side_effect=frames), patch.object(
            capture, "known_meeting_window_open", return_value=False
        ), patch.object(capture.time, "monotonic", side_effect=itertools.count(0, 30)), patch(
            "builtins.print"
        ), patch.object(capture.LOG, "info"), patch.object(capture.LOG, "warning"):
            self.assertEqual(capture.run(args, stop_event=stop, event_callback=events,
                                         transcript_saved_callback=saved), 0)
        kinds = [call.args[0] for call in events.call_args_list]
        return folder / "transcripts", events, kinds, saved

    def test_join_sharing_compact_restore_and_close_keep_one_transcript(self):
        joined = surface("Meeting join | Microsoft Teams",
                         speech=["Captions will be shown in", "English (US)"])
        hidden = surface("Planning | Microsoft Teams", visible=False)
        hidden.GetChildren = Mock(side_effect=AssertionError("Do not traverse hidden meeting"))
        compact = surface("Meeting compact view | Microsoft Teams", 300, controls=False)
        restored = surface("Planning | Microsoft Teams", speech=[
            "Alex: We can review the first item.",
            "Alex: The second item is ready.",
            "Alex: Please check the final result.",
        ])
        home = surface("Chat | Microsoft Teams", 400, controls=False)
        for resume in (False, True):
            with self.subTest(resume_on_toolbar=resume):
                bar = sharing_bar(resume=resume)
                folder, events, kinds, saved = self.run_frames([
                    [joined], [joined, bar], [hidden, bar],
                    [compact, bar], [compact, bar], [restored, bar],
                    [home, bar], [home, bar], [home, bar],
                ])
                self.assertEqual(kinds.count("meeting_started"), 1)
                self.assertEqual(kinds.count("meeting_ended"), 1)
                saved.assert_called_once()
                files = list(folder.glob("*.md"))
                self.assertEqual(len(files), 1)
                self.assertTrue(files[0].name.startswith("Meeting-"), files[0].name)
                self.assertEqual(saved.call_args.args[1], "Planning")
                text = files[0].read_text(encoding="utf-8")
                self.assertIn("# Teams meeting transcript — Planning", text)
                for sentence in ("first item", "second item", "final result"):
                    self.assertIn(sentence, text)
                for forbidden in ("Sharing control bar", "Toolbar text", "Meeting join",
                                  "Captions will be shown", "English (US)"):
                    self.assertNotIn(forbidden, text)
                self.assertIn("captions_unavailable", kinds)
                self.assertEqual(kinds.count("caption"), 3)
                # Closure is confirmed before watcher shutdown, not merely its
                # finally block saving a falsely retained sharing toolbar.
                ended = [call.args[1] for call in events.call_args_list
                         if call.args[0] == "meeting_ended"]
                self.assertIn("Meeting windows closed", ended[0])
        hidden.GetChildren.assert_not_called()

    def test_sharing_toolbar_alone_never_starts_even_with_call_like_controls(self):
        for resume in (False, True):
            with self.subTest(resume_on_toolbar=resume):
                bar = sharing_bar(resume=resume)
                self.assertFalse(capture.is_active_meeting_window(bar))
                self.assertFalse(capture.meeting_is_held(bar, list(capture.walk_controls(bar))))
                folder, _, kinds, saved = self.run_frames([[bar], [bar], [bar]])
                self.assertNotIn("meeting_started", kinds)
                self.assertNotIn("caption", kinds)
                self.assertEqual(list(folder.glob("*.md")), [])
                saved.assert_not_called()

    def test_sharing_toolbar_is_not_a_caption_source_even_with_explicit_fallback(self):
        bar = sharing_bar()
        self.assertEqual(capture.caption_strings(bar), [])
        self.assertEqual(capture.caption_strings(bar, positional_fallback=True), [])

    def test_exact_toolbar_name_with_case_and_whitespace_is_excluded(self):
        for title in ("Sharing control bar", "SHARING CONTROL BAR | Microsoft Teams",
                      "  Sharing control bar  | Microsoft Teams"):
            with self.subTest(title=title):
                window = surface(title)
                self.assertIsNone(capture.meeting_title_from_window(title))
                self.assertFalse(capture.is_active_meeting_window(window))

    def test_real_meeting_name_containing_toolbar_phrase_is_not_excluded(self):
        title = "Sharing control bar planning"
        window = surface(f"{title} | Microsoft Teams", speech=["Alex: Ordinary meeting speech."])
        self.assertEqual(capture.meeting_title_from_window(window.Name), title)
        self.assertTrue(capture.is_active_meeting_window(window))
        folder, _, kinds, saved = self.run_frames([[window], [], []])
        self.assertEqual(kinds.count("meeting_started"), 1)
        saved.assert_called_once()
        self.assertIn("Ordinary meeting speech.", next(folder.glob("*.md")).read_text())

    def test_detached_viewer_meeting_named_sharing_control_bar_is_not_a_toolbar(self):
        viewer = surface("Captions | Sharing control bar | Microsoft Teams", 500,
                         controls=False, speech=["Alex: Keep actual spoken content."])
        self.assertEqual(capture.meeting_title_from_window(viewer.Name), "Sharing control bar")
        self.assertTrue(capture.is_active_meeting_window(viewer))
        self.assertEqual(capture.caption_strings(viewer), ["Alex: Keep actual spoken content."])

    def test_meeting_join_placeholder_is_canonical_generic_name(self):
        for title in ("Meeting join", "Meeting join | Microsoft Teams", "MEETING JOIN | Microsoft Teams"):
            with self.subTest(title=title):
                window = surface(title)
                folder, _, kinds, saved = self.run_frames([[window], [], []])
                self.assertEqual(kinds.count("meeting_started"), 1)
                saved.assert_called_once()
                self.assertEqual(saved.call_args.args[1], "Meeting")
                file = next(folder.glob("*.md"))
                self.assertTrue(file.name.startswith("Meeting-"), file.name)
                text = file.read_text(encoding="utf-8")
                self.assertEqual(text.splitlines()[0], "# Teams meeting transcript — Meeting")

    def test_meeting_join_needs_evidence_of_joined_call(self):
        join_screen = surface("Meeting join | Microsoft Teams", controls=False)
        join_screen._children.append(FakeControl("Join now", "ButtonControl"))
        self.assertFalse(capture.is_active_meeting_window(join_screen))
        _, _, kinds, saved = self.run_frames([[join_screen], [join_screen]])
        self.assertNotIn("meeting_started", kinds)
        saved.assert_not_called()

    def test_generic_join_title_promotes_heading_without_new_file_or_new_speech(self):
        joined = surface("Meeting join | Microsoft Teams", speech=["Alex: Speech before title loads."])
        named = surface("Planning | Microsoft Teams")
        folder, _, kinds, saved = self.run_frames([[joined], [named], [], []])
        self.assertEqual(kinds.count("meeting_started"), 1)
        saved.assert_called_once()
        self.assertEqual(saved.call_args.args[1], "Planning")
        file = next(folder.glob("*.md"))
        self.assertTrue(file.name.startswith("Meeting-"), file.name)
        text = file.read_text(encoding="utf-8")
        self.assertIn("# Teams meeting transcript — Planning", text)
        self.assertIn("Speech before title loads.", text)

    def test_join_phrase_as_part_of_real_meeting_name_is_preserved(self):
        title = "Meeting join troubleshooting"
        self.assertEqual(capture.meeting_title_from_window(title + " | Microsoft Teams"), title)

    def test_genuine_new_named_meeting_still_creates_separate_file(self):
        first = surface("Planning | Microsoft Teams", speech=["Alex: Planning speech only."])
        second = surface("Review | Microsoft Teams", 600, speech=["Alex: Review speech only."])
        folder, _, kinds, saved = self.run_frames([[first], [first, sharing_bar()], [second], [], []])
        self.assertEqual(kinds.count("meeting_started"), 2)
        self.assertEqual(saved.call_count, 2)
        self.assertEqual(len(list(folder.glob("*.md"))), 2)
        first_text, second_text = [call.args[0].read_text() for call in saved.call_args_list]
        self.assertIn("Planning speech only.", first_text)
        self.assertNotIn("Review speech only.", first_text)
        self.assertIn("Review speech only.", second_text)
        self.assertNotIn("Planning speech only.", second_text)
        self.assertNotIn("Toolbar text", first_text + second_text)


if __name__ == "__main__":
    unittest.main()
