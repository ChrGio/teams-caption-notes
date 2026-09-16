import ctypes
import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import teams_caption_notes as capture
import tray_settings
from teams_caption_tray import TrayApp
from test_teams_caption_notes import FakeControl


class QuietTrayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.folder_patch = patch("teams_caption_tray.capture.application_dir", return_value=self.base)
        self.folder_patch.start()
        self.addCleanup(self.folder_patch.stop)
        self.icon_patch = patch("teams_caption_tray.pystray.Icon")
        self.icon_patch.start()
        self.addCleanup(self.icon_patch.stop)

    def test_default_suppresses_start_save_and_error_balloons_but_keeps_colors(self):
        app = TrayApp()
        self.assertTrue(app.popups_hidden())
        app.capture_event("meeting_started", "test")
        self.assertEqual(app._state, "recording")
        app.capture_event("meeting_ended", "test")
        self.assertEqual(app._state, "watching")
        app.capture_event("scan_retrying", "test")
        self.assertEqual(app._state, "error")
        app.notify("error", "test")
        app.icon.notify.assert_not_called()
        app.capture_event("scan_recovered", "test")
        self.assertEqual(app._state, "watching")

    def test_toggle_persists_and_dismisses_existing_notification(self):
        app = TrayApp()
        app.toggle_popups()
        self.assertFalse(app.popups_hidden())
        self.assertFalse(TrayApp().popups_hidden())
        app.notify("test title", "test message")
        app.icon.notify.assert_called_once_with("test message", "test title")
        app.toggle_popups()
        app.icon.remove_notification.assert_called_once()
        self.assertTrue(TrayApp().popups_hidden())

    def test_held_and_ambiguous_status_keeps_capture_owned_and_quiet(self):
        app = TrayApp()
        app.capture_event("meeting_started", "test")
        for message in (
            "Meeting is on hold; caption capture paused.",
            "Multiple meeting surfaces are ambiguous; waiting without combining captions.",
        ):
            app.capture_event("meeting_visibility_lost", message)
            self.assertEqual(app._state, "uncertain")
            self.assertEqual(app._status, message)
            self.assertTrue(app._meeting_active)
        app.icon.notify.assert_not_called()

    def test_failed_save_does_not_lie_about_setting_or_show_popup(self):
        app = TrayApp()
        with patch("tray_settings.save", side_effect=PermissionError("locked")), patch("teams_caption_tray.logging.exception"):
            app.toggle_popups()
        self.assertTrue(app.popups_hidden())
        app.icon.notify.assert_not_called()
        self.assertIn("Could not save", app._status)

    def test_bad_preferences_fail_quiet_and_unrelated_settings_survive(self):
        path = self.base / "tray-settings.json"
        for content in ('{', '{"hide_popups": "false"}', '[]'):
            path.write_text(content)
            with patch("tray_settings.logging.exception"):
                self.assertTrue(tray_settings.load(path)["hide_popups"])
        path.write_text('{"hide_popups": false, "future_setting": 7}')
        app = TrayApp()
        app.toggle_popups()
        self.assertEqual(tray_settings.load(path)["future_setting"], 7)


class ScanRecoveryTests(unittest.TestCase):
    def test_com_error_preserves_session_and_does_not_count_as_leave(self):
        active = FakeControl("Captions | Planning | Microsoft Teams")
        error = ctypes.COMError(-2147220991, "subscriber failure", None)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "meeting.md"
            args = capture.build_parser().parse_args(["--output", str(output), "--exit-after-meeting"])
            events, saved = Mock(), Mock()
            # First empty scan after recovery must not end the meeting even after a long outage.
            with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
                capture, "find_teams_windows", side_effect=[[active], error, [], [active], [], []]
            ), patch.object(capture, "caption_strings", side_effect=[["Ada: Before outage"], ["Grace: After outage"]]), patch.object(
                capture.time, "monotonic", side_effect=itertools.count(0, 20)
            ), patch.object(capture.time, "sleep"), patch("builtins.print"), patch.object(capture.LOG, "warning"):
                self.assertEqual(capture.run(args, event_callback=events, transcript_saved_callback=saved), 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("Before outage", text)
            self.assertIn("After outage", text)
            kinds = [call.args[0] for call in events.call_args_list]
            self.assertEqual(kinds.count("meeting_started"), 1)
            self.assertEqual(kinds.count("meeting_ended"), 1)
            self.assertIn("scan_retrying", kinds)
            self.assertIn("scan_recovered", kinds)
            saved.assert_called_once()

    def test_stop_interrupts_retry_and_saves_active_transcript(self):
        active = FakeControl("Captions | Planning | Microsoft Teams")
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "meeting.md"
            args = capture.build_parser().parse_args(["--output", str(output)])
            with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
                capture, "find_teams_windows", side_effect=[[active], ctypes.COMError(-2147220991, "failure", None)]
            ), patch.object(capture, "caption_strings", return_value=["Ada: Keep this caption"]), patch(
                "builtins.print"
            ), patch.object(capture.LOG, "warning"):
                self.assertEqual(capture.run(args, stop_event=stop), 0)
            self.assertIn("Keep this caption", output.read_text())
            self.assertEqual(stop.wait.call_args.args, (2.0,))

    def test_persistent_errors_back_off_to_30_seconds(self):
        active = FakeControl("Captions | Planning | Microsoft Teams")
        error = ctypes.COMError(-2147220991, "failure", None)
        with tempfile.TemporaryDirectory() as temp:
            args = capture.build_parser().parse_args(["--output", str(Path(temp) / "meeting.md"), "--exit-after-meeting"])
            with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
                capture, "find_teams_windows", side_effect=[error] * 7 + [[active], [], []]
            ), patch.object(capture, "caption_strings", return_value=[]), patch.object(
                capture.time, "monotonic", side_effect=itertools.count(0, 20)
            ), patch.object(capture.time, "sleep") as sleep, patch("builtins.print"), patch.object(capture.LOG, "warning"):
                capture.run(args)
            self.assertEqual([c.args[0] for c in sleep.call_args_list[:7]], [2, 4, 8, 16, 30, 30, 30])

    def test_programming_errors_are_not_silently_retried(self):
        args = capture.build_parser().parse_args([])
        with patch.object(capture, "import_uiautomation", return_value=Mock()), patch.object(
            capture, "find_teams_windows", side_effect=ValueError("unexpected bug")
        ), patch("builtins.print"):
            with self.assertRaisesRegex(ValueError, "unexpected bug"):
                capture.run(args)


if __name__ == "__main__":
    unittest.main()
