import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import teams_caption_notes as capture
from test_teams_caption_notes import FakeControl


def meeting(name="Planning", visible=True, controls=True, hwnd=100):
    window = FakeControl(f"{name} | Microsoft Teams", children=(
        [FakeControl("Meeting controls", "ToolBarControl")] if controls else []))
    window.IsOffscreen = not visible
    window.NativeWindowHandle, window.ProcessId = hwnd, 42
    return window


class ContinuityTests(unittest.TestCase):
    def run_scans(self, scans, captions=None, single=True, native=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / "meeting.md"
        args = capture.build_parser().parse_args(["--output", str(output)] + (["--exit-after-meeting"] if single else []))
        events, saved = Mock(), Mock()
        stop = Mock()
        stop.is_set.side_effect = [False] * len(scans) + [True]
        stop.wait.return_value = False
        with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
            capture, "find_teams_windows", side_effect=scans
        ), patch.object(capture, "caption_strings", side_effect=captions or itertools.repeat([])), patch.object(
            capture.time, "monotonic", side_effect=itertools.count(0, 30)
        ), patch.object(capture, "known_meeting_window_open", side_effect=native or (lambda _: False)), patch("builtins.print"):
            capture.run(args, stop_event=stop, event_callback=events, transcript_saved_callback=saved)
        return output, [c.args[0] for c in events.call_args_list], saved

    def test_minimized_for_minutes_then_restored_stays_one_transcript(self):
        visible, hidden = meeting(), meeting(visible=False, controls=False)
        out, events, saved = self.run_scans([[visible]] + [[hidden]] * 12 + [[visible], [], []],
                                           [["Ada: Before sharing"], ["Grace: After sharing"]])
        self.assertEqual(events.count("meeting_started"), 1)
        self.assertEqual(events.count("meeting_ended"), 1)
        self.assertIn("meeting_visibility_lost", events)
        self.assertIn("meeting_visibility_restored", events)
        self.assertIn("Before sharing", out.read_text())
        self.assertIn("After sharing", out.read_text())
        saved.assert_called_once()

    def test_empty_and_failed_subtrees_do_not_end_existing_meeting(self):
        normal, empty, broken = meeting(), meeting(controls=False), meeting(controls=False)
        broken.GetChildren = Mock(side_effect=OSError("UIA provider unavailable"))
        _, events, saved = self.run_scans([[normal], [empty], [broken], [normal], [], []])
        self.assertEqual(events.count("meeting_started"), 1)
        self.assertEqual(events.count("meeting_ended"), 1)
        saved.assert_called_once()

    def test_native_handle_can_keep_session_alive_when_uia_omits_window(self):
        _, events, _ = self.run_scans([[meeting()], [], [], [meeting()], [], []],
                                     native=Mock(side_effect=[True, True, False, False]))
        self.assertEqual(events.count("meeting_started"), 1)
        self.assertIn("meeting_visibility_restored", events)

    def test_actual_close_ends_even_when_main_teams_remains(self):
        main = meeting(name="Chat", controls=False, hwnd=101)
        _, events, saved = self.run_scans([[meeting()], [main], [main]])
        self.assertEqual(events.count("meeting_ended"), 1)
        saved.assert_called_once()

    def test_different_meeting_reusing_window_does_not_merge_captions(self):
        out, events, saved = self.run_scans([[meeting()], [meeting(name="Budget")], [], []],
                                           [["Ada: Planning only"], ["Grace: Budget only"]], single=False)
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertEqual(events.count("meeting_ended"), 2)
        self.assertEqual(saved.call_count, 2)
        first = saved.call_args_list[0].args[0].read_text()
        second = saved.call_args_list[1].args[0].read_text()
        self.assertIn("Planning only", first)
        self.assertNotIn("Budget only", first)
        self.assertIn("Budget only", second)
        self.assertNotIn("Planning only", second)

    def test_window_recreation_within_grace_retains_current_session(self):
        # Adjacent positive scans represent a layout changing its HWND without disappearance.
        _, events, _ = self.run_scans([[meeting()], [meeting(hwnd=200)], [], []])
        self.assertEqual(events.count("meeting_started"), 1)

    def test_new_recurring_same_title_after_confirmed_exit_gets_new_file(self):
        _, events, saved = self.run_scans([[meeting()], [], [], [meeting(hwnd=200)], [], []], single=False)
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertEqual(len({c.args[0] for c in saved.call_args_list}), 2)

    def test_discovery_keeps_offscreen_windows_but_does_not_start_hidden_call(self):
        hidden = meeting(visible=False)
        hidden.GetChildren = Mock(side_effect=AssertionError("Do not traverse minimized UIA trees"))
        auto = Mock()
        auto.GetRootControl.return_value.GetChildren.return_value = [hidden]
        with patch.object(capture, "is_teams_window", return_value=True):
            self.assertEqual(capture.find_teams_windows(auto), [hidden])
        _, events, saved = self.run_scans([[hidden], [hidden]], single=False)
        self.assertNotIn("meeting_started", events)
        saved.assert_not_called()
        hidden.GetChildren.assert_not_called()

    def test_generic_main_window_is_not_meeting_continuity_evidence(self):
        identity = capture.meeting_identity(meeting(name="Microsoft Teams", controls=False))
        self.assertFalse(identity.matches(identity))

    def test_compact_window_needs_call_specific_controls(self):
        compact = meeting(name="Meeting compact view", controls=False)
        compact._children = [FakeControl("Leave", "ButtonControl"), FakeControl("Mute (Ctrl+Shift+M)", "ButtonControl")]
        self.assertTrue(capture.is_active_meeting_window(compact))
        compact._children.pop()
        self.assertFalse(capture.is_active_meeting_window(compact))
        _, events, _ = self.run_scans([[meeting()], [compact], [meeting()], [], []])
        self.assertEqual(events.count("meeting_started"), 1)


if __name__ == "__main__":
    unittest.main()
