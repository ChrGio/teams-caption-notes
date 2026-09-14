"""Caption onboarding integration; no real process, UI or registry operations."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import teams_caption_tray as tray
import tray_settings


class CaptionSetupTrayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prefs = self.base / "tray-settings.json"
        for target, kwargs in (
            ("teams_caption_tray.capture.application_dir", {"return_value": self.base}),
            ("teams_caption_tray.pystray.Icon", {}),
            ("teams_caption_tray.subprocess.Popen", {}),
            ("teams_caption_tray.logging.exception", {}),
        ):
            current = patch(target, **kwargs)
            mocked = current.start()
            self.addCleanup(current.stop)
            if target.endswith("Popen"):
                self.launch = mocked
                self.launch.return_value.poll.return_value = None

    def test_new_manual_packaged_install_offers_setup_once(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["app.exe"]):
            app = tray.TrayApp()
            app._offer_first_run_setup()
            app._offer_first_run_setup()
        self.launch.assert_called_once()
        self.assertEqual(self.launch.call_args.args[0][-1], "--caption-setup")
        self.assertTrue(tray_settings.load(self.prefs)["caption_setup_seen"])
        self.assertIn("available", app.caption_setup_status)
        self.assertNotIn("enabled", app.caption_setup_status)

    def test_source_run_never_auto_opens_setup(self):
        with patch.object(sys, "frozen", False, create=True):
            tray.TrayApp()._offer_first_run_setup()
        self.launch.assert_not_called()

    def test_sign_in_run_never_auto_opens_setup_even_with_no_preferences(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["app.exe", "--startup"]):
            tray.TrayApp()._offer_first_run_setup()
        self.launch.assert_not_called()
        self.assertFalse(self.prefs.exists())

    def test_existing_v44_install_does_not_open_setup_during_upgrade(self):
        tray_settings.save(self.prefs, {"hide_popups": True, "startup_enabled": True})
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["app.exe"]):
            app = tray.TrayApp()
            app._offer_first_run_setup()
        self.launch.assert_not_called()
        self.assertIn("recommended", app.caption_setup_status)

    def test_invalid_existing_settings_are_not_treated_as_new_install(self):
        self.prefs.write_text("{")
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["app.exe"]):
            tray.TrayApp()._offer_first_run_setup()
        self.launch.assert_not_called()

    def test_manual_open_preserves_preferences_and_resets_pyinstaller_environment(self):
        tray_settings.save(self.prefs, {"hide_popups": True, "startup_enabled": False, "future_key": 42})
        with patch.object(sys, "frozen", True, create=True):
            app = tray.TrayApp()
            app.open_caption_setup()
        prefs = tray_settings.load(self.prefs)
        self.assertFalse(prefs["startup_enabled"])
        self.assertEqual(prefs["future_key"], 42)
        self.assertTrue(prefs["caption_setup_seen"])
        self.assertEqual(self.launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_duplicate_window_is_not_launched(self):
        app = tray.TrayApp()
        app.open_caption_setup()
        app.open_caption_setup()
        self.launch.assert_called_once()

    def test_closed_window_can_be_reopened(self):
        app = tray.TrayApp()
        app.open_caption_setup()
        self.launch.return_value.poll.return_value = 0
        app.open_caption_setup()
        self.assertEqual(self.launch.call_count, 2)

    def test_setup_launch_failure_does_not_mark_setup_seen(self):
        self.launch.side_effect = PermissionError("blocked")
        app = tray.TrayApp()
        app.open_caption_setup()
        self.assertNotIn("caption_setup_seen", app._preferences)
        self.assertFalse(self.prefs.exists())

    def test_installing_and_exiting_block_new_setup_window(self):
        app = tray.TrayApp()
        app._installing = True
        app.open_caption_setup()
        app._installing = False
        app._exiting.set()
        app.open_caption_setup()
        self.launch.assert_not_called()

    def test_open_setup_window_blocks_install_but_not_capture(self):
        app = tray.TrayApp()
        app.open_caption_setup()
        with app._lock:
            self.assertIn("caption setup", app._installation_blocker())
        self.assertFalse(app._stop_event.is_set())
        self.assertFalse(app._installing)

    def test_invalid_setup_seen_type_is_rejected(self):
        with self.assertRaises(ValueError):
            tray_settings.save(self.prefs, {"caption_setup_seen": "yes"})


class CaptionSetupDispatchTests(unittest.TestCase):
    def test_setup_subprocess_never_registers_startup_or_starts_watcher(self):
        setup_main = Mock(return_value=0)
        with patch.object(sys, "argv", ["app.exe", "--caption-setup"]), patch.dict(
            "sys.modules", {"caption_setup_window": SimpleNamespace(main=setup_main)}
        ), patch.object(tray, "acquire_single_instance") as lock, patch.object(
            tray, "TrayApp"
        ) as app, patch.object(tray.logging, "basicConfig"), patch.object(
            tray.windows_startup, "apply_default"
        ) as startup:
            self.assertEqual(tray.main(), 0)
        setup_main.assert_called_once_with()
        lock.assert_not_called()
        app.assert_not_called()
        startup.assert_not_called()

    def test_malformed_setup_arguments_fail_before_any_ui(self):
        with patch.object(sys, "argv", ["app.exe", "--caption-setup", "--notes-window"]), patch.object(
            tray, "TrayApp"
        ) as app, patch.object(tray.logging, "basicConfig") as configure:
            self.assertEqual(tray.main(), 1)
        app.assert_not_called()
        configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
