"""Track call-window continuity independently of caption/control visibility."""
from dataclasses import dataclass
import ctypes
import os


COMPACT_TITLES = {"meeting compact view", "compact view"}
PROVISIONAL_TITLES = {"meeting join"}
AUXILIARY_TITLES = {"sharing control bar"}
GENERIC_TITLES = COMPACT_TITLES | PROVISIONAL_TITLES | {"meeting", "team meeting", "captions", ""}


def is_auxiliary_name(name):
    """Identify an exact Teams helper title, not a meeting mentioned within it."""
    first = " ".join(str(name or "").split("|", 1)[0].split()).casefold()
    return first in AUXILIARY_TITLES


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    name: str
    title: str

    def matches(self, other):
        if is_auxiliary_name(self.name) or is_auxiliary_name(other.name):
            return False
        if self.pid != other.pid:
            return False
        if self.hwnd and other.hwnd and self.hwnd != other.hwnd:
            return False
        # Never keep a call alive based only on the Teams process or a reused HWND.
        return (bool(self.title and other.title and self.name and self.name == other.name) or
                bool(self.title and self.title == other.title) or
                bool(self.hwnd and self.hwnd == other.hwnd and
                     self.title and other.title in COMPACT_TITLES))


@dataclass(frozen=True)
class MeetingSurface:
    identity: WindowIdentity
    viewer: bool = False
    held: bool = False


@dataclass(frozen=True)
class MeetingSelection:
    surfaces: tuple = ()
    changed: bool = False
    held: bool = False
    ambiguous: bool = False
    paused: bool = False
    reason: str = ""


