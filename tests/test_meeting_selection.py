"""Synthetic meeting ownership regressions; never inspect the real desktop."""
import ctypes
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import teams_caption_notes as capture
from meeting_presence import MeetingSelector, MeetingSurface, WindowIdentity
import test_meeting_continuity as continuity
from test_meeting_continuity import meeting
from test_teams_caption_notes import FakeControl


def call(name, hwnd, token, *, held=False, viewer=False):
    window = meeting(name=("Captions | " if viewer else "") + name, hwnd=hwnd,
                     controls=not viewer)
    window.test_caption = token
    if held:
        window._children.append(FakeControl("Live captions are paused while on hold", "TextControl"))
    return window


def captions(window, *_):
    return ["Synthetic: " + window.test_caption] if window.test_caption else []


class SelectionIntegrationTests(unittest.TestCase):
    run_scans = continuity.ContinuityTests.run_scans
    def outputs(self, scans):
        _, events, saved = self.run_scans(scans, captions=captions, single=False)
        return [item.args[0].read_text(encoding="utf-8") for item in saved.call_args_list], events, saved

    def test_overlapping_named_calls_are_separate_in_either_enumeration_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                a, b = call("Alpha", 100, "alpha original"), call("Beta", 200, "beta only")
                overlap = [a, b] if reverse else [b, a]
                documents, events, _ = self.outputs([[a], overlap, list(reversed(overlap)), [b], [], []])
                self.assertEqual(events.count("meeting_started"), 2)
                self.assertEqual(len(documents), 2)
                self.assertIn("alpha original", documents[0])
                self.assertNotIn("beta only", documents[0])
                self.assertIn("beta only", documents[1])
                self.assertNotIn("alpha original", documents[1])

    def test_held_old_call_and_detached_viewer_cannot_capture_or_relabel_new_call(self):
        a = call("Alpha", 100, "alpha original")
        held_a = call("Alpha", 100, "old held speech", held=True)
        old_viewer = call("Alpha", 101, "stale viewer text", viewer=True, held=True)
        b, viewer_b = call("Beta", 200, "beta builtin"), call("Beta", 201, "beta viewer", viewer=True)
        documents, _, _ = self.outputs([[a], [old_viewer, b, held_a, viewer_b],
                                        [viewer_b, old_viewer, held_a, b], [], []])
        self.assertEqual(len(documents), 2)
        self.assertIn("— Beta", documents[1])
        self.assertIn("beta viewer", documents[1])
        self.assertNotIn("stale viewer", "".join(documents))
        self.assertNotIn("old held", "".join(documents))
        self.assertNotIn("beta builtin", "".join(documents))

    def test_resume_switch_creates_third_file_without_replaying_retired_call(self):
        a = call("Alpha", 100, "alpha original")
        held_a = call("Alpha", 100, "held alpha", held=True)
        b = call("Beta", 200, "beta original")
        held_b = call("Beta", 200, "held beta", held=True)
        resumed_a = call("Alpha", 100, "alpha resumed")
        documents, events, saved = self.outputs([[a], [held_a, b], [b, held_a],
                                                [held_b, resumed_a], [resumed_a, held_b], [], []])
        self.assertEqual(events.count("meeting_started"), 3)
        self.assertEqual(len({entry.args[0] for entry in saved.call_args_list}), 3)
        self.assertIn("alpha original", documents[0])
        self.assertNotIn("beta original", documents[0])
        self.assertIn("beta original", documents[1])
        self.assertNotIn("alpha resumed", documents[1])
        self.assertIn("alpha resumed", documents[2])
        self.assertNotIn("beta original", documents[2])

    def test_resumed_held_call_waits_for_other_call_closure_then_starts_again(self):
        a, b = call("Alpha", 100, "alpha original"), call("Beta", 200, "beta original")
        held_a = call("Alpha", 100, "held alpha", held=True)
        resumed_a = call("Alpha", 100, "alpha resumed")
        documents, events, _ = self.outputs([[a], [held_a, b], [resumed_a], [resumed_a],
                                            [resumed_a], [], []])
        self.assertEqual(events.count("meeting_started"), 3)
        self.assertIn("alpha resumed", documents[2])
        self.assertNotIn("alpha resumed", documents[1])

    def test_hold_alone_preserves_session_without_status_text_or_speech(self):
        a, held = call("Alpha", 100, "before hold"), call("Alpha", 100, "must not capture", held=True)
        after = call("Alpha", 100, "after hold")
        documents, events, _ = self.outputs([[a], [held], [held], [after], [], []])
        self.assertEqual(events.count("meeting_started"), 1)
        self.assertIn("before hold", documents[0])
        self.assertIn("after hold", documents[0])
        self.assertNotIn("must not capture", documents[0])

    def test_simultaneous_same_name_calls_exclude_unassignable_detached_viewer(self):
        a, b = call("Standup", 100, "first call"), call("Standup", 200, "second call")
        viewer = call("Standup", 300, "ambiguous viewer", viewer=True)
        documents, events, _ = self.outputs([[a], [a, viewer, b], [b, viewer, a],
                                            [viewer, b], [], []])
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertIn("first call", documents[0])
        self.assertNotIn("second call", documents[0])
        self.assertIn("second call", documents[1])
        self.assertNotIn("ambiguous viewer", "".join(documents))

    def test_late_stale_viewer_does_not_steal_back_after_old_main_disappears(self):
        a, b = call("Alpha", 100, "alpha original"), call("Beta", 200, "beta original")
        late = call("Alpha", 300, "stale detached alpha", viewer=True)
        documents, events, _ = self.outputs([[a], [a, b]] + [[late, b], [b, late]] * 4 + [[late], [late], [late]])
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertEqual(len(documents), 2)
        self.assertNotIn("stale detached", "".join(documents))

    def test_hidden_same_name_old_main_does_not_lend_its_viewer_to_new_call(self):
        a, b = call("Standup", 100, "first call"), call("Standup", 200, "second call")
        viewer = call("Standup", 300, "first viewer", viewer=True)
        hidden_a = call("Standup", 100, "hidden old call")
        hidden_a.IsOffscreen = True
        documents, events, _ = self.outputs([[a, viewer], [hidden_a, b, viewer], [b, viewer], [], []])
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertIn("first viewer", documents[0])
        self.assertIn("second call", documents[1])
        self.assertNotIn("first viewer", documents[1])

    def test_compact_with_associated_detached_viewer_preserves_capture(self):
        a = call("Alpha", 100, "builtin")
        compact = call("Meeting compact view", 100, "compact builtin")
        before = call("Alpha", 101, "before compact", viewer=True)
        during = call("Alpha", 101, "during compact", viewer=True)
        after = call("Alpha", 101, "after compact", viewer=True)
        documents, events, _ = self.outputs([[a, before], [compact, during], [a, after], [], []])
        self.assertEqual(events.count("meeting_started"), 1)
        self.assertIn("during compact", documents[0])
        self.assertIn("after compact", documents[0])

    def test_compact_reused_for_new_named_meeting_does_not_match_previous_call(self):
        a = call("Alpha", 100, "alpha original")
        compact = call("Meeting compact view", 100, "alpha compact")
        b = call("Beta", 100, "beta new")
        documents, events, _ = self.outputs([[a], [compact], [b], [], []])
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertIn("alpha compact", documents[0])
        self.assertNotIn("beta new", documents[0])
        self.assertIn("beta new", documents[1])

    def test_initial_multiple_calls_wait_without_combining(self):
        a, b = call("Alpha", 100, "alpha original"), call("Beta", 200, "beta original")
        documents, events, _ = self.outputs([[a, b], [b, a]])
        self.assertEqual(documents, [])
        self.assertNotIn("meeting_started", events)
        self.assertIn("meeting_visibility_lost", events)
        self.assertNotIn("recording", events)

    def test_com_failure_during_switch_never_captures_new_call_under_old(self):
        a, b = call("Alpha", 100, "alpha original"), call("Beta", 200, "beta original")
        error = ctypes.COMError(-2147220991, "synthetic scan failure", None)
        with patch.object(capture.LOG, "warning"):
            documents, events, _ = self.outputs([[a], error, [b, a], [b], [], []])
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertNotIn("beta original", documents[0])
        self.assertIn("beta original", documents[1])

    def test_held_after_com_recovery_never_reports_recording(self):
        a, held = call("Alpha", 100, "alpha original"), call("Alpha", 100, "held", held=True)
        error = ctypes.COMError(-2147220991, "synthetic scan failure", None)
        with patch.object(capture.LOG, "warning"):
            _, events, _ = self.outputs([[a], [held], error, [held], [held]])
        self.assertNotIn("recording", events)

    def test_failed_old_transcript_save_does_not_commit_switch_or_ingest_foreign_text(self):
        a = call("Alpha", 100, "alpha original")
        b_first = call("Beta", 200, "beta skipped during failed save")
        b_next = call("Beta", 200, "beta after successful save")
        actual_write = capture.atomic_write
        writes = 0

        def fail_first_final_save(path, content):
            nonlocal writes
            writes += 1
            if writes == 3:
                raise PermissionError("synthetic write failure")
            return actual_write(path, content)

        with patch.object(capture, "atomic_write", side_effect=fail_first_final_save), patch.object(
            capture, "recovery_destinations", return_value=[]
        ), patch.object(capture.LOG, "warning"):
            documents, events, _ = self.outputs([[a], [a, b_first], [b_next, a], [], []])
        self.assertEqual(events.count("meeting_started"), 2)
        self.assertIn("write_blocked", events)
        self.assertIn("alpha original", documents[0])
        self.assertNotIn("beta", documents[0])
        self.assertIn("beta after successful save", documents[1])
        self.assertNotIn("beta skipped", "".join(documents))


