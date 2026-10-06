"""Synthetic caption health checks; never touch a live Teams window."""
import itertools
import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import teams_caption_notes as capture
from test_meeting_continuity import meeting
from test_teams_caption_notes import FakeControl


def captioned(name="Planning", hwnd=100, lines=(), viewer=False):
    window = meeting(("Captions | " if viewer else "") + name, hwnd=hwnd)
    window._children.append(FakeControl("Live captions", children=[
        FakeControl(line, "TextControl") for line in lines]))
    return window


class CaptureAvailabilityTests(unittest.TestCase):
    def run_frames(self, frames):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name) / "meeting.md"
        args = capture.build_parser().parse_args(["--output", str(output)])
        args.status_interval = 0  # The tray disables console status polling.
        stop = Mock()
        stop.is_set.side_effect = [False] * len(frames) + [True]
        stop.wait.return_value = False
        events, saved = Mock(), Mock()
        with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
            capture, "find_teams_windows", side_effect=frames
        ), patch.object(capture, "known_meeting_window_open", return_value=False), patch.object(
            capture.time, "monotonic", side_effect=itertools.count(0, 30)
        ), patch("builtins.print"), patch.object(capture.LOG, "warning"), patch.object(capture.LOG, "info") as diagnostic:
            capture.run(args, stop_event=stop, event_callback=events, transcript_saved_callback=saved)
        return output, events, saved, diagnostic

    def test_empty_caption_setup_never_saved_as_speech_in_both_layouts(self):
        for viewer in (False, True):
            with self.subTest(viewer=viewer):
                window = captioned(lines=["Captions will be shown in", "English (US)"], viewer=viewer)
                self.assertEqual(capture.caption_strings(window), [])
                output, events, _, _ = self.run_frames([[window], [window], [], []])
                text = output.read_text(encoding="utf-8")
                self.assertNotIn("Captions will be shown", text)
                self.assertNotIn("English (US)", text)
                kinds = [call.args[0] for call in events.call_args_list]
                self.assertNotIn("caption", kinds)
                self.assertNotIn("captions_receiving", kinds)
                self.assertIn("captions_waiting", kinds)

    def test_combined_setup_prompt_is_not_a_caption(self):
        for value in ("Captions will be shown in", "Captions will be shown in\nEnglish (US)",
                      "Captions will be shown in English (US)"):
            self.assertIsNone(capture.parse_caption(value))

    def test_language_name_remains_valid_spoken_content(self):
        window = captioned(lines=["Example, Alex: English (US)"])
        self.assertEqual(capture.caption_strings(window), ["Example, Alex: English (US)"])
        self.assertEqual(capture.parse_caption("Example, Alex: English (US)"),
                         ("Example, Alex", "English (US)"))
        self.assertEqual(capture.caption_strings(captioned(lines=["English (US)"])), ["English (US)"])

    def test_toolbar_caption_command_is_not_an_available_caption_region(self):
        window = meeting()
        window._children.append(FakeControl("Show live captions", "ButtonControl"))
        self.assertFalse(capture.caption_region_visible(list(capture.walk_controls(window))))
        _, events, _, _ = self.run_frames([[window]])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertIn("captions_unavailable", kinds)
        self.assertNotIn("captions_receiving", kinds)

    def test_hidden_region_is_not_available(self):
        region = FakeControl("Live captions")
        region.IsOffscreen = True
        self.assertFalse(capture.caption_region_visible([(region, 1, ("Planning",))]))

    def test_capture_health_runs_in_tray_mode_and_restores_only_on_speech(self):
        before = captioned(lines=["Alex: Before sharing."])
        without_captions = meeting()
        waiting = captioned()
        after = captioned(lines=["Alex: After sharing."])
        output, events, saved, _ = self.run_frames([[before], [without_captions], [waiting], [after], [], []])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertEqual(kinds.count("meeting_started"), 1)
        self.assertEqual(kinds.count("captions_receiving"), 2)
        unavailable = kinds.index("captions_unavailable")
        waiting_index = kinds.index("captions_waiting", unavailable)
        receiving = kinds.index("captions_receiving", waiting_index)
        self.assertLess(unavailable, waiting_index)
        self.assertLess(waiting_index, receiving)
        self.assertIn("Before sharing.", output.read_text())
        self.assertIn("After sharing.", output.read_text())
        saved.assert_called_once()

    def test_no_new_caption_for_a_minute_is_not_reported_as_proven_failure(self):
        window = captioned(lines=["Alex: A single complete sentence."])
        _, events, _, _ = self.run_frames([[window]] * 5)
        messages = [call.args[1] for call in events.call_args_list if call.args[0] == "captions_waiting"]
        self.assertTrue(any("quiet" in message and "unavailable" in message for message in messages))
        self.assertEqual(sum(call.args[0] == "caption" for call in events.call_args_list), 1)

    def test_transition_diagnostic_has_ids_but_no_titles_or_speech(self):
        window = captioned("Private Project Title", lines=["Alex: Confidential sentence."])
        identity = capture.meeting_identity(window)
        shape, details = capture.scan_diagnostics([(window, list(capture.walk_controls(window)))],
                                                   {identity}, {identity}, {identity})
        serialized = json.dumps(details)
        self.assertEqual(len(shape), 1)
        self.assertIn('"hwnd": 100', serialized)
        self.assertIn('"pid": 42', serialized)
        self.assertNotIn("Private", serialized)
        self.assertNotIn("Confidential", serialized)
        self.assertNotIn("Alex", serialized)

    def test_new_compact_window_without_controls_preserves_file_until_original_returns(self):
        before = captioned(lines=["Alex: Before screen sharing."])
        compact = meeting("Meeting compact view", hwnd=200, controls=False)
        after = captioned(lines=["Alex: After screen sharing."])
        output, events, saved, _ = self.run_frames([[before]] + [[compact]] * 12 + [[after], [], []])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertEqual(kinds.count("meeting_started"), 1)
        self.assertEqual(kinds.count("meeting_ended"), 1)
        self.assertIn("Before screen sharing.", output.read_text())
        self.assertIn("After screen sharing.", output.read_text())
        saved.assert_called_once()
        unavailable = [call.args[1] for call in events.call_args_list if call.args[0] == "captions_unavailable"]
        self.assertTrue(any("compact view" in message for message in unavailable))

    def test_new_compact_with_owned_viewer_keeps_reading_only_that_viewer(self):
        before = captioned(lines=["Alex: Builtin old text."])
        viewer = captioned(hwnd=101, lines=["Alex: Viewer before sharing."], viewer=True)
        compact = captioned("Compact view", hwnd=200, lines=["Unknown: Do not capture this."])
        live_viewer = captioned(hwnd=101, lines=["Alex: Viewer during sharing."], viewer=True)
        output, events, saved, _ = self.run_frames([[before, viewer], [compact, live_viewer], [], []])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertEqual(kinds.count("meeting_started"), 1)
        text = output.read_text()
        self.assertIn("Viewer during sharing.", text)
        self.assertNotIn("Do not capture", text)
        self.assertNotIn("Builtin old", text)
        saved.assert_called_once()

    def test_different_meeting_during_compact_view_still_gets_separate_transcript(self):
        before = captioned(lines=["Alex: Planning only."])
        compact = meeting("Compact view", hwnd=200, controls=False)
        other = captioned("Review", hwnd=300, lines=["Alex: Review only."])
        _, events, saved, _ = self.run_frames([[before], [compact], [compact, other], [other], [], []])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertEqual(kinds.count("meeting_started"), 2)
        self.assertEqual(saved.call_count, 2)
        first, second = [call.args[0].read_text() for call in saved.call_args_list]
        self.assertIn("Planning only.", first)
        self.assertNotIn("Review only.", first)
        self.assertIn("Review only.", second)
        self.assertNotIn("Planning only.", second)

    def test_compact_closure_does_not_keep_session_open_on_teams_home(self):
        before = captioned(lines=["Alex: Keep this sentence."])
        compact = meeting("Compact view", hwnd=200, controls=False)
        home = meeting("Chat", hwnd=300, controls=False)
        _, events, saved, _ = self.run_frames([[before], [compact], [home], [home]])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertEqual(kinds.count("meeting_ended"), 1)
        saved.assert_called_once()

    def test_same_name_overlap_pauses_without_splitting_then_original_resumes(self):
        before = captioned(lines=["Alex: Original before transition."])
        duplicate = captioned(hwnd=200, lines=["Unknown: Ambiguous text must not be saved."])
        after = captioned(lines=["Alex: Original after transition."])
        output, events, saved, _ = self.run_frames([[before], [before, duplicate], [duplicate], [after], [], []])
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertEqual(kinds.count("meeting_started"), 1)
        self.assertIn("Original before transition.", output.read_text())
        self.assertIn("Original after transition.", output.read_text())
        self.assertNotIn("Ambiguous text", output.read_text())
        saved.assert_called_once()

    def test_scan_recovery_without_captions_reports_unavailable_not_receiving(self):
        before = captioned(lines=["Alex: Before outage."])
        error = ctypes.COMError(-2147220991, "synthetic provider error", None)
        _, events, _, _ = self.run_frames([[before], error, [meeting()], [meeting()], [], []])
        kinds = [call.args[0] for call in events.call_args_list]
        recovered = kinds.index("scan_recovered")
        self.assertIn("captions_unavailable", kinds[recovered:])
        self.assertNotIn("captions_receiving", kinds[recovered:])


if __name__ == "__main__":
    unittest.main()
