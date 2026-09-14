#!/usr/bin/env python3
"""System-tray front end for Teams Caption Notes."""

from __future__ import annotations

import ctypes
import logging
import msvcrt
import os
import threading
import time
import subprocess
import sys
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import teams_caption_notes as capture
import copilot_summary
import windows_startup
import notes_library
import tray_settings


APP_NAME = "Teams Caption Notes v4.3"
LOG_PATH = capture.application_dir() / "teams-caption-notes.log"
COPILOT_CONFIG_PATH = capture.application_dir() / "copilot-config.json"
BASIC_COPILOT_URL = "https://m365.cloud.microsoft/chat"
CHATGPT_URL = "https://chatgpt.com/"


def copy_to_clipboard(text: str) -> None:
    """Copy Unicode text with the Windows API; no Python install is required."""
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    )
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)

    # A windowless tray callback still needs a valid clipboard owner. Opening
    # with NULL followed by EmptyClipboard can make SetClipboardData fail.
    owner = user32.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, None, None, None, None)
    if not owner:
        raise OSError("Could not create the clipboard owner window.")
    for _ in range(10):
        if user32.OpenClipboard(owner):
            break
        time.sleep(0.05)
    else:
        user32.DestroyWindow(owner)
        raise OSError("The Windows clipboard is busy. Try again.")

    handle = None
    try:
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(0x0002, len(encoded))  # GMEM_MOVEABLE
        if not handle:
            raise OSError("Could not allocate clipboard memory.")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise OSError("Could not lock clipboard memory.")
        try:
            ctypes.memmove(pointer, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            raise OSError("Could not place the transcript on the clipboard.")
        handle = None  # Windows owns it after SetClipboardData succeeds.
    finally:
        user32.CloseClipboard()
        user32.DestroyWindow(owner)
        if handle:
            kernel32.GlobalFree(handle)


def tray_image(color: str) -> Image.Image:
    """Create a crisp, dependency-free caption bubble tray icon."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 8, 59, 50), radius=11, fill=color, outline="white", width=3)
    draw.polygon(((17, 49), (17, 59), (29, 49)), fill=color)
    draw.line((16, 21, 48, 21), fill="white", width=5)
    draw.line((16, 31, 43, 31), fill="white", width=5)
    draw.line((16, 41, 36, 41), fill="white", width=5)
    return image


class TrayApp:
    COLORS = {
        "watching": "#2563eb",
        "recording": "#16a34a",
        "stopped": "#64748b",
        "error": "#dc2626",
        "uncertain": "#d97706",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._preferences_path = capture.application_dir() / "tray-settings.json"
        self._preferences = tray_settings.load(self._preferences_path)
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._summary_lock = threading.Lock()
        self._notes_process = None
        self._state = "stopped"
        self._status = "Watcher stopped"
        self.icon = pystray.Icon(
            "teams_caption_notes",
            tray_image(self.COLORS["stopped"]),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: self._status, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start watcher", self.start_watcher, enabled=lambda _: not self.is_running),
                pystray.MenuItem("Stop watcher", self.stop_watcher, enabled=lambda _: self.is_running),
                pystray.MenuItem("Start with Windows (at sign-in)", self.toggle_startup, checked=self.startup_checked),
                pystray.MenuItem("Hide tray pop-up notifications", self.toggle_popups, checked=self.popups_hidden),
                pystray.MenuItem("Open transcripts", self.open_transcripts, default=True),
                pystray.MenuItem("Open log", self.open_log),
                pystray.MenuItem("Chats and notes…", self.open_notes_window),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: self.copilot_menu_status, None, enabled=False),
                pystray.MenuItem("Configure Copilot…", self.configure_copilot),
                pystray.MenuItem("Sign in / test Copilot", self.sign_in_copilot),
                pystray.MenuItem("Summarize latest transcript", self.summarize_latest),
                pystray.MenuItem("Copy latest for basic Copilot Chat", self.open_basic_copilot),
                pystray.MenuItem("Copy latest for ChatGPT", self.open_chatgpt),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self.exit_app),
            ),
        )

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def startup_checked(self, item=None) -> bool:
        try:
            return windows_startup.is_enabled()
        except (OSError, ValueError):
            return False

    def popups_hidden(self, item=None) -> bool:
        return self._preferences["hide_popups"]

    def toggle_popups(self, icon=None, item=None) -> None:
        try:
            with self._lock:
                preferences = {**self._preferences, "hide_popups": not self.popups_hidden()}
                tray_settings.save(self._preferences_path, preferences)
                self._preferences = preferences
                if self.popups_hidden():
                    try:
                        self.icon.remove_notification()
                    except Exception:
                        logging.exception("Could not dismiss the current tray notification")
            logging.info("Tray pop-up notifications hidden=%s", self.popups_hidden())
            self.icon.update_menu()
        except Exception:
            logging.exception("Could not save notification preference")
            # Never announce a failed attempt to mute using another pop-up.
            self.set_status(self._state, "Could not save pop-up setting — see log")

    def toggle_startup(self, icon=None, item=None) -> None:
        try:
            enabled = not windows_startup.is_enabled()
            windows_startup.set_enabled(enabled)
            self.icon.update_menu()
            self.notify(
                "Windows startup updated",
                "This copy will start when you sign in to Windows. Keep it in this folder."
                if enabled else "Automatic startup is turned off.",
            )
        except (OSError, ValueError) as exc:
            logging.exception("Could not update Windows startup")
            self.notify("Could not change startup", str(exc))

    @property
    def copilot_menu_status(self) -> str:
        try:
            config = copilot_summary.load_config(COPILOT_CONFIG_PATH)
            if not config.configured:
                return "Copilot: setup required"
            return "Copilot: automatic summaries on" if config.enabled and config.auto_summarize else "Copilot: manual only"
        except Exception:
            return "Copilot: configuration error"

    def set_status(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._status = message[:120]
            self.icon.title = f"{APP_NAME}: {self._status}"[:127]
            self.icon.icon = tray_image(self.COLORS.get(state, self.COLORS["watching"]))
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def capture_event(self, event: str, message: str) -> None:
        logging.info("%s: %s", event, message)
        if event == "meeting_started":
            self.set_status("recording", "Meeting detected — capturing captions")
            self.notify("Caption capture started", message)
        elif event == "caption":
            self.set_status("recording", "Capturing meeting captions")
        elif event == "recording":
            self.set_status("recording", message)
        elif event == "scan_retrying":
            self.set_status("error", "Windows scan unavailable — retrying automatically")
        elif event == "scan_recovered":
            self.set_status("watching", "Windows scan recovered — watching Teams")
        elif event == "meeting_visibility_lost":
            self.set_status("uncertain", "Meeting visibility uncertain — keeping transcript open")
        elif event == "meeting_visibility_restored":
            self.set_status("recording", "Meeting visible — continuing the same transcript")
        elif event == "meeting_ended":
            self.set_status("watching", "Meeting saved — waiting for another")
            self.notify("Transcript saved", message)
        elif event == "write_blocked":
            self.set_status("recording", "Transcript locked — capture continues in memory")
            self.notify("Transcript file is open", message)
        elif event == "write_recovered":
            self.set_status("watching", "Recovery transcript saved")
            self.notify("Recovery transcript saved", message)
        elif event == "watching":
            self.set_status("watching", "Waiting for a Teams meeting")
        elif event == "stopped":
            self.set_status("stopped", "Watcher stopped")

    def notify(self, title: str, message: str) -> None:
        try:
            with self._lock:
                if self.popups_hidden():
                    logging.info("Tray pop-up suppressed: %s", title)
                    return
                self.icon.notify(message, title)
        except Exception:
            logging.exception("Unable to display tray notification")

    def start_watcher(self, icon=None, item=None) -> None:
        if self.is_running:
            return
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._watcher_main, name="teams-caption-watcher", daemon=False)
        self._worker.start()
        self.set_status("watching", "Starting Teams watcher")

    def _watcher_main(self) -> None:
        try:
            args = capture.build_parser().parse_args([])
            args.status_interval = 0
            capture.run(
                args,
                stop_event=self._stop_event,
                event_callback=self.capture_event,
                transcript_saved_callback=self.transcript_saved,
            )
        except Exception as exc:
            logging.exception("Caption watcher failed")
            self.set_status("error", f"Error: {exc}")
            self.notify("Teams Caption Notes error", f"{exc}\nSee {LOG_PATH.name} for details.")
        finally:
            if self._state != "error":
                self.set_status("stopped", "Watcher stopped")

    def stop_watcher(self, icon=None, item=None) -> None:
        if self.is_running:
            self.set_status("stopped", "Stopping watcher and saving transcript")
            self._stop_event.set()

    def open_transcripts(self, icon=None, item=None) -> None:
        folder = capture.application_dir() / "transcripts"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def open_log(self, icon=None, item=None) -> None:
        LOG_PATH.touch(exist_ok=True)
        os.startfile(LOG_PATH)

    def open_notes_window(self, icon=None, item=None) -> None:
        try:
            if self._notes_process is not None and self._notes_process.poll() is None:
                self.notify("Chats and notes", "The notes window is already open. Select it from the taskbar.")
                return
            args = [sys.executable]
            if not getattr(sys, "frozen", False):
                args.append(str(Path(__file__).resolve()))
            args.append("--notes-window")
            self._notes_process = subprocess.Popen(args, cwd=str(capture.application_dir()), creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            self.notify("Could not open notes", str(exc))

    def configure_copilot(self, icon=None, item=None) -> None:
        try:
            copilot_summary.ensure_config(COPILOT_CONFIG_PATH)
            os.startfile(COPILOT_CONFIG_PATH)
            self.icon.update_menu()
        except Exception as exc:
            self.show_copilot_error(exc)

    def sign_in_copilot(self, icon=None, item=None) -> None:
        threading.Thread(target=self._sign_in_worker, name="copilot-sign-in", daemon=True).start()

    def _sign_in_worker(self) -> None:
        try:
            config = copilot_summary.load_config(COPILOT_CONFIG_PATH)
            self.set_status("watching", "Opening Microsoft sign-in")
            copilot_summary.get_access_token(config, interactive=True)
            self.set_status("watching", "Copilot sign-in succeeded")
            self.notify("Copilot is ready", "Microsoft sign-in and required Graph consent succeeded.")
        except Exception as exc:
            self.show_copilot_error(exc)

    def transcript_saved(self, transcript_path: Path, meeting_title: str) -> None:
        try:
            config = copilot_summary.load_config(COPILOT_CONFIG_PATH)
        except Exception as exc:
            self.show_copilot_error(exc)
            return
        if config.enabled and config.auto_summarize:
            self._start_summary(transcript_path, meeting_title)

    def summarize_latest(self, icon=None, item=None) -> None:
        latest = self.latest_transcript()
        if latest is None:
            self.show_copilot_error(copilot_summary.CopilotError("No transcript files were found."))
            return
        meeting_title = latest.stem
        try:
            first_line = latest.open("r", encoding="utf-8").readline().strip()
            prefix = "# Teams meeting transcript — "
            if first_line.startswith(prefix):
                meeting_title = first_line[len(prefix):]
        except OSError:
            pass
        self._start_summary(latest, meeting_title)

    def latest_transcript(self) -> Path | None:
        return notes_library.latest_transcript(capture.application_dir() / "transcripts")

    def open_basic_copilot(self, icon=None, item=None) -> None:
        """User-assisted handoff for tenants without Copilot Graph API access."""
        self.open_ai_chat("Copilot Chat", BASIC_COPILOT_URL)

    def open_chatgpt(self, icon=None, item=None) -> None:
        self.open_ai_chat("ChatGPT", CHATGPT_URL)

    def open_ai_chat(self, service: str, url: str) -> None:
        try:
            latest = self.latest_transcript()
            if latest is None:
                raise copilot_summary.CopilotError("No transcript files were found.")
            transcript_text = latest.read_text(encoding="utf-8-sig")
            prompt = f"SOURCE FILE: {latest.name}\n\n{copilot_summary.DEFAULT_PROMPT}\n\nMEETING TRANSCRIPT:\n{transcript_text}"
            copy_to_clipboard(prompt)
            logging.info("AI handoff copied: service=%s source=%s characters=%d", service, latest.resolve(), len(prompt))
            os.startfile(url)
            self.notify(
                "Transcript copied",
                f"Copied {latest.name}\nPaste into a new {service} chat, review, then send.",
            )
        except Exception as exc:
            logging.exception("Could not prepare transcript for %s", service)
            self.notify(f"Could not open {service}", str(exc))

    def _start_summary(self, transcript_path: Path, meeting_title: str) -> None:
        if self._summary_lock.locked():
            self.notify("Copilot is busy", "Another transcript summary is already running.")
            return
        threading.Thread(
            target=self._summary_worker,
            args=(transcript_path, meeting_title),
            name="copilot-summary",
            daemon=True,
        ).start()

    def _summary_worker(self, transcript_path: Path, meeting_title: str) -> None:
        with self._summary_lock:
            try:
                self.set_status("recording", "Copilot is summarizing the transcript")
                output = copilot_summary.summarize_transcript(transcript_path, meeting_title, COPILOT_CONFIG_PATH)
                logging.info("Copilot summary saved to %s", output)
                self.set_status("watching", "Copilot summary saved")
                self.notify("Copilot summary saved", str(output))
            except Exception as exc:
                self.show_copilot_error(exc)

    def show_copilot_error(self, exc: Exception) -> None:
        logging.exception("Copilot operation failed", exc_info=(type(exc), exc, exc.__traceback__))
        self.set_status("error", f"Copilot error: {exc}")
        self.notify("Copilot summary failed", f"{exc}\nThe transcript remains saved locally.")

    def exit_app(self, icon=None, item=None) -> None:
        self._stop_event.set()
        self.icon.stop()

    def run(self) -> None:
        self.start_watcher()
        self.icon.run()
        if self._worker is not None:
            self._worker.join(timeout=10)


def acquire_single_instance():
    """Prevent duplicates with a lock Windows releases when the process exits."""
    lock_root = Path(os.environ.get("LOCALAPPDATA", str(capture.application_dir()))) / "Teams Caption Notes"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / ".instance.lock"
    lock_file = lock_path.open("a+b")
    lock_file.seek(0)
    if lock_file.read(1) == b"":
        lock_file.write(b"1")
        lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} is already running in the system tray.", APP_NAME, 0x40)
        return None
    return lock_file


def main() -> int:
    if "--notes-window" in sys.argv:
        logging.basicConfig(filename=capture.application_dir() / "notes-window.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
        from notes_window import main as notes_main
        return notes_main()
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    logging.info("Application starting from %s", capture.application_dir())
    instance_lock = acquire_single_instance()
    if instance_lock is None:
        return 0
    try:
        try:
            TrayApp().run()
            return 0
        except Exception as exc:
            logging.exception("Tray application failed during startup")
            ctypes.windll.user32.MessageBoxW(
                None,
                f"Teams Caption Notes could not start:\n\n{exc}\n\nSee {LOG_PATH} for details.",
                APP_NAME,
                0x10,
            )
            return 1
    finally:
        try:
            instance_lock.seek(0)
            msvcrt.locking(instance_lock.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            instance_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