class SelectionUnitTests(unittest.TestCase):
    def test_hold_status_is_never_parsed_as_speech_with_or_without_speaker(self):
        status = "Live captions are paused while on hold"
        for value in (status, "Synthetic, Speaker\n" + status, "Synthetic: " + status):
            with self.subTest(value=value):
                self.assertIsNone(capture.parse_caption(value))

    def test_selection_is_not_committed_before_old_transcript_saves(self):
        a = MeetingSurface(WindowIdentity(100, 42, "alpha | teams", "alpha"))
        b = MeetingSurface(WindowIdentity(200, 42, "beta | teams", "beta"))
        selector = MeetingSelector()
        selector.commit(selector.select([a], [a.identity]))
        for _ in range(3):
            proposed = selector.select([a, b], [a.identity, b.identity])
            self.assertTrue(proposed.changed)
            self.assertEqual(selector.current, (a,))
            self.assertFalse(selector.allow_caption(b.identity))
        selector.commit(proposed)
        self.assertTrue(selector.allow_caption(b.identity))
        self.assertFalse(selector.allow_caption(a.identity))

    def test_same_second_destinations_never_overwrite_previous_session(self):
        instant = datetime(2026, 1, 2, 3, 4, 5)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for requested in (None, folder / "explicit.md"):
                paths = []
                for session in range(1, 5):
                    path = capture.session_destination(requested, instant, session, "Standup", folder)
                    path.write_text("synthetic saved transcript", encoding="utf-8")
                    paths.append(path)
                self.assertEqual(len(set(paths)), 4)
                self.assertTrue(all(path.read_text() == "synthetic saved transcript" for path in paths))


if __name__ == "__main__":
    unittest.main()
