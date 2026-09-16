"""Pure meeting-ownership regressions: no UI Automation or live Teams access."""

import unittest

from meeting_presence import MeetingSelector, MeetingSurface, WindowIdentity


def surface(hwnd, title, *, viewer=False, held=False, pid=42):
    name = f"Captions | {title} | Microsoft Teams" if viewer else f"{title} | Microsoft Teams"
    return MeetingSurface(WindowIdentity(hwnd, pid, name.casefold(), title.casefold()),
                          viewer=viewer, held=held)


class SelectorEdgeTests(unittest.TestCase):
    def setUp(self):
        self.selector = MeetingSelector()
        self.a = surface(100, "Planning")
        self.b = surface(200, "Budget")

    def select(self, surfaces, observed=None, *, commit=False):
        identities = [item.identity for item in (surfaces if observed is None else observed)]
        selection = self.selector.select(surfaces, identities)
        if commit:
            self.selector.commit(selection)
        return selection

    def test_new_detached_viewer_keeps_minimized_current_main_in_same_session(self):
        self.select([self.a], commit=True)
        viewer = surface(101, "Planning", viewer=True)
        # The main is still observed, but is offscreen or has no call controls.
        selection = self.select([viewer], [self.a, viewer], commit=True)
        self.assertFalse(selection.changed)
        self.assertFalse(selection.ambiguous)
        self.assertIn(viewer, selection.surfaces)
        self.assertTrue(self.selector.allow_caption(self.a.identity))
        self.assertTrue(self.selector.allow_caption(viewer.identity))
        restored = self.select([viewer, self.a], commit=True)
        self.assertFalse(restored.changed)
        self.assertIn(self.a, restored.surfaces)

    def test_detached_viewer_cannot_be_adopted_when_same_title_has_two_main_owners(self):
        other = surface(102, "Planning")
        viewer = surface(101, "Planning", viewer=True)
        self.select([self.a], commit=True)
        self.select([self.a, other, viewer], commit=True)
        self.assertFalse(self.selector.allow_caption(viewer.identity))
        selection = self.select([viewer], [self.a, other, viewer])
        self.assertNotIn(viewer, selection.surfaces)

    def test_unique_generic_joined_main_can_own_its_matching_detached_viewer(self):
        for title in ("Team meeting", "Meeting"):
            with self.subTest(title=title):
                self.selector = MeetingSelector()
                main = surface(300, title)
                viewer = surface(301, title, viewer=True)
                selection = self.select([main, viewer], commit=True)
                self.assertEqual(set(selection.surfaces), {main, viewer})
                self.assertFalse(selection.ambiguous)
                self.assertTrue(self.selector.allow_caption(viewer.identity))

    def test_generic_viewer_without_a_confirmed_owner_does_not_start_capture(self):
        viewer = surface(301, "Team meeting", viewer=True)
        selection = self.select([viewer], commit=True)
        self.assertFalse(selection.surfaces)
        self.assertFalse(self.selector.current)

    def test_two_generic_joined_calls_never_share_the_detached_viewer(self):
        a, b = surface(300, "Team meeting"), surface(302, "Team meeting")
        viewer = surface(301, "Team meeting", viewer=True)
        selection = self.select([a, b, viewer], commit=True)
        self.assertTrue(selection.ambiguous)
        self.assertFalse(selection.surfaces)
        self.assertFalse(self.selector.allow_caption(viewer.identity))

    def test_initial_multiple_unheld_calls_wait_for_a_unique_owner(self):
        selection = self.select([self.a, self.b], commit=True)
        self.assertTrue(selection.ambiguous)
        self.assertFalse(self.selector.current)
        selection = self.select([self.b], commit=True)
        self.assertEqual(selection.surfaces, (self.b,))
        self.assertFalse(selection.changed)
        self.assertFalse(self.selector.allow_caption(self.a.identity))

    def test_initial_held_call_does_not_block_the_unique_unheld_call(self):
        held_a = surface(100, "Planning", held=True)
        selection = self.select([held_a, self.b], commit=True)
        self.assertEqual(selection.surfaces, (self.b,))
        self.assertFalse(selection.held)
        self.assertFalse(self.selector.allow_caption(held_a.identity))

    def test_late_detached_retired_viewer_cannot_steal_the_new_call_after_grace(self):
        self.select([self.a], commit=True)
        self.select([self.a, self.b], commit=True)
        viewer = surface(101, "Planning", viewer=True)
        for now in (0, 9, 3600):
            self.selector.observe_retired([self.b.identity, viewer.identity], lambda _: False, now, 8)
            selection = self.select([viewer, self.b], commit=True)
            self.assertEqual(selection.surfaces, (self.b,))
            self.assertFalse(selection.changed)
            self.assertFalse(self.selector.allow_caption(viewer.identity))

    def test_retired_call_is_eligible_again_only_after_confirmed_absence(self):
        self.select([self.a], commit=True)
        self.select([self.a, self.b], commit=True)
        for now in (0, 7.99):
            self.selector.observe_retired([self.b.identity], lambda _: False, now, 8)
            self.assertIn(self.a.identity, self.selector.retired)
        self.selector.observe_retired([self.b.identity], lambda _: False, 8, 8)
        self.assertNotIn(self.a.identity, self.selector.retired)
        selection = self.select([self.b, self.a], commit=True)
        self.assertTrue(selection.changed)
        self.assertEqual(selection.surfaces, (self.a,))
        self.assertFalse(self.selector.allow_caption(self.b.identity))

    def test_observed_retired_window_without_call_controls_never_counts_as_closed(self):
        self.select([self.a], commit=True)
        self.select([self.a, self.b], commit=True)
        for now in (0, 30, 3600):
            self.selector.observe_retired([self.a.identity, self.b.identity], lambda _: False, now, 8)
            selection = self.select([self.b], [self.a, self.b])
            self.assertEqual(selection.surfaces, (self.b,))
            self.assertIn(self.a.identity, self.selector.retired)

    def test_uncertain_scan_restarts_retired_absence_confirmation(self):
        self.select([self.a], commit=True)
        self.select([self.a, self.b], commit=True)
        self.selector.observe_retired([self.b.identity], lambda _: False, 0, 8)
        self.selector.uncertain()
        self.selector.observe_retired([self.b.identity], lambda _: False, 100, 8)
        self.assertIn(self.a.identity, self.selector.retired)
        self.selector.observe_retired([self.b.identity], lambda _: False, 108, 8)
        self.assertNotIn(self.a.identity, self.selector.retired)

    def test_compact_transition_then_original_name_stays_the_same_session(self):
        compact = surface(100, "Meeting compact view")
        self.select([self.a], commit=True)
        selection = self.select([compact], commit=True)
        self.assertFalse(selection.changed)
        self.assertTrue(self.selector.allow_caption(compact.identity))
        selection = self.select([self.a], commit=True)
        self.assertFalse(selection.changed)
        self.assertEqual(selection.surfaces, (self.a,))

    def test_initial_generic_main_name_refinement_keeps_exact_native_owner(self):
        for title in ("Team meeting", "Meeting"):
            with self.subTest(title=title):
                self.selector = MeetingSelector()
                generic = surface(100, title)
                self.select([generic], commit=True)
                selection = self.select([self.a], commit=True)
                self.assertFalse(selection.changed)
                self.assertFalse(selection.ambiguous)
                self.assertEqual(selection.surfaces, (self.a,))
                self.assertTrue(self.selector.allow_caption(self.a.identity))

    def test_held_generic_main_replaced_by_named_call_is_not_name_refinement(self):
        generic = surface(100, "Team meeting")
        held_generic = surface(100, "Team meeting", held=True)
        self.select([generic], commit=True)
        self.select([held_generic], commit=True)
        selection = self.select([self.a], commit=True)
        self.assertTrue(selection.changed)
        self.assertEqual(selection.surfaces, (self.a,))
        self.assertFalse(self.selector.allow_caption(generic.identity))

    def test_generic_name_refinement_requires_the_same_window_and_process(self):
        pairs = (
            (surface(100, "Team meeting"), surface(101, "Planning")),
            (surface(100, "Team meeting"), surface(100, "Planning", pid=43)),
            (surface(100, "Team meeting", pid=0), surface(100, "Planning", pid=0)),
            (surface(0, "Team meeting"), surface(0, "Planning")),
        )
        for generic, replacement in pairs:
            with self.subTest(old=generic.identity, new=replacement.identity):
                self.selector = MeetingSelector()
                self.select([generic], commit=True)
                selection = self.select([replacement], commit=True)
                self.assertTrue(selection.changed)
                self.assertFalse(self.selector.allow_caption(generic.identity))

    def test_compact_hwnd_reused_for_a_different_named_call_requires_a_switch(self):
        compact = surface(100, "Meeting compact view")
        replacement = surface(100, "Budget")
        self.select([self.a], commit=True)
        self.select([compact], commit=True)
        selection = self.select([replacement], commit=True)
        self.assertTrue(selection.changed)
        self.assertEqual(selection.surfaces, (replacement,))
        self.assertFalse(self.selector.allow_caption(self.a.identity))

    def test_unrelated_compact_hwnd_is_not_attached_to_the_current_call(self):
        compact = surface(300, "Meeting compact view")
        self.select([self.a], commit=True)
        selection = self.select([self.a, compact], commit=True)
        self.assertTrue(selection.ambiguous)
        self.assertFalse(self.selector.allow_caption(compact.identity))
        self.assertTrue(self.selector.allow_caption(self.a.identity))

    def test_failed_save_does_not_commit_or_consume_resume_evidence(self):
        held_a = surface(100, "Planning", held=True)
        held_b = surface(200, "Budget", held=True)
        self.select([self.a], commit=True)
        self.select([held_a, self.b], commit=True)
        proposal = self.select([self.a, held_b])
        self.assertTrue(proposal.changed)
        self.assertEqual(proposal.surfaces, (self.a,))
        self.assertTrue(self.selector.allow_caption(self.b.identity))
        self.assertFalse(self.selector.allow_caption(self.a.identity))
        retry = self.select([held_b, self.a])
        self.assertEqual(retry, proposal)
        self.selector.commit(retry)
        self.assertTrue(self.selector.allow_caption(self.a.identity))
        self.assertFalse(self.selector.allow_caption(self.b.identity))

    def test_proven_resume_waits_until_other_call_has_confirmed_closed(self):
        held_a = surface(100, "Planning", held=True)
        self.select([self.a], commit=True)
        self.select([held_a, self.b], commit=True)
        self.assertFalse(self.select([self.a]).surfaces)
        self.assertTrue(self.selector.allow_caption(self.b.identity))
        # Integration calls this only after lifecycle grace and a successful save.
        self.selector.end_session()
        selection = self.select([self.a], commit=True)
        self.assertEqual(selection.surfaces, (self.a,))
        self.assertFalse(selection.changed)
        self.assertTrue(self.selector.allow_caption(self.a.identity))


if __name__ == "__main__":
    unittest.main()
