"""Tray health tests isolate notifications, preferences and all native UI."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teams_caption_tray import TrayApp


class CaptionHealthTrayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch("teams_caption_tray.capture.application_dir", return_value=Path(self.temp.name)))
        self.enterContext(patch("teams_caption_tray.pystray.Icon"))
        self.enterContext(patch("teams_caption_tray.logging.info"))
        self.app = TrayApp()

    def test_meeting_start_waits_and_keeps_update_guard(self):
        self.app.capture_event("meeting_started", "session started")
        self.assertEqual(self.app._state, "uncertain")
        self.assertIn("waiting", self.app._status)
        self.assertTrue(self.app._meeting_active)
        self.assertIsNotNone(self.app._installation_blocker())

    def test_only_usable_caption_events_turn_green(self):
        self.app.capture_event("meeting_started", "session started")
        for event in ("recording", "meeting_visibility_restored", "scan_recovered"):
            self.app.capture_event(event, "window is present")
            self.assertEqual(self.app._state, "uncertain", event)
        self.app.capture_event("caption", "Ada: a usable caption")
        self.assertEqual(self.app._state, "recording")

    def test_explicit_receiving_event_turns_green(self):
        self.app.capture_event("meeting_started", "session started")
        self.app.capture_event("captions_receiving", "Receiving new meeting captions")
        self.assertEqual(self.app._state, "recording")

    def test_unavailable_and_waiting_are_amber_and_keep_meeting_owned(self):
        self.app.capture_event("meeting_started", "session started")
        for event in ("captions_unavailable", "captions_waiting"):
            self.app.capture_event("caption", "Ada: speech")
            self.app.capture_event(event, "Captions unavailable — compact view")
            self.assertEqual(self.app._state, "uncertain")
            self.assertEqual(self.app._status, "Captions unavailable — compact view")
            self.assertTrue(self.app._meeting_active)
            self.assertIsNotNone(self.app._installation_blocker())

    def test_feed_health_never_opens_popups_even_when_enabled(self):
        self.app._preferences["hide_popups"] = False
        for event in ("captions_waiting", "captions_unavailable", "captions_receiving"):
            self.app.capture_event(event, "status")
        self.app.icon.notify.assert_not_called()

    def test_visibility_restored_requires_new_speech(self):
        self.app.capture_event("meeting_started", "session started")
        self.app.capture_event("caption", "Ada: speech")
        self.app.capture_event("meeting_visibility_lost", "compact view")
        self.app.capture_event("meeting_visibility_restored", "same meeting")
        self.assertEqual(self.app._state, "uncertain")
        self.app.capture_event("captions_receiving", "new speech")
        self.assertEqual(self.app._state, "recording")

    def test_scan_recovery_during_meeting_waits_not_idle(self):
        self.app.capture_event("meeting_started", "session started")
        self.app.capture_event("scan_retrying", "COM error")
        self.assertEqual(self.app._state, "error")
        self.app.capture_event("scan_recovered", "scan is available")
        self.assertEqual(self.app._state, "uncertain")
        self.assertTrue(self.app._meeting_active)

    def test_scan_recovery_without_meeting_is_idle(self):
        self.app.capture_event("scan_retrying", "COM error")
        self.app.capture_event("scan_recovered", "scan is available")
        self.assertEqual(self.app._state, "watching")

    def test_write_events_cannot_fake_received_captions(self):
        self.app.capture_event("meeting_started", "session started")
        for event in ("write_blocked", "write_recovered"):
            self.app.capture_event(event, "write status")
            self.assertEqual(self.app._state, "uncertain", event)
            self.assertTrue(self.app._meeting_active)

    def test_meeting_end_releases_guard_after_caption_unavailability(self):
        self.app.capture_event("meeting_started", "session started")
        self.app.capture_event("captions_unavailable", "compact view")
        self.app.capture_event("meeting_ended", "saved")
        self.assertEqual(self.app._state, "watching")
        self.assertFalse(self.app._meeting_active)
        self.assertIsNone(self.app._installation_blocker())

    def test_unrelated_legacy_heartbeat_preserves_waiting_health_status(self):
        self.app.capture_event("meeting_started", "session started")
        self.app.capture_event("captions_unavailable", "Captions unavailable — compact view")
        self.app.capture_event("recording", "meeting found")
        self.assertEqual(self.app._state, "uncertain")
        self.assertEqual(self.app._status, "Captions unavailable — compact view")


if __name__ == "__main__":
    unittest.main()
