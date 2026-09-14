import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import windows_startup
import notes_library
from notes_window import NotesWindow
from teams_caption_tray import TrayApp, CHATGPT_URL


class StartupTests(unittest.TestCase):
    def test_packaged_path_with_spaces_is_quoted(self):
        with patch.object(windows_startup.sys, "frozen", True, create=True), patch.object(
            windows_startup.sys, "executable", r"C:\Apps\Meeting Notes\TeamsCaptionNotes.exe"
        ):
            self.assertEqual(windows_startup.startup_command(), '"C:\\Apps\\Meeting Notes\\TeamsCaptionNotes.exe" --startup')

    def test_enable_writes_only_current_user_app_value(self):
        with patch.object(windows_startup.winreg, "CreateKeyEx") as create, patch.object(
            windows_startup.winreg, "SetValueEx"
        ) as write, patch.object(windows_startup, "startup_command", return_value='"C:\\Notes.exe"'):
            windows_startup.set_enabled(True)
            self.assertEqual(create.call_args.args[0], windows_startup.winreg.HKEY_CURRENT_USER)
            self.assertEqual(write.call_args.args[1:], ("TeamsCaptionNotes", 0, windows_startup.winreg.REG_SZ, '"C:\\Notes.exe"'))

    def test_disable_missing_registration_is_harmless(self):
        with patch.object(windows_startup.winreg, "OpenKey", side_effect=FileNotFoundError):
            windows_startup.set_enabled(False)

    def test_registration_for_old_copy_is_not_this_copy(self):
        with patch.object(windows_startup, "registered_command", return_value="old"), patch.object(
            windows_startup, "startup_command", return_value="new"
        ):
            self.assertFalse(windows_startup.is_enabled())


class ChatHandoffTests(unittest.TestCase):
    def test_latest_meeting_wins_even_when_old_file_was_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "Meeting_with_Alex-20260911-111925.md"
            new = root / "Team_meeting-20260911-113325.md"
            old.write_text("- Capture started: 2026-09-11T11:19:25-04:00\nOLD", encoding="utf-8")
            new.write_text("- Capture started: 2026-09-11T11:33:25-04:00\nNEW", encoding="utf-8")
            os.utime(old, (2000000000, 2000000000))
            os.utime(new, (1000000000, 1000000000))
            (root / "Team_meeting-20260912-113325-summary.md").write_text("summary")
            self.assertEqual(notes_library.latest_transcript(root), new)
            rows, errors = notes_library.search_notes([root])
            self.assertLess([r["path"] for r in rows].index(new.resolve()),
                            [r["path"] for r in rows].index(old.resolve()))
            self.assertFalse(errors)

    def test_filename_date_is_used_for_legacy_transcripts(self):
        path = Path("Team_meeting-20260911-113325.md")
        self.assertGreater(notes_library.note_time(path, "legacy"),
                           notes_library.note_time(Path("Other-20260911-111925.md"), "legacy"))

    def test_unreadable_candidate_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "meeting.md").write_text("meeting")
            with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    notes_library.latest_transcript(Path(folder))

    def test_notes_window_copies_selected_row_not_another_meeting(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / "Meeting_with_Alex-20260911-111925.md"
            new = Path(folder) / "Team_meeting-20260911-113325.md"
            old.write_text("OLD MEETING")
            new.write_text("NEW MEETING")
            window = NotesWindow.__new__(NotesWindow)
            window.rows = [{"path": new}, {"path": old}]
            window.note_list = MagicMock()
            window.note_list.selection.return_value = ("0",)
            window.status = MagicMock()
            with patch("teams_caption_tray.copy_to_clipboard") as copy, patch("notes_window.os.startfile"):
                window.handoff("ChatGPT", CHATGPT_URL)
            prompt = copy.call_args.args[0]
            self.assertTrue(prompt.startswith(f"SELECTED FILES: {new.name}"))
            self.assertIn("NEW MEETING", prompt)
            self.assertNotIn("OLD MEETING", prompt)
            self.assertIn(new.name, window.status.set.call_args.args[0])

    def test_refresh_preserves_selection_by_path_when_rows_move(self):
        old, new = Path("old.md"), Path("new.md")
        window = NotesWindow.__new__(NotesWindow)
        window.base = Path(".")
        window.library = {"folders": [], "projects": {}}
        window.rows = [{"path": old}, {"path": new}]
        window.note_list = MagicMock()
        window.note_list.selection.return_value = ("1",)
        window.query = MagicMock()
        window.status = MagicMock()
        window.run_job = lambda work, done: done(([{"path": new, "project": ""}, {"path": old, "project": ""}], []))
        window.refresh_notes()
        window.note_list.selection_add.assert_called_once_with("0")

    def test_chatgpt_copies_whole_transcript_and_opens_plain_url(self):
        app = TrayApp.__new__(TrayApp)
        app.notify = MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            transcript = Path(folder) / "meeting.md"
            transcript.write_text("A decision about the release. Ω", encoding="utf-8")
            app.latest_transcript = MagicMock(return_value=transcript)
            with patch("teams_caption_tray.copy_to_clipboard") as copy, patch("teams_caption_tray.os.startfile") as launch:
                app.open_chatgpt()
                self.assertIn("A decision about the release. Ω", copy.call_args.args[0])
                self.assertTrue(copy.call_args.args[0].startswith("SOURCE FILE: meeting.md"))
                self.assertIn("meeting.md", app.notify.call_args.args[1])
                launch.assert_called_once_with(CHATGPT_URL)

    def test_clipboard_failure_does_not_open_browser_or_stop_capture(self):
        app = TrayApp.__new__(TrayApp)
        app.notify = MagicMock()
        transcript = MagicMock()
        transcript.read_text.return_value = "meeting"
        app.latest_transcript = MagicMock(return_value=transcript)
        with patch("teams_caption_tray.copy_to_clipboard", side_effect=OSError("busy")), patch(
            "teams_caption_tray.os.startfile"
        ) as launch, patch("teams_caption_tray.logging.exception"):
            app.open_chatgpt()
            launch.assert_not_called()
            app.notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
