import itertools
import threading
import types
import unittest
from unittest.mock import patch

import caption_setup as setup


class Pattern:
    def __init__(self, state=0, callback=None, selected=False):
        self.ToggleState = state
        self.IsSelected = selected
        self.calls = 0
        self.callback = callback

    def Toggle(self, waitTime=0):
        self.calls += 1
        if self.callback:
            self.callback()
        else:
            self.ToggleState = 1 - self.ToggleState
        return True

    def Invoke(self, waitTime=0):
        self.calls += 1
        if self.callback:
            self.callback()
        return True

    def Select(self, waitTime=0):
        self.IsSelected = True
        return self.Invoke(waitTime)

    def Expand(self, waitTime=0):
        self.ExpandCollapseState = 1
        return self.Invoke(waitTime)


class Node:
    serial = itertools.count(1)

    def __init__(self, name="", kind="PaneControl", children=(), pid=77, patterns=None,
                 enabled=True, visible=True, label=None):
        self.Name = name
        self.ControlTypeName = kind
        self.ProcessId = pid
        self.NativeWindowHandle = 0
        self.IsEnabled = enabled
        self.IsOffscreen = not visible
        self.patterns = patterns or {}
        self.identity = next(self.serial)
        self.Element = types.SimpleNamespace(CurrentLabeledBy=label)
        self.parent = None
        self.set_children(children)

    def set_children(self, children):
        self.children = list(children)
        for child in self.children:
            child.parent = self

    def GetRuntimeId(self):
        return [42, self.identity]

    def GetFirstChildControl(self):
        return self.children[0] if self.children else None

    def GetNextSiblingControl(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        index = siblings.index(self) + 1
        return siblings[index] if index < len(siblings) else None

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)


class Auto:
    Control = types.SimpleNamespace(CreateControlFromElement=lambda value: value)

    def __init__(self, windows):
        self.desktop = Node("Desktop", children=windows, pid=1)

    def GetRootControl(self):
        return self.desktop


def toggle(name="Always show captions in my calls and meetings", state=0, **kwargs):
    pattern = Pattern(state=state)
    return Node(name, "CheckBoxControl", patterns={10015: pattern}, **kwargs), pattern


def accessibility(children, selected=True):
    selected_pattern = Pattern(selected=selected)
    return Node("Microsoft Teams", children=[
        Node("General", "TabItemControl", patterns={10010: Pattern()}),
        Node("Accessibility", "TabItemControl", patterns={10010: selected_pattern}),
        *children,
    ])


def button(name, callback=None, kind="ButtonControl", **kwargs):
    pattern = Pattern(callback=callback)
    return Node(name, kind, patterns={10000: pattern}, **kwargs), pattern


def meeting(children):
    return Node("Meeting — Microsoft Teams", children=[
        button("Leave")[0], button("Mic")[0], *children,
    ])


class CaptionSetupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.images = patch.object(setup, "process_image_name", side_effect=lambda pid: "ms-teams.exe" if pid == 77 else "notepad.exe").start()
        patch.object(setup._Session, "pause", lambda session: session.check()).start()

    def test_persistent_off_is_toggled_once_and_verified_on(self):
        control, pattern = toggle()
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertEqual(result.state, "enabled")
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 1)
        self.assertEqual(pattern.ToggleState, 1)

    def test_persistent_on_is_never_toggled_off(self):
        control, pattern = toggle(state=1)
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertEqual(result.state, "already_enabled")
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_adjacent_identification_and_profanity_settings_are_untouched(self):
        caption, caption_pattern = toggle()
        identity, identity_pattern = toggle("Automatically identify me in meeting captions and transcripts")
        profanity, profanity_pattern = toggle("Filter profane words in meeting captions")
        result = setup.enable_always_show(Auto([accessibility([identity, caption, profanity])]))
        self.assertTrue(result.verified)
        self.assertEqual(caption_pattern.calls, 1)
        self.assertEqual(identity_pattern.calls, 0)
        self.assertEqual(profanity_pattern.calls, 0)

    def test_known_exact_legacy_caption_label_is_supported(self):
        control, pattern = toggle("Always show captions in my meetings")
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 1)

    def test_explicit_current_labeledby_relationship_is_supported(self):
        label = Node("Always show captions in my calls and meetings", "TextControl")
        control, pattern = toggle(name="", label=label)
        result = setup.enable_always_show(Auto([accessibility([label, control])]))
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 1)

    def test_adjacent_text_without_labeledby_never_selects_toggle(self):
        label = Node("Always show captions in my calls and meetings", "TextControl")
        control, pattern = toggle(name="")
        result = setup.enable_always_show(Auto([accessibility([label, control])]))
        self.assertEqual(result.state, "unavailable")
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_caption_like_substring_never_matches(self):
        control, pattern = toggle("Automatically identify me — always show captions in my calls and meetings")
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_disabled_and_indeterminate_toggles_fail_closed(self):
        for state, enabled in ((0, False), (1, False), (2, True)):
            with self.subTest(state=state, enabled=enabled):
                control, pattern = toggle(state=state, enabled=enabled)
                result = setup.enable_always_show(Auto([accessibility([control])]))
                self.assertFalse(result.verified)
                self.assertEqual(pattern.calls, 0)

    def test_duplicate_caption_controls_fail_closed(self):
        first, a = toggle()
        second, b = toggle()
        result = setup.enable_always_show(Auto([accessibility([first, second])]))
        self.assertEqual(result.state, "unavailable")
        self.assertEqual(a.calls + b.calls, 0)

    def test_multiple_settings_windows_fail_closed(self):
        first, a = toggle()
        second, b = toggle()
        result = setup.enable_always_show(Auto([accessibility([first]), accessibility([second])]))
        self.assertFalse(result.verified)
        self.assertEqual(a.calls + b.calls, 0)

    def test_spoofed_teams_title_in_other_process_is_rejected(self):
        control, pattern = toggle()
        root = accessibility([control])
        root.ProcessId = 999
        result = setup.enable_always_show(Auto([root]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_child_webview_process_is_allowed_only_inside_verified_teams_root(self):
        control, pattern = toggle(pid=888)
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 1)

    def test_changed_root_image_prevents_mutation(self):
        control, pattern = toggle()
        self.images.side_effect = ["ms-teams.exe", "ms-teams.exe", "notepad.exe"]
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_cancellation_before_start_has_no_actions(self):
        control, pattern = toggle()
        cancel = threading.Event()
        cancel.set()
        result = setup.enable_always_show(Auto([accessibility([control])]), cancel)
        self.assertEqual(result.state, "cancelled")
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)
        self.images.assert_not_called()

    def test_missing_toggle_pattern_fails_closed(self):
        control, pattern = toggle()
        control.patterns.clear()
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_failed_verification_does_not_toggle_repeatedly(self):
        control, pattern = toggle()
        pattern.callback = lambda: None
        result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertEqual(result.state, "unverified")
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 1)

    def test_exception_after_toggle_attempt_is_unverified_and_not_retried(self):
        control, pattern = toggle()
        def fail():
            raise RuntimeError("private unrelated contents")
        pattern.callback = fail
        with self.assertLogs(level="INFO") as logs:
            result = setup.enable_always_show(Auto([accessibility([control])]))
        self.assertEqual(result.state, "unverified")
        self.assertEqual(pattern.calls, 1)
        self.assertNotIn("private unrelated contents", str(logs.output))
        self.assertNotIn(control.Name, str(logs.output))

    def test_semantic_settings_navigation_refreshes_then_enables(self):
        caption, pattern = toggle()
        root = Node("Microsoft Teams")
        access_pattern = Pattern(callback=lambda: root.set_children([general, access, caption]))
        general = Node("General", "TabItemControl", patterns={10010: Pattern(selected=True)})
        access = Node("Accessibility", "TabItemControl", patterns={10010: access_pattern})
        settings, settings_pattern = button("Settings", lambda: root.set_children([general, access]), "MenuItemControl")
        more, more_pattern = button("Settings and more", lambda: root.set_children([more, settings]))
        root.set_children([more])
        result = setup.enable_always_show(Auto([root]))
        self.assertEqual(result.state, "enabled")
        self.assertEqual([more_pattern.calls, settings_pattern.calls, access_pattern.calls, pattern.calls], [1, 1, 1, 1])

    def test_disabled_settings_navigation_is_not_invoked(self):
        more, pattern = button("Settings and more", enabled=False)
        result = setup.enable_always_show(Auto([Node("Microsoft Teams", children=[more])]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_current_meeting_show_command_and_verify_hide(self):
        root = meeting([])
        hide, hide_pattern = button("Hide live captions")
        show, pattern = button("Show live captions", lambda: root.set_children([*root.children[:2], hide]))
        root.set_children([*root.children, show])
        result = setup.enable_current_meeting(Auto([root]))
        self.assertEqual(result.state, "enabled")
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 1)
        self.assertEqual(hide_pattern.calls, 0)

    def test_current_hide_command_is_never_invoked(self):
        hide, pattern = button("Hide live captions (Alt+Shift+C)")
        result = setup.enable_current_meeting(Auto([meeting([hide])]))
        self.assertEqual(result.state, "already_enabled")
        self.assertTrue(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_pinned_caption_viewer_alone_is_not_a_joined_meeting(self):
        show, pattern = button("Show live captions")
        root = Node("Captions | Microsoft Teams — Pinned window", children=[show, Node("Live captions")])
        result = setup.enable_current_meeting(Auto([root]))
        self.assertEqual(result.state, "unavailable")
        self.assertEqual(pattern.calls, 0)

    def test_ambiguous_two_joined_meetings_are_not_changed(self):
        first, a = button("Show live captions")
        second, b = button("Show live captions")
        result = setup.enable_current_meeting(Auto([meeting([first]), meeting([second])]))
        self.assertFalse(result.verified)
        self.assertEqual(a.calls + b.calls, 0)

    def test_current_menu_navigation_only_opens_language_and_captions(self):
        root = meeting([])
        base = list(root.children)
        record, recording_pattern = button("Start recording")
        transcribe, transcript_pattern = button("Start transcription")
        hide, hide_pattern = button("Hide live captions")
        show, show_pattern = button("Show live captions", lambda: root.set_children([*base, hide]))
        language, language_pattern = button("Language and speech", lambda: root.set_children([*base, show]))
        more, more_pattern = button("More actions", lambda: root.set_children([*base, language, record, transcribe]))
        root.set_children([*base, more])
        result = setup.enable_current_meeting(Auto([root]))
        self.assertEqual(result.state, "enabled")
        self.assertEqual([more_pattern.calls, language_pattern.calls, show_pattern.calls], [1, 1, 1])
        self.assertEqual(recording_pattern.calls + transcript_pattern.calls + hide_pattern.calls, 0)

    def test_current_caption_pane_verifies_without_exposing_caption_text(self):
        result = setup.enable_current_meeting(Auto([meeting([Node("Live captions", children=[Node("Private spoken content", "TextControl")])])]))
        self.assertTrue(result.verified)
        self.assertNotIn("Private spoken content", result.message)

    def test_current_missing_confirmation_is_unverified(self):
        show, pattern = button("Show live captions")
        result = setup.enable_current_meeting(Auto([meeting([show])]))
        self.assertEqual(result.state, "unverified")
        self.assertEqual(pattern.calls, 1)

    def test_navigation_can_expand_known_more_menu_without_invoking(self):
        root = meeting([])
        base = list(root.children)
        hide, _ = button("Hide live captions")
        show, shown = button("Show live captions", lambda: root.set_children([*base, hide]))
        expanded = Pattern(callback=lambda: root.set_children([*base, show]))
        expanded.ExpandCollapseState = 0
        forbidden_invoke = Pattern()
        more = Node("More actions", "ButtonControl", patterns={10005: expanded, 10000: forbidden_invoke})
        root.set_children([*base, more])
        result = setup.enable_current_meeting(Auto([root]))
        self.assertTrue(result.verified)
        self.assertEqual(expanded.calls, 1)
        self.assertEqual(forbidden_invoke.calls, 0)
        self.assertEqual(shown.calls, 1)

    def test_already_expanded_menu_is_never_collapsed_or_invoked(self):
        expansion = Pattern()
        expansion.ExpandCollapseState = 1
        invoke = Pattern()
        more = Node("More actions", "ButtonControl", patterns={10005: expansion, 10000: invoke})
        result = setup.enable_current_meeting(Auto([meeting([more])]))
        self.assertFalse(result.verified)
        self.assertEqual(expansion.calls + invoke.calls, 0)

    def test_unknown_expansion_state_fails_closed(self):
        expansion = Pattern()
        expansion.ExpandCollapseState = 2
        invoke = Pattern()
        more = Node("More actions", "ButtonControl", patterns={10005: expansion, 10000: invoke})
        result = setup.enable_current_meeting(Auto([meeting([more])]))
        self.assertFalse(result.verified)
        self.assertEqual(expansion.calls + invoke.calls, 0)

    def test_show_caption_target_never_uses_expand_as_fallback(self):
        expansion = Pattern()
        expansion.ExpandCollapseState = 0
        show = Node("Show live captions", "MenuItemControl", patterns={10005: expansion})
        result = setup.enable_current_meeting(Auto([meeting([show])]))
        self.assertFalse(result.verified)
        self.assertEqual(expansion.calls, 0)

    def test_current_cancel_between_navigation_and_show_prevents_show(self):
        cancel = threading.Event()
        root = meeting([])
        base = list(root.children)
        show, show_pattern = button("Show live captions")
        def open_then_cancel():
            root.set_children([*base, show])
            cancel.set()
        more, more_pattern = button("More", open_then_cancel)
        root.set_children([*base, more])
        result = setup.enable_current_meeting(Auto([root]), cancel)
        self.assertEqual(result.state, "cancelled")
        self.assertEqual(more_pattern.calls, 1)
        self.assertEqual(show_pattern.calls, 0)

    def test_caption_words_in_text_are_not_commands(self):
        text = Node("Show live captions", "TextControl")
        result = setup.enable_current_meeting(Auto([meeting([text])]))
        self.assertFalse(result.verified)

    def test_hidden_meeting_is_not_changed(self):
        show, pattern = button("Show live captions")
        root = meeting([show])
        root.IsOffscreen = True
        result = setup.enable_current_meeting(Auto([root]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_bounded_tree_limit_fails_without_mutation(self):
        caption, pattern = toggle()
        with patch.object(setup, "_MAX_NODES", 3):
            result = setup.enable_always_show(Auto([accessibility([caption])]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)

    def test_missing_accessibility_selection_never_implies_already_enabled(self):
        caption, pattern = toggle(state=1)
        root = Node("Microsoft Teams", children=[Node("Accessibility", "TextControl"), caption])
        result = setup.enable_always_show(Auto([root]))
        self.assertFalse(result.verified)
        self.assertEqual(pattern.calls, 0)


if __name__ == "__main__":
    unittest.main()
