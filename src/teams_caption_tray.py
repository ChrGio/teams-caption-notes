#!/usr/bin/env python3
"""System-tray front end for Teams Caption Notes."""

from __future__ import annotations

import ctypes
import hashlib
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
import app_update
import windows_clipboard


APP_VERSION = "4.5.3"
APP_NAME = f"Teams Caption Notes v{APP_VERSION}"
LOG_PATH = capture.application_dir() / "teams-caption-notes.log"
COPILOT_CONFIG_PATH = capture.application_dir() / "copilot-config.json"
BASIC_COPILOT_URL = "https://m365.cloud.microsoft/chat"
CHATGPT_URL = "https://chatgpt.com/"


def copy_to_clipboard(text: str) -> None:
    """Copy and verify the exact Unicode payload before reporting success."""
    windows_clipboard.copy_text(text)


def open_chat_destination(service: str, url: str) -> None:
    # Opening a website cannot clear a browser's existing conversation/draft.
    # The user must choose New chat and paste the verified clipboard contents.
    os.startfile(url)


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
        self._new_install = not self._preferences_path.exists()
        self._preferences = tray_settings.load(self._preferences_path)
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._summary_lock = threading.Lock()
        self._update_lock = threading.Lock()
        self._installing = False
        self._exiting = threading.Event()
        self._meeting_active = False
        self._update_capture_started = threading.Event()
        self._update_status = "Updates: not checked"
        self._handoff_status = "AI copy: no transcript copied this session"
        self._sign_in_thread = None
        self._notes_process = None
        self._caption_setup_process = None
        self._state = "stopped"
        self._status = "Watcher stopped"
        self.icon = pystray.Icon(
            "teams_caption_notes",
            tray_image(self.COLORS["stopped"]),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: self._status, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start watcher", self.start_watcher, enabled=lambda _: not self.is_running and not self._installing),
                pystray.MenuItem("Stop watcher", self.stop_watcher, enabled=lambda _: self.is_running),
                pystray.MenuItem("Start with Windows (at sign-in)", self.toggle_startup, checked=self.startup_checked),
                pystray.MenuItem("Hide tray pop-up notifications", self.toggle_popups, checked=self.popups_hidden),
                pystray.MenuItem(lambda _: self.caption_setup_status, None, enabled=False),
                pystray.MenuItem("Set up Teams captions…", self.open_caption_setup, enabled=lambda _: not self._installing),
                pystray.MenuItem("Open transcripts", self.open_transcripts, default=True),
                pystray.MenuItem("Open log", self.open_log),
                pystray.MenuItem("Chats and notes…", self.open_notes_window, enabled=lambda _: not self._installing),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: self.copilot_menu_status, None, enabled=False),
                pystray.MenuItem("Configure Copilot…", self.configure_copilot),
                pystray.MenuItem("Sign in / test Copilot", self.sign_in_copilot),
                pystray.MenuItem("Summarize latest transcript", self.summarize_latest),
                pystray.MenuItem("Copy latest for basic Copilot Chat", self.open_basic_copilot),
                pystray.MenuItem("Copy latest for ChatGPT", self.open_chatgpt),
                pystray.MenuItem("Copy latest for AI (no browser)", self.copy_latest_only),
                pystray.MenuItem(lambda _: self._handoff_status, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: self._update_status, None, enabled=False),
                pystray.MenuItem("Check for updates…", self.check_for_updates, enabled=lambda _: not self._update_lock.locked()),
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
            with self._lock:
                enabled = not windows_startup.is_enabled()
                try:
                    windows_startup.set_preference(enabled, self._preferences_path)
                finally:
                    self._preferences = tray_settings.load(self._preferences_path)
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
        # Keep session ownership separate from tray color: recovery and Copilot
        # status messages must not make an active meeting appear safe to update.
        if event in ("meeting_started", "meeting_ended"):
            with self._lock:
                self._meeting_active = event == "meeting_started"
                if event == "meeting_started" and self._installing:
                    self._update_capture_started.set()
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
            self.set_status("uncertain", message)
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
        with self._lock:
            if self.is_running or self._installing or self._exiting.is_set():
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
            with self._lock:
                if self._installing or self._exiting.is_set():
                    return
                already_open = self._notes_process is not None and self._notes_process.poll() is None
                if not already_open:
                    args = [sys.executable]
                    if not getattr(sys, "frozen", False):
                        args.append(str(Path(__file__).resolve()))
                    args.append("--notes-window")
                    environment = os.environ.copy()
                    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                    self._notes_process = subprocess.Popen(args, cwd=str(capture.application_dir()), env=environment,
                                                           creationflags=subprocess.CREATE_NO_WINDOW)
            if already_open:
                self.notify("Chats and notes", "The notes window is already open. Select it from the taskbar.")
        except OSError as exc:
            self.notify("Could not open notes", str(exc))

    @property
    def caption_setup_status(self) -> str:
        if self._preferences.get("caption_setup_seen") is True:
            return "Teams captions: setup available in menu"
        return "Teams captions: setup recommended"

    def open_caption_setup(self, icon=None, item=None) -> None:
        """Open a separate, opt-in UI; opening it never changes Teams settings."""
        try:
            with self._lock:
                if self._installing or self._exiting.is_set():
                    return
                already_open = self._caption_setup_process is not None and self._caption_setup_process.poll() is None
                if not already_open:
                    args = [sys.executable]
                    if not getattr(sys, "frozen", False):
                        args.append(str(Path(__file__).resolve()))
                    args.append("--caption-setup")
                    environment = os.environ.copy()
                    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                    self._caption_setup_process = subprocess.Popen(
                        args, cwd=str(capture.application_dir()), env=environment,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    try:
                        # This tracks onboarding display, NOT whether the user's
                        # Teams setting was successfully enabled or stayed on.
                        tray_settings.save(self._preferences_path, {"caption_setup_seen": True})
                        self._preferences = tray_settings.load(self._preferences_path)
                    except (OSError, ValueError):
                        logging.exception("Could not remember caption setup display")
            if already_open:
                self.notify("Teams caption setup", "The setup window is already open. Select it from the taskbar.")
            self.icon.update_menu()
        except Exception as exc:
            logging.exception("Could not open caption setup")
            self.notify("Could not open caption setup", f"{exc}\nSee {LOG_PATH.name} for details.")

    def _offer_first_run_setup(self) -> None:
        if (getattr(sys, "frozen", False) and self._new_install
                and "--startup" not in sys.argv
                and self._preferences.get("caption_setup_seen") is not True):
            self.open_caption_setup()

    def configure_copilot(self, icon=None, item=None) -> None:
        try:
            copilot_summary.ensure_config(COPILOT_CONFIG_PATH)
            os.startfile(COPILOT_CONFIG_PATH)
            self.icon.update_menu()
        except Exception as exc:
            self.show_copilot_error(exc)

    def sign_in_copilot(self, icon=None, item=None) -> None:
        with self._lock:
            if self._installing or self._exiting.is_set() or (self._sign_in_thread and self._sign_in_thread.is_alive()):
                return
            self._sign_in_thread = threading.Thread(target=self._sign_in_worker, name="copilot-sign-in", daemon=True)
            self._sign_in_thread.start()

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

    def copy_latest_only(self, icon=None, item=None) -> None:
        self.open_ai_chat("Clipboard", None)

    def _set_handoff_status(self, status: str) -> None:
        # Keep this separate from watcher status: caption events must not erase it.
        self._handoff_status = status
        icon = getattr(self, "icon", None)
        if icon is not None:
            try:
                icon.update_menu()
            except Exception:
                logging.exception("Could not refresh AI copy status")

    def open_ai_chat(self, service: str, url: str | None) -> None:
        self._set_handoff_status(f"AI copy: preparing for {service}…")
        try:
            latest = self.latest_transcript()
            if latest is None:
                raise copilot_summary.CopilotError("No transcript files were found.")
            prompt = notes_library.handoff_prompt([latest], copilot_summary.DEFAULT_PROMPT)
            copy_to_clipboard(prompt)
        except Exception as exc:
            self._set_handoff_status("AI copy FAILED — clipboard may contain old text; see log")
            logging.exception("Could not prepare transcript for %s", service)
            self.notify(f"Could not open {service}", str(exc))
            return
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        logging.info("AI handoff copied and verified: service=%s source=%s characters=%d sha256=%s",
                     service, latest.resolve(), len(prompt), digest)
        if url is not None:
            try:
                open_chat_destination(service, url)
            except Exception as exc:
                self._set_handoff_status(f"Copied {latest.name} — browser did not open; paste manually")
                logging.exception("Transcript copied, but browser launch failed for %s", service)
                self.notify("Transcript copied; browser did not open", str(exc))
                return
        try:
            windows_clipboard.verify_text(prompt)
        except Exception as exc:
            self._set_handoff_status("AI copy NOT VERIFIED — clipboard changed or is busy; copy again")
            logging.exception("AI handoff clipboard verification failed after launch: service=%s source=%s",
                              service, latest.resolve())
            self.notify("Check your clipboard before pasting", str(exc))
            return
        self._set_handoff_status(f"Copied: {latest.name} — paste with Ctrl+V in a new chat")
        logging.info("AI handoff ready: service=%s source=%s sha256=%s", service, latest.resolve(), digest)
        self.notify("Transcript copied", f"Copied {latest.name}\nChoose New chat, paste with Ctrl+V, review, then send.")

    def _start_summary(self, transcript_path: Path, meeting_title: str) -> None:
        with self._lock:
            if self._installing or self._exiting.is_set():
                return
            acquired = self._summary_lock.acquire(blocking=False)
        if not acquired:
            self.notify("Copilot is busy", "Another transcript summary is already running.")
            return
        try:
            threading.Thread(
                target=self._summary_worker,
                args=(transcript_path, meeting_title),
                name="copilot-summary",
                daemon=True,
            ).start()
        except Exception:
            self._summary_lock.release()
            raise

    def _summary_worker(self, transcript_path: Path, meeting_title: str) -> None:
        try:
            try:
                self.set_status("recording", "Copilot is summarizing the transcript")
                output = copilot_summary.summarize_transcript(transcript_path, meeting_title, COPILOT_CONFIG_PATH)
                logging.info("Copilot summary saved to %s", output)
                self.set_status("watching", "Copilot summary saved")
                self.notify("Copilot summary saved", str(output))
            except Exception as exc:
                self.show_copilot_error(exc)
        finally:
            self._summary_lock.release()

    def show_copilot_error(self, exc: Exception) -> None:
        logging.exception("Copilot operation failed", exc_info=(type(exc), exc, exc.__traceback__))
        self.set_status("error", f"Copilot error: {exc}")
        self.notify("Copilot summary failed", f"{exc}\nThe transcript remains saved locally.")

    def _update_message(self, message: str, *, confirm: bool = False) -> bool:
        # Responses to a requested action still appear when balloons are hidden.
        if self._exiting.is_set():
            return False
        flags = (0x04 | 0x20 | 0x100) if confirm else 0x40  # Yes/No, default No
        return ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} — Updates", flags) == 6

    def _set_update_status(self, message: str) -> None:
        with self._lock:
            self._update_status = message
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _installation_blocker(self) -> str | None:
        """Called holding _lock to exclude competing tray operations."""
        if self._meeting_active or self._update_capture_started.is_set():
            return "A meeting is being captured (or its last save is still pending). Finish the meeting and try again."
        if self._notes_process is not None and self._notes_process.poll() is None:
            return "Close the Chats and notes window, then try again."
        if self._caption_setup_process is not None and self._caption_setup_process.poll() is None:
            return "Close the Teams caption setup window, then try again."
        if self._summary_lock.locked():
            return "Wait for the current Copilot summary to finish, then try again."
        if self._sign_in_thread is not None and self._sign_in_thread.is_alive():
            return "Finish Microsoft sign-in, then try again."
        if self._exiting.is_set():
            return "The application is exiting."
        return None

    def check_for_updates(self, icon=None, item=None) -> None:
        if self._exiting.is_set() or not self._update_lock.acquire(blocking=False):
            return
        try:
            threading.Thread(target=self._check_update_worker, name="app-update", daemon=True).start()
        except Exception:
            self._update_lock.release()
            raise

    def _check_update_worker(self) -> None:
        restart_watcher = False
        helper_started = False
        try:
            self._set_update_status("Updates: checking GitHub…")
            release = app_update.check_for_update(APP_VERSION)
            if self._exiting.is_set():
                return
            if release is None:
                self._set_update_status(f"Updates: v{APP_VERSION} is up to date")
                self._update_message(f"You are running v{APP_VERSION}. No newer stable release is available.")
                return
            self._set_update_status(f"Updates: v{release.version} available")
            if not getattr(sys, "frozen", False):
                self._update_message(f"Version {release.version} is available. In-app installation works in the packaged EXE only.\n\n{release.html_url}")
                return
            with self._lock:
                self._update_capture_started.clear()
                blocker = self._installation_blocker()
            if blocker:
                self._update_message(f"Version {release.version} is available.\n\n{blocker}")
                return
            if not self._update_message(
                f"Install Teams Caption Notes v{release.version}?\n\n"
                "The EXE will download from ChrGio/teams-caption-notes on GitHub and its SHA-256 checksum will be verified. "
                "The app will close and restart after installation. Your transcripts and settings stay in this folder.\n\n"
                "The app is unsigned; your organization's Windows security policies still apply.", confirm=True
            ):
                return
            with self._lock:
                blocker = self._installation_blocker()
                if not blocker:
                    self._installing = True
            if blocker:
                raise app_update.UpdateError(blocker)
            self._set_update_status(f"Updates: downloading v{release.version}…")
            install_path = Path(sys.executable).resolve()
            candidate = app_update.download_update(release, install_path)
            with self._lock:
                blocker = self._installation_blocker()
                if not blocker:
                    restart_watcher = self.is_running
                    self._stop_event.set()
            if blocker:
                raise app_update.UpdateError(blocker)
            self._set_update_status("Updates: waiting for watcher to save and stop…")
            if self._worker is not None:
                self._worker.join(timeout=30)
            if self.is_running:
                raise app_update.UpdateError("The watcher has not finished saving. Nothing was installed. Wait for it to stop, then try again.")
            with self._lock:
                blocker = self._installation_blocker()
            if blocker:
                raise app_update.UpdateError(blocker)
            self._set_update_status("Updates: preparing installer…")
            manifest = app_update.prepare_install(candidate, install_path, release)
            if self._exiting.is_set():
                return
            # Returns only after the helper validates its plan and is ready.
            app_update.launch_installer(manifest)
            helper_started = True
            logging.info("Update helper ready for v%s; exiting for installation", release.version)
            self.exit_app()
        except Exception as exc:
            logging.exception("App update did not complete")
            self._set_update_status("Updates: not installed — see log")
            self._update_message(f"The update was not installed. Your current EXE and notes have not been replaced.\n\n{exc}\n\nSee {LOG_PATH.name} for details.")
        finally:
            with self._lock:
                self._installing = False
            if restart_watcher and not helper_started and not self._exiting.is_set() and not self.is_running:
                self.start_watcher()
            self._update_lock.release()
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def exit_app(self, icon=None, item=None) -> None:
        self._exiting.set()
        self._stop_event.set()
        self.icon.stop()

    def run(self) -> None:
        try:
            windows_startup.apply_default(self._preferences_path)
        except (OSError, ValueError):
            logging.exception("Could not apply Windows startup preference; use the tray startup option or contact IT")
        finally:
            self._preferences = tray_settings.load(self._preferences_path)
        self._offer_first_run_setup()
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
    if "--apply-update" in sys.argv:
        if len(sys.argv) != 3 or sys.argv[1] != "--apply-update":
            return 1
        return app_update.apply_update(Path(sys.argv[2]))
    if "--caption-setup" in sys.argv:
        if len(sys.argv) != 2 or sys.argv[1] != "--caption-setup":
            return 1
        logging.basicConfig(filename=capture.application_dir() / "caption-setup.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
        from caption_setup_window import main as caption_setup_main
        return caption_setup_main()
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
    logging.info("%s starting from %s", APP_NAME, capture.application_dir())
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
