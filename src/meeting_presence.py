"""Track call-window continuity independently of caption/control visibility."""
from dataclasses import dataclass
import ctypes
import os


COMPACT_TITLES = {"meeting compact view", "compact view"}
GENERIC_TITLES = COMPACT_TITLES | {"meeting", "team meeting", "captions", ""}


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    name: str
    title: str

    def matches(self, other):
        if self.pid != other.pid:
            return False
        if self.hwnd and other.hwnd and self.hwnd != other.hwnd:
            return False
        # Never keep a call alive based only on the Teams process or a reused HWND.
        return (bool(self.title and other.title and self.name and self.name == other.name) or
                bool(self.title and self.title == other.title) or
                bool(self.hwnd and self.hwnd == other.hwnd and
                     self.title and other.title in COMPACT_TITLES))


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