class MeetingSelector:
    """Bind a transcript to one call, never to the union of visible calls.

    Titles associate a detached viewer only when there is a unique owner.
    Native identities distinguish concurrent same-name calls. A superseded
    surface cannot steal capture back merely because enumeration order changes.
    Selection is a proposal: commit only after the previous transcript is saved.
    """

    def __init__(self):
        self.current = ()
        self.retired = []
        self.ambiguous_titles = set()
        self.previously_held = set()
        self.retired_missing = {}
        self.ambiguous_missing = {}
        # Continuity-only aliases are never caption ownership. A compact view
        # can replace a named window without exposing the actual caption tree.
        self.continuity_compacts = set()
        self._foreign_window_keys = set()
        self._last_observed = ()
        self._initial_provisional = None
        self._provisional_target = None

    @staticmethod
    def _window_key(identity):
        return identity.hwnd, identity.pid

    def _compact_continuity(self, observed):
        owner_pids = {s.identity.pid for s in self.current
                      if s.identity.hwnd and s.identity.pid
                      and s.identity.title and s.identity.title not in COMPACT_TITLES}
        # A previously superseded call in the same process could own this
        # generic surface. Do not borrow its compact window for the new call.
        eligible = []
        if len(owner_pids) == 1 and not any(old.pid in owner_pids for old in self.retired):
            eligible = [identity for identity in observed
                        if identity.title in COMPACT_TITLES and identity.hwnd
                        and identity.pid in owner_pids
                        and self._window_key(identity) not in self._foreign_window_keys
                        and not any(self._matches(s.identity, identity) for s in self.current)]
        self.continuity_compacts = set(eligible)
        # Windows seen before ownership or known to be unrelated must not
        # become continuity evidence merely by changing their title to compact.
        self._foreign_window_keys.update(self._window_key(identity) for identity in observed
            if identity.hwnd and identity.pid and identity not in self.continuity_compacts
            and not any(self._matches(s.identity, identity) for s in self.current))
        return bool(eligible)

    def _provisional_refinement(self, observed):
        """Bind only the first provisional main to its exact native replacement.

        An offscreen title change can prove continuity, not caption ownership.
        Remember the first specific title so a later different call cannot be
        absorbed before the first named window has become readable again.
        """
        original = self._initial_provisional
        if (original is None or self.previously_held
                or not any(s.identity == original for s in self.current)):
            return None
        candidates = {identity for identity in observed
                      if identity.hwnd == original.hwnd and identity.pid == original.pid
                      and identity.title not in GENERIC_TITLES
                      and not is_auxiliary_name(identity.name)
                      and identity.name.split("|", 1)[0].strip().casefold() not in {"caption", "captions"}}
        if len(candidates) != 1:
            return None
        target = next(iter(candidates))
        if self._provisional_target is not None and target.title != self._provisional_target.title:
            return None
        self._provisional_target = target
        return target

    @staticmethod
    def _matches(left, right):
        # Compact is a one-way transition from a known named call. Reversing
        # matches would let a new named call reuse an old compact HWND.
        return left.matches(right)

    def _owned(self, surface):
        for old in self.current:
            if not self._matches(old.identity, surface.identity):
                continue
            if surface.identity.title in self.ambiguous_titles:
                # A caption viewer has no call ID, and missing native handles
                # must not make a same-name survivor look like the old call.
                if old.viewer or surface.viewer:
                    continue
                if not (old.identity.hwnd and old.identity.hwnd == surface.identity.hwnd
                        and old.identity.pid and old.identity.pid == surface.identity.pid):
                    continue
            return True
        return False

    def _retired(self, surface):
        return any(self._matches(old, surface.identity) for old in self.retired)

    def _groups(self, surfaces):
        mains = [s for s in surfaces if not s.viewer]
        titles = [s.identity.title for s in mains]
        self.ambiguous_titles.update(t for t in titles if titles.count(t) > 1)
        groups = [[s] for s in mains]
        orphan_viewers = {}
        for viewer in (s for s in surfaces if s.viewer):
            title = viewer.identity.title
            owners = [g for g in groups if g[0].identity.title == title]
            if not owners and self._owned(viewer):
                owners = [g for g in groups if g[0].identity.title in COMPACT_TITLES
                          and self._owned(g[0])]
            if title in self.ambiguous_titles:
                continue
            if len(owners) == 1 and (title not in GENERIC_TITLES or (
                    title not in COMPACT_TITLES and title and viewer.identity.pid
                    and owners[0][0].identity.pid == viewer.identity.pid)):
                owners[0].append(viewer)
            elif not owners:
                orphan_viewers.setdefault(title, []).append(viewer)
            # Two same-title main windows cannot safely share a caption viewer.
        for title, viewers in orphan_viewers.items():
            known_main = [s for s in self.current if not s.viewer
                          and s.identity.title == title and s.identity.pid == viewers[0].identity.pid]
            if len(viewers) == 1 and (title not in GENERIC_TITLES or self._owned(viewers[0])
                                     or (title not in COMPACT_TITLES and title and len(known_main) == 1)):
                groups.append(viewers)
        return [tuple(g) for g in groups]

    def observe_retired(self, observed, still_open, now, grace):
        for old in list(self.retired):
            if any(self._matches(old, seen) or
                   (old.title not in GENERIC_TITLES and old.title == seen.title)
                   for seen in observed) or still_open(old):
                self.retired_missing.pop(old, None)
            elif now - self.retired_missing.setdefault(old, now) >= grace:
                self.retired.remove(old)
                self.retired_missing.pop(old, None)
                self.previously_held.discard(old)
        for title in list(self.ambiguous_titles):
            if any(seen.title == title for seen in observed):
                self.ambiguous_missing.pop(title, None)
            elif now - self.ambiguous_missing.setdefault(title, now) >= grace:
                self.ambiguous_titles.discard(title)
                self.ambiguous_missing.pop(title, None)

    def uncertain(self):
        self.retired_missing.clear()
        self.ambiguous_missing.clear()

    def select(self, surfaces, observed=()):
        # Capture-side classification also excludes these, but no auxiliary
        # toolbar may claim a transcript if a caller supplies one directly.
        surfaces = tuple(s for s in surfaces if not is_auxiliary_name(s.identity.name))
        observed = tuple(observed)
        self._last_observed = observed
        compact_continuity = self._compact_continuity(observed) if self.current else False
        # A minimized old main (or its still-open viewer) is also simultaneous
        # evidence. Do not attach that viewer to a new same-name main HWND.
        if any(self._matches(old.identity, seen) for old in self.current for seen in observed):
            self.ambiguous_titles.update(s.identity.title for s in surfaces if not s.viewer
                and not self._owned(s) and s.identity.title not in GENERIC_TITLES
                and any(old.identity.title == s.identity.title for old in self.current))
        groups = self._groups(surfaces)
        current = [g for g in groups if any(self._owned(s) for s in g)]
        current_held = bool(current and any(s.held for g in current for s in g))
        held_now = {s.identity for s in surfaces if s.held}
        resumed = [g for g in groups if not any(s.held for s in g)
                   and any(any(self._matches(old, s.identity) for old in self.previously_held)
                           for s in g)]
        # Hold evidence is consumed only by commit, so a failed save cannot
        # lose the pending resume transition and redirect captions elsewhere.
        self.previously_held |= held_now
        provisional_target = self._provisional_refinement(observed)
        fresh = [g for g in groups if not any(self._owned(s) or self._retired(s) for s in g)
                 and not any(s.held for s in g)
                 and not (self.current and all(s.identity.title in COMPACT_TITLES for s in g))
                 and not (all(s.viewer for s in g) and any(
                     old.title == g[0].identity.title for old in self.retired))]
        if len(current) > 1:
            return MeetingSelection(ambiguous=True, paused=bool(self.current), reason="multiple_owned_surfaces")
        if not self.current and len(resumed) == 1:
            # Once the previous call's closure grace has completed, a proven
            # held-to-unheld call can resume even though its window is retired.
            return MeetingSelection(resumed[0], reason="held_call_resumed_after_closure")
        if self.current and current_held:
            resumed_other = [g for g in resumed if g not in current]
            if len(resumed_other) == 1:
                return MeetingSelection(resumed_other[0], changed=True, reason="held_call_resumed")
        if len(fresh) > 1:
            return MeetingSelection(ambiguous=True, paused=bool(self.current), reason="multiple_new_meetings")
        if fresh:
            target = fresh[0]
            if not self.current:
                # A single unheld call is usable even if another call is held.
                return MeetingSelection(target, reason="initial_meeting")
            # A lone same-name replacement after a layout/HWND recreation is
            # continuity. Concurrent distinct HWNDs instead prove two surfaces.
            old_titles = {s.identity.title for s in self.current} - GENERIC_TITLES
            new_titles = {s.identity.title for s in target} - GENERIC_TITLES
            old_still_present = any(self._matches(old.identity, seen)
                                    for old in self.current for seen in observed)
            same_title = {s.identity.title for s in self.current if s.identity.title
                          and s.identity.title not in COMPACT_TITLES} & {
                              s.identity.title for s in target if s.identity.title}
            if (same_title and not all(s.viewer for s in target)
                    and (old_still_present or same_title & self.ambiguous_titles)):
                # A second same-name native surface can be either another call
                # or a Teams layout transition. Neither split nor combine it.
                self.ambiguous_titles.update(same_title)
                return MeetingSelection(ambiguous=True, paused=True, reason="same_title_ambiguity")
            if (provisional_target is not None and len(groups) == 1
                    and any(not s.viewer and s.identity == provisional_target for s in target)):
                return MeetingSelection(target, reason="provisional_title_refined")
            if (not old_titles and new_titles and len(groups) == 1
                    and not self.previously_held and any(
                        not old.viewer and old.identity.title in GENERIC_TITLES - COMPACT_TITLES - PROVISIONAL_TITLES
                        and old.identity.hwnd and old.identity.pid
                        and old.identity.hwnd == new.identity.hwnd
                        and old.identity.pid == new.identity.pid
                        for old in self.current for new in target if not new.viewer)):
                # Teams may name a newly joined call after first exposing a
                # generic title. This is not the named->compact->new-call case.
                return MeetingSelection(target, reason="generic_title_refined")
            if (all(s.viewer for s in target) and target[0].identity.title not in self.ambiguous_titles
                    and any(not s.viewer and s.identity.title == target[0].identity.title
                            and (s.identity.title not in GENERIC_TITLES
                                 or s.identity.pid == target[0].identity.pid) for s in self.current)):
                # The current call can pop out its captions while its main
                # window is minimized or temporarily lacks a toolbar.
                return MeetingSelection(target, reason="current_viewer_attached")
            if (not current and not old_still_present and old_titles == new_titles
                    and old_titles and not any(self._retired(s) for s in target)):
                old_pids = {s.identity.pid for s in self.current if s.identity.pid}
                new_pids = {s.identity.pid for s in target if s.identity.pid}
                if len(old_pids) == 1 and old_pids == new_pids:
                    return MeetingSelection(target, reason="same_named_window_recreated")
                return MeetingSelection(ambiguous=True, paused=True, reason="same_title_identity_unverified")
            # An unrelated generic/compact surface is insufficient identity.
            if not new_titles and not current_held:
                return MeetingSelection(ambiguous=True, paused=True, reason="generic_identity_unverified")
            return MeetingSelection(target, changed=True, reason="different_named_meeting")
        if current:
            return MeetingSelection(current[0], held=current_held,
                                    reason="current_meeting_held" if current_held else "current_meeting")
        if provisional_target is not None:
            return MeetingSelection(paused=True, reason="provisional_title_refined")
        if compact_continuity:
            return MeetingSelection(paused=True, reason="compact_continuity")
        return MeetingSelection(reason="no_selected_surface")

    def commit(self, selection):
        if selection.paused or not selection.surfaces:
            return
        if selection.changed or not self.current:
            selected = [s.identity for s in selection.surfaces]
            provisional = [s.identity for s in selection.surfaces if not s.viewer
                           and s.identity.title in PROVISIONAL_TITLES
                           and s.identity.hwnd and s.identity.pid]
            self._initial_provisional = provisional[0] if len(provisional) == 1 else None
            self._provisional_target = None
            self._foreign_window_keys = {self._window_key(identity) for identity in self._last_observed
                if identity.hwnd and identity.pid and not any(self._matches(new, identity) for new in selected)}
            self.continuity_compacts.clear()
        if selection.changed:
            old_titles = {s.identity.title for s in self.current}
            new_titles = {s.identity.title for s in selection.surfaces}
            self.ambiguous_titles.update((old_titles & new_titles) - GENERIC_TITLES)
            for surface in self.current:
                if surface.identity not in self.retired:
                    self.retired.append(surface.identity)
            self.current = ()
        # A resumed surface is current again, not permanently retired.
        self.retired = [old for old in self.retired
                        if not any(self._matches(old, s.identity) for s in selection.surfaces)]
        if not selection.held:
            self.previously_held = {old for old in self.previously_held
                                    if not any(self._matches(old, s.identity) for s in selection.surfaces)}
        previous = [old for old in self.current if not any(
            old.identity.hwnd and old.identity.hwnd == new.identity.hwnd
            and old.identity.pid == new.identity.pid and new.identity.title not in COMPACT_TITLES
            for new in selection.surfaces)]
        self.current = tuple(dict.fromkeys(previous + list(selection.surfaces)))
        if self._initial_provisional is not None and not any(
                s.identity == self._initial_provisional for s in self.current):
            self._initial_provisional = None
            self._provisional_target = None

    def end_session(self):
        # Keep superseded windows excluded. A stale old viewer must not become
        # a new meeting as soon as the selected call closes.
        self.current = ()
        self.continuity_compacts.clear()
        self._foreign_window_keys.clear()
        self._initial_provisional = None
        self._provisional_target = None

    def allow_caption(self, identity):
        return self._owned(MeetingSurface(identity))


