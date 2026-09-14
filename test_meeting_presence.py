import unittest
from unittest.mock import Mock, patch

import meeting_presence as presence
from meeting_presence import MeetingPresence, WindowIdentity


def call_window(hwnd=100, pid=200, title="planning"):
    return WindowIdentity(hwnd, pid, f"{title} | microsoft teams", title)


class MeetingPresenceTests(unittest.TestCase):
    def setUp(self):
        self.meeting = call_window()
        self.state = MeetingPresence()
        self.state.remember([self.meeting])

    def test_known_window_with_missing_controls_stays_uncertain_indefinitely(self):
        # Visibility and caption controls intentionally are not lifecycle evidence.
        native_probe = Mock(return_value=False)
        for now in (0, 12, 300, 3600):
            self.assertEqual(self.state.observe([self.meeting], native_probe, now, 8), "uncertain")
            self.assertIsNone(self.state.missing_since)
        native_probe.assert_not_called()

    def test_absent_call_requires_a_full_confirmed_missing_grace_period(self):
        self.assertEqual(self.state.observe([], lambda _: False, 100, 8), "missing")
        self.assertEqual(self.state.observe([], lambda _: False, 107.99, 8), "missing")
        self.assertEqual(self.state.observe([], lambda _: False, 108, 8), "ended")

    def test_uncertainty_resets_departure_timer(self):
        self.assertEqual(self.state.observe([], lambda _: False, 0, 8), "missing")
        self.assertEqual(self.state.observe([self.meeting], lambda _: False, 7, 8), "uncertain")
        self.assertEqual(self.state.observe([], lambda _: False, 100, 8), "missing")
        self.assertEqual(self.state.observe([], lambda _: False, 108, 8), "ended")

    def test_scan_error_reset_requires_new_complete_grace_period(self):
        self.state.observe([], lambda _: False, 0, 8)
        self.state.uncertain()
        self.assertEqual(self.state.observe([], lambda _: False, 1000, 8), "missing")
        self.assertEqual(self.state.observe([], lambda _: False, 1008, 8), "ended")

    def test_native_probe_preserves_call_when_uia_omits_window(self):
        native_probe = Mock(return_value=True)
        self.assertEqual(self.state.observe([], native_probe, 900, 8), "uncertain")
        native_probe.assert_called_once_with(self.meeting)
        self.assertIsNone(self.state.missing_since)

    def test_open_teams_main_window_does_not_keep_call_alive(self):
        main = WindowIdentity(101, 200, "chat | microsoft teams", "")
        self.assertEqual(self.state.observe([main], lambda _: False, 0, 8), "missing")
        self.assertEqual(self.state.observe([main], lambda _: False, 8, 8), "ended")

    def test_same_hwnd_reused_for_teams_main_does_not_match(self):
        main = WindowIdentity(100, 200, "chat | microsoft teams", "")
        self.assertFalse(self.meeting.matches(main))
        self.assertEqual(self.state.observe([main], lambda _: False, 0, 8), "missing")

    def test_same_hwnd_reused_for_different_named_call_does_not_match(self):
        replacement = call_window(title="budget review")
        self.assertFalse(self.meeting.matches(replacement))
        self.assertTrue(self.state.different_meeting([replacement]))

    def test_reused_hwnd_in_different_process_does_not_match(self):
        recycled = call_window(pid=201)
        self.assertFalse(self.meeting.matches(recycled))
        self.assertEqual(self.state.observe([recycled], lambda _: False, 0, 8), "missing")

    def test_same_title_on_different_native_window_does_not_match(self):
        other = call_window(hwnd=101)
        self.assertFalse(self.meeting.matches(other))
        self.assertEqual(self.state.observe([other], lambda _: False, 0, 8), "missing")

    def test_identity_without_hwnd_can_match_same_pid_and_title(self):
        no_hwnd = call_window(hwnd=0)
        self.assertTrue(self.meeting.matches(no_hwnd))
        self.assertFalse(self.meeting.matches(call_window(hwnd=0, pid=201)))

    def test_remember_refreshes_session_evidence_and_resets_missing_timer(self):
        self.state.observe([], lambda _: False, 0, 8)
        self.state.remember([self.meeting])
        self.assertEqual(self.state.known, [self.meeting])
        self.assertIsNone(self.state.missing_since)

    def test_named_second_meeting_is_distinct(self):
        self.assertTrue(self.state.different_meeting([call_window(hwnd=101, title="budget review")]))
        self.assertFalse(self.state.different_meeting([self.meeting]))
        self.assertFalse(self.state.different_meeting([]))

    def test_compact_transition_on_known_hwnd_preserves_session(self):
        compact = call_window(title="meeting compact view")
        self.assertTrue(self.meeting.matches(compact))
        self.assertFalse(self.state.different_meeting([compact]))
        self.assertEqual(self.state.observe([compact], lambda _: False, 600, 8), "uncertain")

    def test_unrelated_compact_window_does_not_match(self):
        compact = call_window(hwnd=101, title="meeting compact view")
        self.assertFalse(self.meeting.matches(compact))

    def test_compact_call_returning_to_main_teams_is_not_continuity(self):
        compact = call_window(title="meeting compact view")
        main = WindowIdentity(100, 200, "chat | microsoft teams", "")
        self.assertFalse(compact.matches(main))
        self.assertFalse(main.matches(compact))

    def test_remember_compact_retains_named_meeting_for_return_to_normal(self):
        self.state.remember([call_window(title="meeting compact view")])
        self.assertEqual(self.state.observe([self.meeting], lambda _: False, 600, 8), "uncertain")
        self.assertFalse(self.state.different_meeting([self.meeting]))

    def test_second_named_meeting_is_distinct_after_compact_transition(self):
        self.state.remember([call_window(title="meeting compact view")])
        replacement = call_window(title="budget review")
        self.assertTrue(self.state.different_meeting([replacement]))

    def test_compact_then_main_teams_does_not_keep_session_alive(self):
        self.state.remember([call_window(title="meeting compact view")])
        main = WindowIdentity(100, 200, "chat | microsoft teams", "")
        self.assertEqual(self.state.observe([main], lambda _: False, 0, 8), "missing")
        self.assertEqual(self.state.observe([main], lambda _: False, 8, 8), "ended")


