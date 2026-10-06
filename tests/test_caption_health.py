"""Feed-health unit tests; no Teams, window, network, or clipboard access."""

import unittest

from caption_health import CaptionHealth


class CaptionHealthTests(unittest.TestCase):
    def test_begin_waits_for_speech(self):
        health = CaptionHealth()
        event, message = health.begin(10)
        self.assertEqual(event, "captions_waiting")
        self.assertIn("waiting", message)
        self.assertIsNone(health.last_speech_at)

    def test_panel_with_no_speech_does_not_report_receiving(self):
        health = CaptionHealth()
        health.begin(0)
        self.assertIsNone(health.observe(1, available=True, speech=False))
        self.assertEqual(health.observe(60, available=True, speech=False)[0], "captions_waiting")
        self.assertIsNone(health.last_speech_at)

    def test_missing_ui_immediately_reports_unavailable(self):
        health = CaptionHealth()
        health.begin(0)
        event, message = health.observe(1, available=False, speech=False)
        self.assertEqual(event, "captions_unavailable")
        self.assertIn("restore Teams", message)

    def test_unavailable_reason_is_descriptive_and_whitespace_normalized(self):
        health = CaptionHealth()
        event, message = health.observe(1, available=False, speech=False, reason="Compact view\n  is visible")
        self.assertEqual(event, "captions_unavailable")
        self.assertEqual(message, "Captions unavailable — Compact view is visible")

    def test_repeated_missing_ui_does_not_spam_events(self):
        health = CaptionHealth()
        self.assertIsNotNone(health.observe(0, available=False, speech=False, reason="Compact view"))
        for now in (1, 60, 120, 600):
            self.assertIsNone(health.observe(now, available=False, speech=False, reason="Compact view"))

    def test_changed_unavailability_reason_is_reported(self):
        health = CaptionHealth()
        health.observe(0, available=False, speech=False, reason="Compact view")
        self.assertIn("on hold", health.observe(1, available=False, speech=False, reason="Meeting is on hold")[1])

    def test_actual_new_speech_reports_receiving(self):
        health = CaptionHealth()
        health.begin(0)
        self.assertEqual(health.observe(1, available=True, speech=True)[0], "captions_receiving")
        self.assertEqual(health.last_speech_at, 1)

    def test_repeated_speech_refreshes_clock_without_event_spam(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        self.assertIsNone(health.observe(50, available=True, speech=True))
        self.assertEqual(health.last_speech_at, 50)
        self.assertIsNone(health.observe(100, available=True, speech=False))

    def test_short_pause_preserves_recent_receiving_status(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        self.assertIsNone(health.observe(59.999, available=True, speech=False))

    def test_sixty_second_pause_is_qualified_not_claimed_broken(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        event, message = health.observe(60, available=True, speech=False)
        self.assertEqual(event, "captions_waiting")
        self.assertIn("60+ seconds", message)
        self.assertIn("may be quiet", message)
        self.assertIn("or captions unavailable", message)

    def test_stale_state_does_not_repeat_every_poll(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        health.observe(60, available=True, speech=False)
        for now in (61, 120, 600):
            self.assertIsNone(health.observe(now, available=True, speech=False))

    def test_old_text_does_not_reset_staleness_clock(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        for now in range(1, 60):
            self.assertIsNone(health.observe(now, available=True, speech=False))
        self.assertEqual(health.last_speech_at, 0)
        self.assertEqual(health.observe(60, available=True, speech=False)[0], "captions_waiting")

    def test_new_speech_resumes_after_stale_pause(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        health.observe(60, available=True, speech=False)
        self.assertEqual(health.observe(65, available=True, speech=True)[0], "captions_receiving")

    def test_recent_old_text_after_hidden_panel_is_not_new_speech(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        health.observe(1, available=False, speech=False)
        self.assertEqual(health.observe(2, available=True, speech=False)[0], "captions_waiting")
        self.assertIsNone(health.observe(3, available=True, speech=False))
        self.assertEqual(health.observe(4, available=True, speech=True)[0], "captions_receiving")

    def test_missing_ui_takes_precedence_over_inconsistent_speech_input(self):
        health = CaptionHealth()
        self.assertEqual(health.observe(0, available=False, speech=True)[0], "captions_unavailable")
        self.assertIsNone(health.last_speech_at)

    def test_begin_new_session_does_not_inherit_prior_speech_or_status(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        self.assertEqual(health.begin(200)[0], "captions_waiting")
        self.assertIsNone(health.last_speech_at)
        self.assertIsNone(health.observe(201, available=True, speech=False))

    def test_reset_allows_fresh_session_without_explicit_begin(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        health.reset()
        self.assertIsNone(health.started_at)
        self.assertIsNone(health.last_speech_at)
        self.assertEqual(health.observe(200, available=True, speech=False)[0], "captions_waiting")
        self.assertIsNone(health.observe(201, available=True, speech=False))

    def test_custom_stale_duration_and_invalid_duration(self):
        health = CaptionHealth(stale_after=5)
        health.begin(0)
        self.assertIn("5+ seconds", health.observe(5, available=True, speech=False)[1])
        for value in (0, -1):
            with self.assertRaises(ValueError):
                CaptionHealth(stale_after=value)

    def test_invalidated_status_reemits_unavailable_after_scan_recovers(self):
        health = CaptionHealth()
        health.begin(0)
        health.observe(1, available=False, speech=False, reason="Panel missing")
        self.assertIsNone(health.observe(2, available=False, speech=False, reason="Panel missing"))
        health.invalidate_status()
        self.assertEqual(
            health.observe(3, available=False, speech=False, reason="Panel missing"),
            ("captions_unavailable", "Captions unavailable — Panel missing"),
        )

    def test_invalidation_keeps_last_speech_time_and_staleness(self):
        health = CaptionHealth()
        health.begin(0)
        health.observe(5, available=True, speech=True)
        health.invalidate_status()
        self.assertEqual(health.started_at, 0)
        self.assertEqual(health.last_speech_at, 5)
        event, message = health.observe(65, available=True, speech=False)
        self.assertEqual(event, "captions_waiting")
        self.assertIn("60+ seconds", message)

    def test_invalidation_requires_fresh_speech_before_receiving(self):
        health = CaptionHealth()
        health.observe(0, available=True, speech=True)
        health.invalidate_status()
        self.assertEqual(health.observe(1, available=True, speech=False)[0], "captions_waiting")
        self.assertEqual(health.observe(2, available=True, speech=True)[0], "captions_receiving")


if __name__ == "__main__":
    unittest.main()
