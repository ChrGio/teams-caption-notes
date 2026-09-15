"""WinDLL functions are mocked; no test accesses the real Windows clipboard."""

import ctypes
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import windows_clipboard as clipboard


class FakeClipboard:
    def __init__(self):
        self.buffers = {}
        self.locks = {}
        self.events = []
        self.owner = 0x100001001
        self.handle_counter = 0x200002000
        self.clipboard_handle = None
        self.opened = False
        self.busy = 0
        self.user32 = SimpleNamespace(
            CreateWindowExW=Mock(side_effect=self.create), DestroyWindow=Mock(side_effect=self.destroy),
            OpenClipboard=Mock(side_effect=self.open), CloseClipboard=Mock(side_effect=self.close),
            EmptyClipboard=Mock(side_effect=self.empty), SetClipboardData=Mock(side_effect=self.set),
            GetClipboardData=Mock(side_effect=self.get),
        )
        self.kernel32 = SimpleNamespace(
            GlobalAlloc=Mock(side_effect=self.allocate), GlobalFree=Mock(side_effect=self.free),
            GlobalLock=Mock(side_effect=self.lock), GlobalUnlock=Mock(side_effect=self.unlock),
            GlobalSize=Mock(side_effect=self.size),
        )

    def buffer(self, data):
        self.handle_counter += 1
        handle = self.handle_counter
        self.buffers[handle] = ctypes.create_string_buffer(data, len(data))
        self.locks[handle] = 0
        return handle

    def seed(self, data):
        self.clipboard_handle = self.buffer(data)

    def create(self, *args):
        self.events.append("create-owner")
        return self.owner

    def destroy(self, owner):
        assert owner == self.owner
        self.events.append("destroy-owner")
        return True

    def open(self, owner):
        self.events.append("open")
        if self.busy:
            self.busy -= 1
            return False
        assert not self.opened
        self.opened = True
        return True

    def close(self):
        assert self.opened
        self.events.append("close")
        self.opened = False
        return True

    def empty(self):
        assert self.opened
        self.events.append("empty")
        self.clipboard_handle = None
        return True

    def set(self, format_id, handle):
        assert self.opened and format_id == clipboard.CF_UNICODETEXT
        assert self.locks[handle] == 0
        self.events.append("set")
        self.clipboard_handle = handle
        return handle

    def get(self, format_id):
        assert self.opened and format_id == clipboard.CF_UNICODETEXT
        self.events.append("get")
        return self.clipboard_handle

    def allocate(self, flags, size):
        assert flags == clipboard.GMEM_MOVEABLE
        self.events.append("allocate")
        return self.buffer(b"\0" * size)

    def free(self, handle):
        assert handle != self.clipboard_handle, "Clipboard-owned memory must never be freed"
        self.events.append("free")
        del self.buffers[handle]
        return None

    def lock(self, handle):
        self.events.append("lock")
        self.locks[handle] += 1
        return ctypes.addressof(self.buffers[handle])

    def unlock(self, handle):
        self.events.append("unlock")
        self.locks[handle] -= 1
        assert self.locks[handle] >= 0
        return self.locks[handle]

    def size(self, handle):
        self.events.append("size")
        return ctypes.sizeof(self.buffers[handle])


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClipboard()
        self.loader = patch.object(clipboard.ctypes, "WinDLL", side_effect=lambda name, **kwargs:
                                  self.fake.user32 if name == "user32" else self.fake.kernel32)
        self.dll = self.loader.start()
        self.addCleanup(self.loader.stop)
        self.sleep_patch = patch.object(clipboard.time, "sleep")
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def assert_read_only(self):
        self.fake.user32.EmptyClipboard.assert_not_called()
        self.fake.user32.SetClipboardData.assert_not_called()
        self.fake.user32.CreateWindowExW.assert_not_called()
        self.fake.user32.DestroyWindow.assert_not_called()
        self.fake.kernel32.GlobalAlloc.assert_not_called()
        self.fake.kernel32.GlobalFree.assert_not_called()

    def test_copy_unicode_reads_back_before_close_and_transfers_ownership(self):
        text = "Meeting notes: Ω, 中文, café, 📝\nSecond line."
        clipboard.copy_text(text)
        self.assertEqual(self.fake.buffers[self.fake.clipboard_handle].raw, text.encode("utf-16-le") + b"\0\0")
        self.assertEqual(self.fake.events, ["create-owner", "open", "allocate", "lock", "unlock",
                                           "empty", "set", "get", "size", "lock", "unlock", "close", "destroy-owner"])
        self.fake.kernel32.GlobalFree.assert_not_called()
        self.fake.user32.OpenClipboard.assert_called_once_with(self.fake.owner)
        self.assertFalse(self.fake.opened)

    def test_copy_empty_text_is_valid_terminated_unicode(self):
        clipboard.copy_text("")
        self.assertEqual(self.fake.buffers[self.fake.clipboard_handle].raw, b"\0\0")

    def test_all_pointer_and_size_signatures_are_declared_for_64_bit_windows(self):
        clipboard._api()
        self.assertEqual(self.fake.user32.GetClipboardData.argtypes, (wintypes.UINT,))
        self.assertIs(self.fake.user32.GetClipboardData.restype, wintypes.HANDLE)
        self.assertIs(self.fake.user32.SetClipboardData.restype, wintypes.HANDLE)
        self.assertIs(self.fake.kernel32.GlobalSize.restype, ctypes.c_size_t)
        self.assertIs(self.fake.kernel32.GlobalLock.restype, wintypes.LPVOID)
        self.assertIs(self.fake.kernel32.GlobalFree.restype, wintypes.HGLOBAL)
        self.assertEqual(self.fake.user32.CloseClipboard.argtypes, ())
        self.assertIs(self.fake.user32.CloseClipboard.restype, wintypes.BOOL)
        self.assertEqual(ctypes.sizeof(self.fake.user32.GetClipboardData.restype), ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(ctypes.sizeof(self.fake.kernel32.GlobalSize.restype), ctypes.sizeof(ctypes.c_void_p))

    def test_busy_copy_retries_only_bounded_attempts_and_destroys_owner(self):
        self.fake.busy = 100
        with self.assertRaisesRegex(clipboard.ClipboardError, "busy"):
            clipboard.copy_text("requested")
        self.assertEqual(self.fake.user32.OpenClipboard.call_count, clipboard.OPEN_ATTEMPTS)
        self.assertEqual(self.sleep.call_args_list, [call(clipboard.RETRY_DELAY)] * (clipboard.OPEN_ATTEMPTS - 1))
        self.fake.user32.DestroyWindow.assert_called_once_with(self.fake.owner)
        self.fake.user32.CloseClipboard.assert_not_called()
        self.fake.user32.EmptyClipboard.assert_not_called()
        self.fake.kernel32.GlobalAlloc.assert_not_called()

    def test_copy_succeeds_after_temporary_clipboard_contention(self):
        self.fake.busy = 2
        clipboard.copy_text("requested")
        self.assertEqual(self.fake.user32.OpenClipboard.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.fake.user32.CloseClipboard.assert_called_once_with()

    def test_owner_creation_failure_never_opens_clipboard(self):
        self.fake.user32.CreateWindowExW.side_effect = None
        self.fake.user32.CreateWindowExW.return_value = 0
        with self.assertRaisesRegex(clipboard.ClipboardError, "owner window"):
            clipboard.copy_text("requested")
        self.fake.user32.OpenClipboard.assert_not_called()
        self.fake.user32.DestroyWindow.assert_not_called()

    def test_allocation_failure_preserves_existing_clipboard_and_closes(self):
        self.fake.seed(b"old-data")
        old_handle = self.fake.clipboard_handle
        self.fake.kernel32.GlobalAlloc.side_effect = None
        self.fake.kernel32.GlobalAlloc.return_value = 0
        with self.assertRaisesRegex(clipboard.ClipboardError, "allocate"):
            clipboard.copy_text("requested")
        self.assertEqual(self.fake.clipboard_handle, old_handle)
        self.fake.user32.EmptyClipboard.assert_not_called()
        self.fake.user32.CloseClipboard.assert_called_once_with()
        self.fake.user32.DestroyWindow.assert_called_once_with(self.fake.owner)

    def test_initial_lock_failure_frees_only_untransferred_allocation(self):
        self.fake.kernel32.GlobalLock.side_effect = None
        self.fake.kernel32.GlobalLock.return_value = 0
        with self.assertRaisesRegex(clipboard.ClipboardError, "lock"):
            clipboard.copy_text("requested")
        self.fake.kernel32.GlobalFree.assert_called_once()
        self.fake.kernel32.GlobalUnlock.assert_not_called()
        self.fake.user32.EmptyClipboard.assert_not_called()
        self.assertFalse(self.fake.opened)

    def test_failed_empty_frees_allocation_and_does_not_set_clipboard(self):
        self.fake.user32.EmptyClipboard.side_effect = None
        self.fake.user32.EmptyClipboard.return_value = 0
        with self.assertRaisesRegex(clipboard.ClipboardError, "prepare"):
            clipboard.copy_text("requested")
        self.fake.kernel32.GlobalFree.assert_called_once()
        self.fake.user32.SetClipboardData.assert_not_called()
        self.fake.user32.CloseClipboard.assert_called_once_with()

    def test_failed_set_frees_untransferred_allocation(self):
        self.fake.user32.SetClipboardData.side_effect = None
        self.fake.user32.SetClipboardData.return_value = 0
        with self.assertRaisesRegex(clipboard.ClipboardError, "place"):
            clipboard.copy_text("requested")
        self.fake.kernel32.GlobalFree.assert_called_once()
        self.fake.user32.GetClipboardData.assert_not_called()
        self.fake.user32.DestroyWindow.assert_called_once_with(self.fake.owner)

    def test_copy_readback_mismatch_never_frees_transferred_memory(self):
        def changed(format_id, handle):
            result = self.fake.set(format_id, handle)
            ctypes.memset(ctypes.addressof(self.fake.buffers[handle]), ord("X"), 2)
            return result
        self.fake.user32.SetClipboardData.side_effect = changed
        with self.assertRaisesRegex(clipboard.ClipboardError, "does not match"):
            clipboard.copy_text("requested")
        self.fake.kernel32.GlobalFree.assert_not_called()
        self.fake.user32.CloseClipboard.assert_called_once_with()
        self.fake.user32.DestroyWindow.assert_called_once_with(self.fake.owner)
        self.assertEqual(self.fake.locks[self.fake.clipboard_handle], 0)

    def test_copy_readback_lock_failure_still_closes_without_freeing_transferred_handle(self):
        calls = []
        def lock(handle):
            calls.append(handle)
            return self.fake.lock(handle) if len(calls) == 1 else 0
        self.fake.kernel32.GlobalLock.side_effect = lock
        with self.assertRaisesRegex(clipboard.ClipboardError, "verification"):
            clipboard.copy_text("requested")
        self.fake.kernel32.GlobalFree.assert_not_called()
        self.fake.user32.CloseClipboard.assert_called_once_with()

    def test_verify_success_is_read_only_and_reads_only_expected_bytes(self):
        expected = "Ω📝 hello".encode("utf-16-le") + b"\0\0"
        self.fake.seed(expected + b"unrelated allocation padding")
        with patch.object(clipboard.ctypes, "string_at", wraps=ctypes.string_at) as read:
            clipboard.verify_text("Ω📝 hello")
        self.assertEqual(read.call_args.args[1], len(expected))
        self.fake.user32.OpenClipboard.assert_called_once_with(None)
        self.fake.user32.CloseClipboard.assert_called_once_with()
        self.assert_read_only()

    def test_very_large_global_size_never_causes_unbounded_read(self):
        expected = "note".encode("utf-16-le") + b"\0\0"
        self.fake.seed(expected)
        self.fake.kernel32.GlobalSize.side_effect = None
        self.fake.kernel32.GlobalSize.return_value = 2**40
        with patch.object(clipboard.ctypes, "string_at", wraps=ctypes.string_at) as read:
            clipboard.verify_text("note")
        self.assertEqual(read.call_args.args[1], len(expected))
        self.assert_read_only()

    def test_verify_mismatch_does_not_restore_overwrite_or_expose_either_text(self):
        actual, expected = "private clipboard item", "different requested transcript"
        self.fake.seed(actual.encode("utf-16-le") + b"\0\0")
        original = self.fake.clipboard_handle
        with self.assertRaises(clipboard.ClipboardError) as raised:
            clipboard.verify_text(expected)
        self.assertNotIn(actual, str(raised.exception))
        self.assertNotIn(expected, str(raised.exception))
        self.assertEqual(self.fake.clipboard_handle, original)
        self.assert_read_only()

    def test_identical_prefix_of_longer_clipboard_text_does_not_verify(self):
        self.fake.seed("hello extra".encode("utf-16-le") + b"\0\0")
        with self.assertRaisesRegex(clipboard.ClipboardError, "does not match"):
            clipboard.verify_text("hello")
        self.assert_read_only()

    def test_missing_terminator_cannot_pass_exact_verification(self):
        self.fake.seed("hello".encode("utf-16-le") + b"XX")
        with self.assertRaisesRegex(clipboard.ClipboardError, "does not match"):
            clipboard.verify_text("hello")
        self.assertEqual(self.fake.locks[self.fake.clipboard_handle], 0)

    def test_short_or_invalid_memory_never_reads_beyond_global_size(self):
        for size in (0, 1, 2, 4):
            with self.subTest(size=size):
                self.fake.seed(b"\0" * max(size, 1))
                self.fake.kernel32.GlobalSize.side_effect = None
                self.fake.kernel32.GlobalSize.return_value = size
                self.fake.kernel32.GlobalLock.reset_mock()
                with patch.object(clipboard.ctypes, "string_at") as read:
                    with self.assertRaises(clipboard.ClipboardError):
                        clipboard.verify_text("longer expected text")
                self.fake.kernel32.GlobalLock.assert_not_called()
                read.assert_not_called()
        self.assert_read_only()

    def test_missing_unicode_format_does_not_access_memory(self):
        with self.assertRaisesRegex(clipboard.ClipboardError, "Unicode text is unavailable"):
            clipboard.verify_text("requested")
        self.fake.kernel32.GlobalSize.assert_not_called()
        self.fake.kernel32.GlobalLock.assert_not_called()
        self.assert_read_only()

    def test_busy_verify_retries_without_mutation_or_owner_window(self):
        self.fake.busy = 100
        with self.assertRaisesRegex(clipboard.ClipboardError, "busy"):
            clipboard.verify_text("requested")
        self.assertEqual(self.fake.user32.OpenClipboard.call_count, clipboard.OPEN_ATTEMPTS)
        self.fake.user32.CloseClipboard.assert_not_called()
        self.assert_read_only()

    def test_close_failure_is_reported_but_owner_cleanup_still_runs(self):
        self.fake.user32.CloseClipboard.side_effect = None
        self.fake.user32.CloseClipboard.return_value = False
        with self.assertRaisesRegex(clipboard.ClipboardError, "release the Windows clipboard"):
            clipboard.copy_text("requested")
        self.fake.user32.DestroyWindow.assert_called_once_with(self.fake.owner)
        self.fake.kernel32.GlobalFree.assert_not_called()

    def test_invalid_input_is_rejected_before_any_clipboard_api(self):
        for operation in (clipboard.copy_text, clipboard.verify_text):
            for text in ("embedded\0NUL", "unpaired\ud800"):
                with self.subTest(operation=operation.__name__, text=repr(text)):
                    with self.assertRaises(clipboard.ClipboardError):
                        operation(text)
            with self.assertRaises(TypeError):
                operation(None)
        self.dll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
