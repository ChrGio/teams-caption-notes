"""Tray update tests use mocks only: no network, GUI, app launches or registry writes."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app_update
import teams_caption_tray as tray
import tray_settings


class FakeWorker:
    def __init__(self, *, finishes=True, events=None):
        self.alive = True
        self.finishes = finishes
        self.events = events if events is not None else []
        self.join_timeouts = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)
        self.events.append("watcher-join")
        if self.finishes:
            self.alive = False


class TrayUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, kwargs in (
            ("teams_caption_tray.capture.application_dir", {"return_value": self.root}),
            ("teams_caption_tray.pystray.Icon", {}),
            ("teams_caption_tray.logging.exception", {}),
        ):
            current = patch(target, **kwargs)
            current.start()
            self.addCleanup(current.stop)
        frozen = patch.object(tray.sys, "frozen", True, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)
        self.app = tray.TrayApp()
        self.app._update_message = Mock(return_value=True)
        self.item = SimpleNamespace(version="4.5", html_url="https://github.com/ChrGio/teams-caption-notes/releases/tag/v4.5")
        self.check = self.start_patch("check_for_update", return_value=self.item)
        self.download = self.start_patch("download_update", return_value=self.root / "candidate.exe")
        self.prepare = self.start_patch("prepare_install", return_value=self.root / "install.json")
        self.launch = self.start_patch("launch_installer", return_value=Mock())

    def start_patch(self, name, **kwargs):
        current = patch.object(app_update, name, **kwargs)
        result = current.start()
        self.addCleanup(current.stop)
        return result

    def run_check(self):
        self.assertTrue(self.app._update_lock.acquire(blocking=False))
        self.app._check_update_worker()
        self.assertFalse(self.app._update_lock.locked())
        self.assertFalse(self.app._installing)

    def assert_not_installed(self):
        self.prepare.assert_not_called()
        self.launch.assert_not_called()
        self.assertFalse(self.app._exiting.is_set())
        self.app.icon.stop.assert_not_called()

    def test_no_new_release_only_reports_status(self):
        self.check.return_value = None
        self.run_check()
        self.check.assert_called_once_with(tray.APP_VERSION)
        self.assertIn("up to date", self.app._update_status)
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_user_declining_does_not_download_stop_or_launch(self):
        self.app._update_message.return_value = False
        self.run_check()
        self.app._update_message.assert_called_once()
        self.assertTrue(self.app._update_message.call_args.kwargs["confirm"])
        self.download.assert_not_called()
        self.assertFalse(self.app._stop_event.is_set())
        self.assert_not_installed()

    def test_source_mode_reports_release_without_offering_install(self):
        with patch.object(tray.sys, "frozen", False):
            self.run_check()
        self.assertIn("packaged EXE only", self.app._update_message.call_args.args[0])
        self.assertNotIn("confirm", self.app._update_message.call_args.kwargs)
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_active_meeting_blocks_install(self):
        self.app.capture_event("meeting_started", "test meeting")
        self.run_check()
        self.assertIn("meeting", self.app._update_message.call_args.args[0])
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_uncertain_meeting_stays_blocked_even_after_tray_color_changes(self):
        self.app.capture_event("meeting_started", "test meeting")
        self.app.capture_event("meeting_visibility_lost", "not visible")
        self.app.capture_event("scan_recovered", "watching")
        self.assertEqual(self.app._state, "watching")
        self.run_check()
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_open_notes_blocks_install_without_closing_window(self):
        self.app._notes_process = Mock()
        self.app._notes_process.poll.return_value = None
        self.run_check()
        self.assertIn("Close the Chats and notes", self.app._update_message.call_args.args[0])
        self.app._notes_process.terminate.assert_not_called()
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_summary_blocks_install(self):
        self.app._summary_lock.acquire()
        self.addCleanup(self.app._summary_lock.release)
        self.run_check()
        self.assertIn("summary", self.app._update_message.call_args.args[0])
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_sign_in_blocks_install(self):
        self.app._sign_in_thread = Mock()
        self.app._sign_in_thread.is_alive.return_value = True
        self.run_check()
        self.assertIn("sign-in", self.app._update_message.call_args.args[0])
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_meeting_starting_during_confirmation_blocks_download(self):
        def answer(message, **kwargs):
            if kwargs.get("confirm"):
                self.app.capture_event("meeting_started", "new meeting")
            return True
        self.app._update_message.side_effect = answer
        self.run_check()
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_meeting_starting_during_download_blocks_stop_and_install(self):
        def downloaded(*args):
            self.app.capture_event("meeting_started", "new meeting")
            return self.root / "candidate.exe"
        self.download.side_effect = downloaded
        self.run_check()
        self.assertFalse(self.app._stop_event.is_set())
        self.assert_not_installed()

    def test_short_meeting_starting_and_finishing_during_download_still_blocks_install(self):
        def downloaded(*args):
            self.app.capture_event("meeting_started", "new meeting")
            self.app.capture_event("meeting_ended", "saved")
            return self.root / "candidate.exe"
        self.download.side_effect = downloaded
        self.run_check()
        self.assertFalse(self.app._stop_event.is_set())
        self.assert_not_installed()

    def test_watcher_join_timeout_never_prepares_or_launches_helper(self):
        self.app._worker = FakeWorker(finishes=False)
        self.app.start_watcher = Mock()
        self.run_check()
        self.assertEqual(self.app._worker.join_timeouts, [30])
        self.assertTrue(self.app._stop_event.is_set())
        self.assertIn("not finished saving", self.app._update_message.call_args.args[0])
        self.app.start_watcher.assert_not_called()
        self.assert_not_installed()

    def test_helper_failure_restores_previously_running_watcher_without_exiting(self):
        self.app._worker = FakeWorker()
        self.app.start_watcher = Mock()
        self.launch.side_effect = app_update.UpdateError("helper unavailable")
        self.run_check()
        self.launch.assert_called_once()
        self.app.start_watcher.assert_called_once_with()
        self.assertFalse(self.app._exiting.is_set())
        self.app.icon.stop.assert_not_called()

    def test_preparation_failure_restores_watcher_and_never_launches(self):
        self.app._worker = FakeWorker()
        self.app.start_watcher = Mock()
        self.prepare.side_effect = app_update.UpdateError("could not stage helper")
        self.run_check()
        self.app.start_watcher.assert_called_once_with()
        self.launch.assert_not_called()
        self.app.icon.stop.assert_not_called()

    def test_failure_does_not_start_a_previously_stopped_watcher(self):
        self.app.start_watcher = Mock()
        self.launch.side_effect = app_update.UpdateError("helper unavailable")
        self.run_check()
        self.app.start_watcher.assert_not_called()
        self.assertFalse(self.app._exiting.is_set())

    def test_success_stops_watcher_then_readies_helper_then_exits(self):
        events = []
        self.app._worker = FakeWorker(events=events)
        self.app.start_watcher = Mock()
        def prepare(*args):
            self.assertFalse(self.app._worker.alive)
            self.assertTrue(self.app._stop_event.is_set())
            events.append("prepare")
            return self.root / "install.json"
        def launch(*args):
            self.assertFalse(self.app._exiting.is_set())
            events.append("helper-ready")
        self.prepare.side_effect = prepare
        self.launch.side_effect = launch
        self.app.icon.stop.side_effect = lambda: events.append("exit")
        self.run_check()
        self.assertEqual(events, ["watcher-join", "prepare", "helper-ready", "exit"])
        self.assertTrue(self.app._exiting.is_set())
        self.app.start_watcher.assert_not_called()

    def test_exit_during_download_does_not_prepare_installer(self):
        def downloaded(*args):
            self.app.exit_app()
            return self.root / "candidate.exe"
        self.download.side_effect = downloaded
        self.run_check()
        self.prepare.assert_not_called()
        self.launch.assert_not_called()

    def test_network_failure_keeps_watcher_running(self):
        self.app._worker = FakeWorker()
        self.check.side_effect = app_update.UpdateError("offline")
        self.run_check()
        self.assertTrue(self.app._worker.alive)
        self.assertFalse(self.app._stop_event.is_set())
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_duplicate_check_does_not_start_second_thread(self):
        self.app._update_lock.acquire()
        try:
            with patch("teams_caption_tray.threading.Thread") as worker:
                self.app.check_for_updates()
            worker.assert_not_called()
        finally:
            self.app._update_lock.release()

    def test_confirmation_defaults_to_no_even_when_popup_notifications_hidden(self):
        with patch("teams_caption_tray.ctypes.windll.user32.MessageBoxW", return_value=7) as dialog:
            result = tray.TrayApp._update_message(self.app, "Install?", confirm=True)
        self.assertFalse(result)
        self.assertTrue(dialog.call_args.args[3] & 0x100)

    def test_startup_default_is_applied_with_failure_isolated_from_watcher(self):
        self.app.start_watcher = Mock()
        def apply(path):
            tray_settings.save(path, {"startup_enabled": False, "hide_popups": False})
            raise PermissionError("registry denied")
        with patch("teams_caption_tray.windows_startup.apply_default", side_effect=apply) as startup, patch.object(
            self.app, "_offer_first_run_setup"
        ):
            self.app.run()
        startup.assert_called_once_with(self.app._preferences_path)
        self.app.start_watcher.assert_called_once_with()
        self.app.icon.run.assert_called_once_with()
        self.assertFalse(self.app._preferences["startup_enabled"])
        self.assertFalse(self.app.popups_hidden())


class UpdateHelperDispatchTests(unittest.TestCase):
    def test_helper_dispatch_bypasses_startup_lock_and_normal_logging(self):
        with patch.object(tray.sys, "argv", ["updater.exe", "--apply-update", "install.json"]), patch.object(
            app_update, "apply_update", return_value=1
        ) as apply, patch.object(tray, "acquire_single_instance") as lock, patch.object(
            tray, "TrayApp"
        ) as app, patch.object(tray.logging, "basicConfig") as logging_setup, patch.object(
            tray.windows_startup, "apply_default"
        ) as startup:
            self.assertEqual(tray.main(), 1)
        apply.assert_called_once_with(Path("install.json"))
        lock.assert_not_called()
        app.assert_not_called()
        logging_setup.assert_not_called()
        startup.assert_not_called()

    def test_malformed_helper_arguments_never_start_application(self):
        for args in (["app.exe", "--apply-update"],
                     ["app.exe", "--notes-window", "--apply-update"],
                     ["app.exe", "--apply-update", "plan", "extra"]):
            with self.subTest(args=args), patch.object(tray.sys, "argv", args), patch.object(
                app_update, "apply_update"
            ) as apply, patch.object(tray, "TrayApp") as app:
                self.assertEqual(tray.main(), 1)
                apply.assert_not_called()
                app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
