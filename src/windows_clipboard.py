"""Checked Unicode clipboard operations; clipboard contents are never logged.

Microsoft permits reading transferred clipboard memory before CloseClipboard,
but the receiving application must never free that memory. GlobalSize can be
larger than the requested allocation, so verification reads only the expected
UTF-16 bytes plus their terminating NUL, never an unbounded clipboard string.
"""

from __future__ import annotations

import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
OPEN_ATTEMPTS = 10
RETRY_DELAY = 0.05


class ClipboardError(OSError):
    """The requested text could not be copied or verified safely."""


def _encoded(text: str) -> bytes:
    if not isinstance(text, str):
        raise TypeError("Clipboard text must be a string.")
    if "\x00" in text:
        raise ClipboardError("Clipboard text cannot contain embedded NUL characters.")
    try:
        return text.encode("utf-16-le") + b"\x00\x00"
    except UnicodeEncodeError:
        raise ClipboardError("The requested text is not valid Unicode for the Windows clipboard.") from None


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = (
        (user32.OpenClipboard, (wintypes.HWND,), wintypes.BOOL),
        (user32.CloseClipboard, (), wintypes.BOOL),
        (user32.EmptyClipboard, (), wintypes.BOOL),
        (user32.GetClipboardData, (wintypes.UINT,), wintypes.HANDLE),
        (user32.SetClipboardData, (wintypes.UINT, wintypes.HANDLE), wintypes.HANDLE),
        (user32.CreateWindowExW, (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ), wintypes.HWND),
        (user32.DestroyWindow, (wintypes.HWND,), wintypes.BOOL),
        (kernel32.GlobalAlloc, (wintypes.UINT, ctypes.c_size_t), wintypes.HGLOBAL),
        (kernel32.GlobalSize, (wintypes.HGLOBAL,), ctypes.c_size_t),
        (kernel32.GlobalLock, (wintypes.HGLOBAL,), wintypes.LPVOID),
        (kernel32.GlobalUnlock, (wintypes.HGLOBAL,), wintypes.BOOL),
        (kernel32.GlobalFree, (wintypes.HGLOBAL,), wintypes.HGLOBAL),
    )
    for function, arguments, result in signatures:
        function.argtypes = arguments
        function.restype = result
    return user32, kernel32


@contextmanager
def _open_clipboard(user32, owner):
    for attempt in range(OPEN_ATTEMPTS):
        if user32.OpenClipboard(owner):
            break
        if attempt + 1 < OPEN_ATTEMPTS:
            time.sleep(RETRY_DELAY)
    else:
        raise ClipboardError("The Windows clipboard is busy. Try again.")
    try:
        yield
    finally:
        if not user32.CloseClipboard():
            raise ClipboardError("Could not release the Windows clipboard. The copy is not confirmed.")


def _unlock(kernel32, handle) -> None:
    # Zero means successful final unlock OR failure; inspect the error code.
    ctypes.set_last_error(0)
    if not kernel32.GlobalUnlock(handle) and ctypes.get_last_error() != 0:
        raise ClipboardError("Could not unlock clipboard memory.")


def _verify_locked(user32, kernel32, expected: bytes) -> None:
    handle = user32.GetClipboardData(CF_UNICODETEXT)
    if not handle:
        raise ClipboardError("Clipboard verification failed: Unicode text is unavailable.")
    size = kernel32.GlobalSize(handle)
    if size == 0:
        raise ClipboardError("Clipboard verification failed: the text memory is unavailable.")
    if size < len(expected):
        raise ClipboardError("Clipboard verification failed: the stored text does not match the requested text.")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        raise ClipboardError("Could not lock clipboard text for verification.")
    try:
        # The terminator is compared too: an identical prefix of a longer text
        # must not pass, while allocator padding after a terminator is harmless.
        if ctypes.string_at(pointer, len(expected)) != expected:
            raise ClipboardError("Clipboard verification failed: the stored text does not match the requested text.")
    finally:
        _unlock(kernel32, handle)


def copy_text(text: str) -> None:
    """Copy and read back exactly this text while still holding the clipboard."""
    expected = _encoded(text)
    user32, kernel32 = _api()
    # NULL owners cannot reliably EmptyClipboard followed by SetClipboardData.
    # This STATIC owner window is hidden and belongs to the calling thread.
    owner = user32.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, None, None, None, None)
    if not owner:
        raise ClipboardError("Could not create the clipboard owner window.")
    allocation = None
    try:
        with _open_clipboard(user32, owner):
            allocation = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(expected))
            if not allocation:
                raise ClipboardError("Could not allocate clipboard memory.")
            pointer = kernel32.GlobalLock(allocation)
            if not pointer:
                raise ClipboardError("Could not lock clipboard memory.")
            try:
                ctypes.memmove(pointer, expected, len(expected))
            finally:
                _unlock(kernel32, allocation)
            if not user32.EmptyClipboard():
                raise ClipboardError("Could not prepare the Windows clipboard for copying.")
            if not user32.SetClipboardData(CF_UNICODETEXT, allocation):
                raise ClipboardError("Could not place the requested text on the clipboard.")
            allocation = None  # Windows owns it, even if readback below fails.
            _verify_locked(user32, kernel32, expected)
    finally:
        try:
            if allocation and kernel32.GlobalFree(allocation):
                raise ClipboardError("Could not release unused clipboard memory.")
        finally:
            if not user32.DestroyWindow(owner):
                raise ClipboardError("Could not close the clipboard owner window.")


def verify_text(text: str) -> None:
    """Read-only recheck; never repair, replace, or log a changed clipboard."""
    expected = _encoded(text)
    user32, kernel32 = _api()
    # A reader needs no owner window because it never calls EmptyClipboard.
    with _open_clipboard(user32, None):
        _verify_locked(user32, kernel32, expected)
