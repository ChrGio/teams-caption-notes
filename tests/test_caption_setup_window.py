"""The caption setup GUI is tested without real Tk windows or Teams actions."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import caption_setup_window as window


class CaptionSetupWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = MagicMock()
        self.root.after.return_value = "scheduled-poll"
        for name in ("Frame", "Label", "LabelFrame", "Button", "Scrollbar"):
            current = patch.object(window.ttk, name, side_effect=lambda *args, **kwargs: MagicMock())
            current.start()
            self.addCleanup(current.stop)
        self.variable_patch = patch.object(window.tk, "StringVar")
        self.variable_patch.start()
        self.addCleanup(self.variable_patch.stop)
        self.text_box = self.start_patch(window.tk, "Text")
        self.future = self.start_patch(window.caption_setup, "enable_always_show")
        self.current = self.start_patch(window.caption_setup, "enable_current_meeting")
        self.automatic = MagicMock()
        self.import_auto = self.start_patch(window.capture, "import_uiautomation", return_value=self.automatic)
        self.threads = self.start_patch(window.threading, "Thread")
        self.confirm = self.start_patch(window.messagebox, "askyesno", return_value=True)
        self.start_patch(window.logging, "exception")
        self.view = window.CaptionSetupWindow(self.root)

    def start_patch(self, target, name, **kwargs):
        current = patch.object(target, name, **kwargs)
        result = current.start()
        self.addCleanup(current.stop)
        return result

    def test_opening_window_never_changes_teams_or_starts_worker(self):
        self.assertFalse(self.view.busy)
        self.future.assert_not_called()
        self.current.assert_not_called()
        self.import_auto.assert_not_called()
        self.threads.assert_not_called()
        self.confirm.assert_not_called()
        self.root.protocol.assert_called_once_with("WM_DELETE_WINDOW", self.view.close)

    def test_cancelled_confirmation_never_starts_work(self):
        self.confirm.return_value = False
        self.view.begin("future")
        self.assertFalse(self.view.busy)
        self.threads.assert_not_called()
        self.future.assert_not_called()
        self.import_auto.assert_not_called()

    def test_each_action_requires_explicit_default_no_confirmation(self):
        for action, expected in (("future", window.MANUAL_FUTURE), ("current", window.MANUAL_CURRENT)):
            with self.subTest(action=action):
                self.view.busy = False
                self.view.begin(action)
                self.assertEqual(self.confirm.call_args.kwargs["default"], window.messagebox.NO)
                text = self.confirm.call_args.args[1]
                self.assertIn(expected, text)
                self.assertIn("visible Teams menus", text)
                self.assertIn("does not start Teams recording", text)
                self.assertIn("speaker identification", text)
                self.assertIn("profanity filtering", text)
                self.assertIn("share your screen", text)
        self.assertEqual(self.confirm.call_count, 2)

    def test_confirmed_action_starts_daemon_without_engine_on_ui_thread(self):
        self.view.begin("future")
        self.assertTrue(self.view.busy)
        self.assertFalse(self.view.cancel.is_set())
        self.threads.assert_called_once_with(target=self.view.run_action, args=("future",),
                                             name="teams-caption-setup", daemon=True)
        self.threads.return_value.start.assert_called_once_with()
        self.future.assert_not_called()
        self.import_auto.assert_not_called()

    def test_duplicate_action_is_ignored_while_busy(self):
        self.view.begin("future")
        self.view.begin("current")
        self.confirm.assert_called_once()
        self.threads.assert_called_once()

    def test_unknown_action_never_runs_engine(self):
        with self.assertRaises(ValueError):
            self.view.begin("record-meeting")
        self.confirm.assert_not_called()
        self.threads.assert_not_called()

    def test_unknown_worker_action_cannot_fall_back_to_current_meeting(self):
        self.view.run_action("unknown")
        self.import_auto.assert_not_called()
        self.current.assert_not_called()
        self.future.assert_not_called()
        self.assertEqual(self.view.events.get_nowait(), ("error", None))

    def test_engine_only_called_inside_worker_automation_context(self):
        result = SimpleNamespace(state="enabled", verified=True, message="Verified setting.")
        self.future.return_value = result
        self.view.status.reset_mock()
        self.view.run_action("future")
        self.automatic.UIAutomationInitializerInThread.assert_called_once_with()
        initializer = self.automatic.UIAutomationInitializerInThread.return_value
        initializer.__enter__.assert_called_once_with()
        initializer.__exit__.assert_called_once()
        self.future.assert_called_once_with(self.automatic, self.view.cancel)
        self.current.assert_not_called()
        self.view.status.set.assert_not_called()
        self.assertEqual(self.view.events.get_nowait(), ("result", result))

    def test_current_action_calls_only_current_engine(self):
        self.view.run_action("current")
        self.current.assert_called_once_with(self.automatic, self.view.cancel)
        self.future.assert_not_called()

    def test_cancelled_before_worker_does_not_import_or_operate_automation(self):
        self.view.cancel.set()
        self.view.run_action("future")
        self.import_auto.assert_not_called()
        self.future.assert_not_called()
        self.assertEqual(self.view.events.get_nowait(), ("cancelled", None))

    def test_cancelled_during_automation_initialization_does_not_operate_teams(self):
        self.automatic.UIAutomationInitializerInThread.return_value.__enter__.side_effect = self.view.cancel.set
        self.view.run_action("future")
        self.future.assert_not_called()
        self.assertEqual(self.view.events.get_nowait(), ("cancelled", None))

    def test_automation_error_is_queued_not_raised_or_sent_to_tk(self):
        self.future.side_effect = OSError("UI Automation subscriber failure")
        self.view.status.reset_mock()
        self.view.run_action("future")
        self.view.status.set.assert_not_called()
        self.assertEqual(self.view.events.get_nowait(), ("error", None))
        self.current.assert_not_called()

    def test_poll_displays_verified_result_and_reenables_controls(self):
        self.view.busy = True
        self.view.events.put(("result", SimpleNamespace(state="enabled", verified=True, message="Read back on.")))
        self.view.poll()
        self.assertFalse(self.view.busy)
        self.assertTrue(self.view.status.set.call_args.args[0].startswith("Verified: captions are enabled."))
        for button in self.view.action_buttons:
            button.state.assert_called_with(["!disabled"])
        self.view.cancel_button.state.assert_called_with(["disabled"])

    def test_unverified_success_state_is_never_presented_as_verified(self):
        for state in ("enabled", "already_enabled", "unverified", "unexpected"):
            with self.subTest(state=state):
                result = SimpleNamespace(state=state, verified=False, message="Readback unavailable.")
                text = self.view.describe_result(result)
                self.assertTrue(text.startswith("Not verified:"))
                self.assertNotIn("Verified: captions", text)

    def test_verified_already_enabled_result_is_clear(self):
        result = SimpleNamespace(state="already_enabled", verified=True, message="Setting already on.")
        self.assertTrue(self.view.describe_result(result).startswith("Verified: captions were already enabled."))

    def test_unavailable_and_cancelled_are_distinct_and_not_success(self):
        unavailable = self.view.describe_result(SimpleNamespace(state="unavailable", verified=False, message="Teams not found."))
        cancelled = self.view.describe_result(SimpleNamespace(state="cancelled", verified=False, message="User cancelled."))
        self.assertTrue(unavailable.startswith("Setup is unavailable"))
        self.assertIn("manual instructions", unavailable)
        self.assertTrue(cancelled.startswith("Setup was cancelled"))
        self.assertNotIn("Verified:", unavailable + cancelled)

    def test_worker_error_poll_does_not_claim_teams_unchanged(self):
        self.view.events.put(("error", None))
        self.view.poll()
        text = self.view.status.set.call_args.args[0]
        self.assertIn("not verified", text)
        self.assertIn("Caption capture is independent", text)
        self.assertNotIn("Teams was not changed", text)

    def test_cancel_keeps_operation_busy_until_worker_finishes(self):
        self.view.busy = True
        self.view.cancel_action()
        self.assertTrue(self.view.cancel.is_set())
        self.assertTrue(self.view.busy)
        self.assertIn("may need to finish", self.view.status.set.call_args.args[0])
        self.view.cancel_button.state.assert_called_with(["disabled"])

    def test_close_cancels_without_waiting_for_worker_or_changing_teams(self):
        self.view.worker = Mock()
        self.view.busy = True
        self.view.close()
        self.assertTrue(self.view.cancel.is_set())
        self.assertTrue(self.view.closed)
        self.view.worker.join.assert_not_called()
        self.root.after_cancel.assert_called_once_with("scheduled-poll")
        self.root.destroy.assert_called_once_with()
        self.future.assert_not_called()
        self.current.assert_not_called()

    def test_closed_window_ignores_queued_results_and_new_actions(self):
        self.view.close()
        self.view.status.reset_mock()
        self.root.after.reset_mock()
        self.view.events.put(("result", SimpleNamespace(state="enabled", verified=True, message="ok")))
        self.view.poll()
        self.view.begin("future")
        self.view.status.set.assert_not_called()
        self.root.after.assert_not_called()
        self.confirm.assert_not_called()

    def test_thread_start_failure_restores_controls_without_team_action(self):
        self.threads.return_value.start.side_effect = RuntimeError("no thread")
        self.view.begin("future")
        self.assertFalse(self.view.busy)
        self.assertIn("could not start", self.view.status.set.call_args.args[0])
        self.future.assert_not_called()
        self.current.assert_not_called()

    def test_manual_instructions_reference_only_caption_settings(self):
        self.assertEqual(window.MANUAL_FUTURE,
                         "Teams Settings → Accessibility → Always show captions in my calls and meetings")
        self.assertEqual(window.MANUAL_CURRENT,
                         "In the meeting: More → Language and speech → Show live captions")

    def test_result_area_is_bounded_scrollable_and_read_only(self):
        self.assertEqual(self.text_box.call_args.kwargs["height"], 5)
        self.assertEqual(self.text_box.call_args.kwargs["state"], "disabled")
        self.view.status.get.return_value = "Verified status\n" + "Details\n" * 50
        self.view.result_box.reset_mock()
        self.view.render_status()
        self.view.result_box.insert.assert_called_once_with("1.0", self.view.status.get.return_value)
        self.view.result_box.configure.assert_called_with(state="disabled")
        self.view.result_box.yview_moveto.assert_called_once_with(0)

    def test_main_only_creates_window_and_enters_event_loop(self):
        with patch.object(window.tk, "Tk", return_value=self.root), patch.object(window, "CaptionSetupWindow") as build:
            self.assertEqual(window.main(), 0)
        build.assert_called_once_with(self.root)
        self.root.mainloop.assert_called_once_with()
        self.future.assert_not_called()
        self.current.assert_not_called()


if __name__ == "__main__":
    unittest.main()
