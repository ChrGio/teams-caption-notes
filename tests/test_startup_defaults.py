"""Startup tests mock every registry access; no real registrations are changed."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tray_settings
import windows_startup


class StartupDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tray-settings.json"
        for name, value in (("frozen", True),):
            task_patch = patch.object(windows_startup.sys, name, value, create=True)
            task_patch.start()
            self.addCleanup(task_patch.stop)
        self.command_patch = patch.object(windows_startup, "startup_command", return_value="current.exe")
        self.command = self.command_patch.start()
        self.addCleanup(self.command_patch.stop)
        self.registered_patch = patch.object(windows_startup, "registered_command", return_value=None)
        self.registered = self.registered_patch.start()
        self.addCleanup(self.registered_patch.stop)
        self.set_patch = patch.object(windows_startup, "set_enabled")
        self.set_enabled = self.set_patch.start()
        self.addCleanup(self.set_patch.stop)

    def write_preferences(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_first_packaged_launch_enables_and_persists_default(self):
        self.assertTrue(windows_startup.apply_default(self.path))
        self.set_enabled.assert_called_once_with(True)
        self.assertEqual(tray_settings.load_strict(self.path), {"hide_popups": True, "startup_enabled": True})

    def test_old_configuration_preserves_unrelated_preferences(self):
        self.write_preferences({"hide_popups": False, "future_setting": {"key": "value"}})
        self.registered.return_value = "old.exe"
        self.assertTrue(windows_startup.apply_default(self.path))
        self.set_enabled.assert_called_once_with(True)
        self.assertEqual(tray_settings.load_strict(self.path), {
            "hide_popups": False, "startup_enabled": True, "future_setting": {"key": "value"}})

    def test_saved_opt_out_remains_off_across_restarts(self):
        self.write_preferences({"startup_enabled": False})
        for _ in range(3):
            self.assertFalse(windows_startup.apply_default(self.path))
        self.set_enabled.assert_not_called()
        self.command.assert_not_called()

    def test_opt_out_removes_lingering_old_registration(self):
        self.write_preferences({"startup_enabled": False})
        self.registered.return_value = "old.exe"
        self.assertFalse(windows_startup.apply_default(self.path))
        self.set_enabled.assert_called_once_with(False)

    def test_enabled_updates_old_exe_but_does_not_rewrite_current_registration(self):
        self.write_preferences({"startup_enabled": True})
        self.registered.return_value = "old.exe"
        self.assertTrue(windows_startup.apply_default(self.path))
        self.set_enabled.assert_called_once_with(True)
        self.set_enabled.reset_mock()
        self.registered.return_value = "current.exe"
        self.assertTrue(windows_startup.apply_default(self.path))
        self.set_enabled.assert_not_called()

    def test_source_launch_is_read_only_even_with_enabled_preference(self):
        self.write_preferences({"startup_enabled": True})
        before = self.path.read_bytes()
        with patch.object(windows_startup.sys, "frozen", False):
            self.assertFalse(windows_startup.apply_default(self.path))
        self.assertEqual(before, self.path.read_bytes())
        self.registered.assert_not_called()
        self.set_enabled.assert_not_called()

    def test_source_first_run_does_not_create_settings(self):
        with patch.object(windows_startup.sys, "frozen", False):
            self.assertFalse(windows_startup.apply_default(self.path))
        self.assertFalse(self.path.exists())
        self.registered.assert_not_called()

    def test_malformed_or_invalid_settings_never_enable_startup(self):
        for text in ('{', '[]', '{"startup_enabled":"false"}',
                     '{"startup_enabled":false,"hide_popups":"false"}'):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    windows_startup.apply_default(self.path)
                self.assertEqual(text, self.path.read_text(encoding="utf-8"))
        self.registered.assert_not_called()
        self.set_enabled.assert_not_called()

    def test_unreadable_settings_do_not_enable_startup(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                windows_startup.apply_default(self.path)
        self.set_enabled.assert_not_called()

    def test_failed_preference_write_does_not_modify_registry(self):
        with patch.object(tray_settings, "save", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                windows_startup.apply_default(self.path)
        self.set_enabled.assert_not_called()

    def test_registry_denial_is_controlled_and_keeps_saved_choice(self):
        self.set_enabled.side_effect = PermissionError("policy")
        with self.assertRaises(PermissionError):
            windows_startup.apply_default(self.path)
        self.assertTrue(tray_settings.load_strict(self.path)["startup_enabled"])

    def test_explicit_toggle_preserves_all_preferences(self):
        self.write_preferences({"hide_popups": False, "startup_enabled": True, "other": 42})
        result = windows_startup.set_preference(False, self.path)
        self.set_enabled.assert_called_once_with(False)
        self.assertEqual(result, {"hide_popups": False, "startup_enabled": False, "other": 42})
        self.assertEqual(result, tray_settings.load_strict(self.path))

    def test_explicit_opt_out_survives_denied_registry_change(self):
        self.set_enabled.side_effect = PermissionError("policy")
        with self.assertRaises(PermissionError):
            windows_startup.set_preference(False, self.path)
        self.assertFalse(tray_settings.load_strict(self.path)["startup_enabled"])

    def test_explicit_enable_validates_path_before_saving(self):
        self.command.side_effect = ValueError("path too long")
        with self.assertRaises(ValueError):
            windows_startup.set_preference(True, self.path)
        self.assertFalse(self.path.exists())
        self.set_enabled.assert_not_called()

    def test_invalid_explicit_choice_is_rejected(self):
        with self.assertRaises(ValueError):
            windows_startup.set_preference("false", self.path)
        self.assertFalse(self.path.exists())
        self.set_enabled.assert_not_called()

    def test_saving_popup_preference_preserves_saved_startup_opt_out(self):
        self.write_preferences({"startup_enabled": False, "future_setting": 7})
        tray_settings.save(self.path, {"hide_popups": False})
        self.assertEqual(tray_settings.load_strict(self.path), {
            "startup_enabled": False, "future_setting": 7, "hide_popups": False})

    def test_saving_fallback_does_not_replace_damaged_preferences(self):
        original = '{"startup_enabled":false,"hide_popups":"invalid"}'
        self.path.write_text(original, encoding="utf-8")
        with patch("tray_settings.logging.exception"):
            fallback = tray_settings.load(self.path)
        with self.assertRaises(ValueError):
            tray_settings.save(self.path, fallback)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