class NativeWindowProbeTests(unittest.TestCase):
    def probe(self, *, exists=True, pid=200, title="Planning | Microsoft Teams", pid_readable=True):
        dll = Mock()
        dll.IsWindow.return_value = exists

        def read_pid(hwnd, destination):
            destination._obj.value = pid
            return 1 if pid_readable else 0

        def read_title(hwnd, buffer, length):
            if title is None:
                return 0
            buffer.value = title
            return len(title)

        dll.GetWindowThreadProcessId.side_effect = read_pid
        dll.GetWindowTextW.side_effect = read_title
        with patch.object(presence.os, "name", "nt"), patch.object(presence.ctypes, "WinDLL", return_value=dll):
            return presence.native_window_name(call_window()), dll

    def test_native_probe_returns_existing_window_title(self):
        result, _ = self.probe()
        self.assertEqual(result, (True, "Planning | Microsoft Teams"))

    def test_destroyed_window_is_not_alive(self):
        result, dll = self.probe(exists=False)
        self.assertEqual(result, (False, None))
        dll.GetWindowThreadProcessId.assert_not_called()

    def test_recycled_hwnd_owned_by_another_pid_is_not_alive(self):
        result, dll = self.probe(pid=201)
        self.assertEqual(result, (False, None))
        dll.GetWindowTextW.assert_not_called()

    def test_unreadable_native_title_preserves_unknown_state(self):
        result, _ = self.probe(title=None)
        self.assertEqual(result, (True, None))

    def test_unreadable_native_pid_preserves_unknown_state(self):
        result, _ = self.probe(pid_readable=False)
        self.assertEqual(result, (True, None))

    def test_absent_hwnd_does_not_probe_native_window(self):
        with patch.object(presence.ctypes, "WinDLL") as dll:
            self.assertEqual(presence.native_window_name(call_window(hwnd=0)), (False, None))
        dll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