def native_window_name(identity: WindowIdentity):
    """(exists, title): confirm a previously known HWND even if UIA omits it.

    None title means unreadable; False means destroyed or recycled into another PID.
    Only reads the identity of a previously confirmed meeting window.
    """
    if not identity.hwnd or os.name != "nt":
        return False, None
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    if not user32.IsWindow(identity.hwnd):
        return False, None
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(identity.hwnd, ctypes.byref(pid)):
        return True, None
    if pid.value != identity.pid:
        return False, None
    buffer = ctypes.create_unicode_buffer(4096)
    if not user32.GetWindowTextW(identity.hwnd, buffer, len(buffer)):
        return True, None
    return True, buffer.value


class MeetingPresence:
    def __init__(self):
        self.known = []
        self.session_titles = set()
        self.missing_since = None

    def remember(self, identities):
        # Replace superseded titles on the same HWND; do not accumulate unrelated windows.
        for identity in identities:
            if identity.title in COMPACT_TITLES:
                if identity not in self.known:
                    self.known.append(identity)
                continue
            self.known = [old for old in self.known if not old.matches(identity)
                          and not (identity.hwnd and identity.hwnd == old.hwnd and identity.pid == old.pid)]
            self.known.append(identity)
            if identity.title not in GENERIC_TITLES:
                self.session_titles.add(identity.title)
        self.missing_since = None

    def uncertain(self):
        self.missing_since = None

    def observe(self, observed, still_open, now, grace):
        if any(old.matches(current) for old in self.known for current in observed) or any(
            still_open(old) for old in self.known
        ):
            self.missing_since = None
            return "uncertain"
        if self.missing_since is None:
            self.missing_since = now
        return "ended" if now - self.missing_since >= grace else "missing"

    def different_meeting(self, active):
        """A newly confirmed named call can supersede a reused meeting window."""
        old_titles = self.session_titles
        new_titles = {w.title for w in active} - GENERIC_TITLES
        return bool(old_titles and new_titles and old_titles.isdisjoint(new_titles)
                    and not any(old.matches(new) for old in self.known for new in active))
