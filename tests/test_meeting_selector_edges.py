"""Pure meeting-ownership regressions: no UI Automation or live Teams access."""

import unittest

from meeting_presence import (GENERIC_TITLES, PROVISIONAL_TITLES, MeetingSelector,
                              MeetingSurface, WindowIdentity, is_auxiliary_name)


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

    def test_auxiliary_name_matches_only_the_exact_first_title_segment(self):
        for name in ("Sharing control bar", "  SHARING   CONTROL BAR | Microsoft Teams"):
            self.assertTrue(is_auxiliary_name(name))
        for name in ("Sharing control bar planning | Microsoft Teams",
                     "Captions | Sharing control bar | Microsoft Teams", "Planning", "", None):
            self.assertFalse(is_auxiliary_name(name))

    def test_auxiliary_identity_cannot_match_itself_or_an_existing_call(self):
        auxiliary = surface(100, "Sharing control bar").identity
        compact = surface(100, "Compact view").identity
        self.assertFalse(auxiliary.matches(auxiliary))
        for identity in (self.a.identity, compact):
            self.assertFalse(auxiliary.matches(identity))
            self.assertFalse(identity.matches(auxiliary))

    def test_sharing_control_bar_cannot_start_or_steal_a_transcript(self):
        auxiliary = surface(300, "Sharing control bar")
        self.assertFalse(self.select([auxiliary], commit=True).surfaces)
        self.assertFalse(self.selector.current)
        self.select([self.a], commit=True)
        for windows in ([auxiliary, self.a], [self.a, auxiliary]):
            selection = self.select(windows, commit=True)
            self.assertEqual(selection.surfaces, (self.a,))
            self.assertFalse(selection.changed)
            self.assertFalse(selection.paused)
            self.assertFalse(self.selector.allow_caption(auxiliary.identity))

    def test_auxiliary_without_parsed_title_still_cannot_be_captured(self):
        auxiliary = MeetingSurface(WindowIdentity(100, 42, "sharing control bar | microsoft teams", ""))
        self.select([self.a], commit=True)
        self.assertFalse(self.select([auxiliary], commit=True).surfaces)
        self.assertFalse(self.selector.allow_caption(auxiliary.identity))

    def test_auxiliary_only_does_not_keep_a_closed_meeting_alive(self):
        auxiliary = surface(300, "Sharing control bar")
        self.select([self.a], commit=True)
        selection = self.select([], [auxiliary], commit=True)
        self.assertFalse(selection.paused)
        self.assertFalse(selection.surfaces)
        self.assertFalse(self.a.identity.matches(auxiliary.identity))

    def test_auxiliary_phrase_inside_a_real_title_or_caption_viewer_is_not_filtered(self):
        for candidate in (surface(300, "Sharing control bar planning"),
                          surface(301, "Sharing control bar", viewer=True)):
            with self.subTest(name=candidate.identity.name):
                self.selector = MeetingSelector()
                selection = self.select([candidate], commit=True)
                self.assertEqual(selection.surfaces, (candidate,))
                self.assertTrue(self.selector.allow_caption(candidate.identity))

    def test_initial_provisional_title_refines_same_native_main_without_split(self):
        provisional = surface(100, "Meeting join")
        self.assertTrue(PROVISIONAL_TITLES <= GENERIC_TITLES)
        self.select([provisional], commit=True)
        selection = self.select([self.a], commit=True)
        self.assertFalse(selection.changed)
        self.assertFalse(selection.paused)
        self.assertEqual(selection.reason, "provisional_title_refined")
        self.assertEqual(selection.surfaces, (self.a,))
        self.assertEqual(self.selector.current, (self.a,))
        self.assertFalse(self.selector.retired)

    def test_initial_provisional_offscreen_refinement_preserves_lifecycle_not_caption_ownership(self):
        provisional = surface(100, "Meeting join")
        auxiliary = surface(300, "Sharing control bar")
        self.select([provisional], commit=True)
        for _ in range(3):
            selection = self.select([], [self.a, auxiliary], commit=True)
            self.assertTrue(selection.paused)
            self.assertFalse(selection.changed)
            self.assertFalse(selection.surfaces)
            self.assertEqual(selection.reason, "provisional_title_refined")
            self.assertEqual(self.selector.current, (provisional,))
            self.assertFalse(self.selector.allow_caption(self.a.identity))
            self.assertFalse(self.selector.allow_caption(auxiliary.identity))
        selection = self.select([self.a], commit=True)
        self.assertFalse(selection.paused)
        self.assertFalse(selection.changed)
        self.assertEqual(selection.surfaces, (self.a,))
        self.assertTrue(self.selector.allow_caption(self.a.identity))

    def test_provisional_refinement_requires_exact_nonzero_native_identity(self):
        pairs = (
            (surface(100, "Meeting join"), surface(101, "Planning")),
            (surface(100, "Meeting join"), surface(100, "Planning", pid=43)),
            (surface(0, "Meeting join"), surface(0, "Planning")),
            (surface(100, "Meeting join", pid=0), surface(100, "Planning", pid=0)),
        )
        for provisional, named in pairs:
            with self.subTest(old=provisional.identity, new=named.identity):
                self.selector = MeetingSelector()
                self.select([provisional], commit=True)
                self.assertFalse(self.select([], [named]).paused)
                selection = self.select([named], commit=True)
                self.assertTrue(selection.changed)
                self.assertFalse(selection.paused)

    def test_named_call_does_not_lose_identity_through_join_title_before_another_call(self):
        provisional = surface(100, "Meeting join")
        replacement = surface(100, "Budget")
        self.select([self.a], commit=True)
        join = self.select([provisional], commit=True)
        self.assertTrue(join.paused)
        self.assertFalse(join.changed)
        self.assertFalse(self.selector.allow_caption(provisional.identity))
        self.assertFalse(self.select([], [replacement]).paused)
        named = self.select([replacement], commit=True)
        self.assertTrue(named.changed)
        self.assertEqual(named.surfaces, (replacement,))
        self.assertFalse(self.selector.allow_caption(self.a.identity))

    def test_first_offscreen_specific_title_cannot_later_refine_to_a_different_call(self):
        provisional = surface(100, "Meeting join")
        replacement = surface(100, "Budget")
        self.select([provisional], commit=True)
        self.assertTrue(self.select([], [self.a]).paused)
        selection = self.select([replacement], commit=True)
        self.assertTrue(selection.changed)
        self.assertEqual(selection.surfaces, (replacement,))

    def test_different_active_meeting_still_switches_during_provisional_offscreen_refinement(self):
        provisional = surface(100, "Meeting join")
        self.select([provisional], commit=True)
        selection = self.select([self.b], [self.a, self.b], commit=True)
        self.assertTrue(selection.changed)
        self.assertEqual(selection.surfaces, (self.b,))
        self.assertFalse(self.selector.allow_caption(self.a.identity))

    def test_provisional_continuity_ends_when_verified_window_disappears(self):
        provisional = surface(100, "Meeting join")
        home = surface(301, "")
        self.select([provisional], commit=True)
        self.assertTrue(self.select([], [self.a]).paused)
        self.assertFalse(self.select([], [home]).paused)
        self.selector.end_session()
        self.assertFalse(self.select([], [self.a]).paused)

    def test_provisional_refinement_never_uses_a_same_handle_caption_viewer(self):
        provisional = surface(100, "Meeting join")
        viewer = surface(100, "Planning", viewer=True)
        self.select([provisional], commit=True)
        self.assertFalse(self.select([], [viewer]).paused)

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
        self.assertEqual(selection.surfaces, (self.a,))
        self.assertFalse(selection.changed)
        self.assertFalse(self.selector.allow_caption(compact.identity))
        self.assertTrue(self.selector.allow_caption(self.a.identity))

    def test_new_compact_handle_only_preserves_continuity_without_caption_ownership(self):
        compact = surface(300, "Meeting compact view")
        self.select([self.a], commit=True)
        for _ in range(3):
            selection = self.select([compact], commit=True)
            self.assertTrue(selection.paused)
            self.assertFalse(selection.changed)
            self.assertEqual(selection.reason, "compact_continuity")
            self.assertFalse(selection.surfaces)
            self.assertEqual(self.selector.current, (self.a,))
            self.assertFalse(self.selector.allow_caption(compact.identity))
        restored = self.select([self.a, compact], commit=True)
        self.assertFalse(restored.paused)
        self.assertFalse(restored.changed)
        self.assertEqual(restored.surfaces, (self.a,))

    def test_compact_observed_without_meeting_controls_is_continuity_only(self):
        compact = surface(300, "Compact view")
        self.select([self.a], commit=True)
        selection = self.select([], [compact], commit=True)
        self.assertTrue(selection.paused)
        self.assertEqual(selection.reason, "compact_continuity")
        self.assertFalse(selection.surfaces)
        self.assertFalse(self.selector.allow_caption(compact.identity))
        self.assertEqual(self.selector.continuity_compacts, {compact.identity})

    def test_new_compact_does_not_pause_a_still_owned_readable_caption_viewer(self):
        viewer = surface(101, "Planning", viewer=True)
        compact = surface(300, "Compact view")
        self.select([self.a, viewer], commit=True)
        selection = self.select([compact, viewer], commit=True)
        self.assertFalse(selection.paused)
        self.assertFalse(selection.changed)
        self.assertEqual(selection.surfaces, (viewer,))
        self.assertTrue(self.selector.allow_caption(viewer.identity))
        self.assertFalse(self.selector.allow_caption(compact.identity))

    def test_compact_disappearance_does_not_preserve_session_on_teams_home(self):
        compact = surface(300, "Compact view")
        main = surface(301, "")
        self.select([self.a], commit=True)
        self.assertTrue(self.select([], [compact]).paused)
        for _ in range(3):
            selection = self.select([], [main], commit=True)
            self.assertFalse(selection.paused)
            self.assertFalse(selection.surfaces)
            self.assertFalse(self.selector.continuity_compacts)
        self.selector.end_session()
        self.assertFalse(self.select([], [compact]).paused)

    def test_compact_replacement_with_foreign_process_or_missing_identity_is_not_adopted(self):
        for compact in (surface(300, "Compact view", pid=43),
                        surface(0, "Compact view"), surface(300, "Compact view", pid=0)):
            with self.subTest(identity=compact.identity):
                self.selector = MeetingSelector()
                self.select([self.a], commit=True)
                selection = self.select([], [compact], commit=True)
                self.assertFalse(selection.paused)
                self.assertFalse(self.selector.allow_caption(compact.identity))

    def test_compact_visible_before_current_meeting_is_never_borrowed(self):
        compact = surface(300, "Compact view")
        self.select([self.a], [self.a, compact], commit=True)
        selection = self.select([], [compact], commit=True)
        self.assertFalse(selection.paused)
        self.assertFalse(selection.surfaces)

    def test_previously_unrelated_window_cannot_become_compact_continuity(self):
        unrelated = surface(300, "Other call")
        compact = surface(300, "Compact view")
        self.select([self.a], [self.a, unrelated], commit=True)
        self.assertFalse(self.select([], [compact]).paused)

    def test_retired_same_process_call_prevents_borrowing_unknown_compact_surface(self):
        compact = surface(300, "Compact view")
        self.select([self.a], commit=True)
        self.select([self.a, self.b], commit=True)
        selection = self.select([], [compact], commit=True)
        self.assertFalse(selection.paused)
        self.assertFalse(selection.surfaces)
        self.assertFalse(self.selector.allow_caption(compact.identity))

    def test_different_named_call_still_switches_during_compact_continuity(self):
        compact = surface(300, "Compact view")
        self.select([self.a], commit=True)
        self.assertTrue(self.select([], [compact]).paused)
        selection = self.select([self.b], [self.b, compact], commit=True)
        self.assertTrue(selection.changed)
        self.assertFalse(selection.paused)
        self.assertEqual(selection.surfaces, (self.b,))
        self.assertFalse(self.selector.continuity_compacts)
        self.assertFalse(self.selector.allow_caption(compact.identity))

    def test_unique_recreated_named_window_after_compact_keeps_same_session(self):
        compact = surface(300, "Compact view")
        recreated = surface(101, "Planning")
        self.select([self.a], commit=True)
        self.assertTrue(self.select([], [compact]).paused)
        selection = self.select([recreated], commit=True)
        self.assertFalse(selection.changed)
        self.assertFalse(selection.paused)
        self.assertEqual(selection.surfaces, (recreated,))

    def test_same_title_overlap_and_unowned_survivor_pause_instead_of_splitting_or_merging(self):
        other = surface(102, "Planning")
        self.select([self.a], commit=True)
        for active in ([self.a, other], [other, self.a], [other]):
            selection = self.select(active, commit=True)
            self.assertTrue(selection.paused)
            self.assertTrue(selection.ambiguous)
            self.assertFalse(selection.changed)
            self.assertFalse(selection.surfaces)
            self.assertEqual(selection.reason, "same_title_ambiguity")
            self.assertFalse(self.selector.allow_caption(other.identity))
        restored = self.select([self.a], commit=True)
        self.assertFalse(restored.paused)
        self.assertFalse(restored.changed)
        self.assertEqual(restored.surfaces, (self.a,))

    def test_minimized_same_title_owner_and_another_main_pause_without_lending_viewer(self):
        other = surface(102, "Planning")
        viewer = surface(103, "Planning", viewer=True)
        self.select([self.a], commit=True)
        selection = self.select([other, viewer], [self.a, other, viewer], commit=True)
        self.assertTrue(selection.paused)
        self.assertEqual(selection.reason, "same_title_ambiguity")
        self.assertFalse(self.selector.allow_caption(other.identity))
        self.assertFalse(self.selector.allow_caption(viewer.identity))

    def test_same_title_survivor_can_start_new_capture_only_after_session_closure(self):
        other = surface(102, "Planning")
        self.select([self.a], commit=True)
        self.assertTrue(self.select([self.a, other]).paused)
        self.assertFalse(self.select([], []).paused)
        self.selector.end_session()
        selection = self.select([other], commit=True)
        self.assertFalse(selection.changed)
        self.assertFalse(selection.paused)
        self.assertEqual(selection.surfaces, (other,))

    def test_missing_native_handle_cannot_claim_a_same_title_ambiguous_survivor(self):
        initial = surface(0, "Planning")
        other = surface(102, "Planning")
        self.select([initial], commit=True)
        overlap = self.select([initial, other], commit=True)
        self.assertTrue(overlap.paused)
        survivor = self.select([other], commit=True)
        self.assertTrue(survivor.paused)
        self.assertFalse(survivor.surfaces)
        self.assertFalse(self.selector.allow_caption(other.identity))

    def test_previously_owned_viewer_cannot_bypass_same_title_ambiguity_via_fallback(self):
        viewer = surface(101, "Planning", viewer=True)
        other = surface(102, "Planning")
        self.select([self.a, viewer], commit=True)
        self.assertTrue(self.selector.allow_caption(viewer.identity))
        self.assertTrue(self.select([self.a, other, viewer], commit=True).paused)
        self.select([viewer], [other, viewer], commit=True)
        self.assertFalse(self.selector.allow_caption(viewer.identity))
        self.assertFalse(self.selector.allow_caption(other.identity))
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
