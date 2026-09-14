"""Opt-in, separate-process Teams caption setup window.

Opening this window never changes Teams. Only a confirmed button action invokes
the bounded setup engine, on a worker thread independent of caption capture.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import caption_setup
import teams_caption_notes as capture


MANUAL_FUTURE = "Teams Settings → Accessibility → Always show captions in my calls and meetings"
MANUAL_CURRENT = "In the meeting: More → Language and speech → Show live captions"
_ACTION_NAMES = {
    "future": "Enable captions for future calls and meetings",
    "current": "Enable captions in the current meeting",
}


class CaptionSetupWindow:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closed = False
        self.worker = None
        self.poll_id = None
        self.action_buttons = []
        root.title("Teams caption setup")
        root.geometry("760x575")
        root.minsize(760, 575)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Ready. Nothing changes until you choose an action and confirm it.")

        content = ttk.Frame(root, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Set up your Teams live captions", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            content,
            text="Caption Notes reads captions that Teams displays. These optional actions use visible Teams menus. "
                 "Keep Teams open and avoid interacting with its menus while setup runs.",
            wraplength=690,
        ).pack(anchor="w", fill="x", pady=(0, 10))
        ttk.Label(
            content,
            text="This only enables your live captions. It does not start Teams recording or change speaker "
                 "identification or profanity filtering. Teams menus may be visible to people watching your shared screen.",
            wraplength=690,
        ).pack(anchor="w", fill="x", pady=(0, 12))

        for action, heading, manual in (
            ("future", "Future calls and meetings", MANUAL_FUTURE),
            ("current", "Current meeting", MANUAL_CURRENT),
        ):
            section = ttk.LabelFrame(content, text=heading, padding=10)
            section.pack(fill="x", pady=(0, 10))
            ttk.Label(section, text=f"Or set it manually: {manual}", wraplength=650).pack(anchor="w", fill="x", pady=(0, 7))
            button = ttk.Button(section, text=_ACTION_NAMES[action], command=lambda selected=action: self.begin(selected))
            button.pack(anchor="w")
            self.action_buttons.append(button)

        result = ttk.LabelFrame(content, text="Setup result", padding=8)
        result.pack(fill="x", pady=(5, 10))
        # A bounded, scrollable result keeps Cancel/Close accessible even when
        # an engine response contains several lines of diagnostic guidance.
        self.result_box = tk.Text(result, height=5, wrap="word", state="disabled", takefocus=True)
        result_scroll = ttk.Scrollbar(result, orient="vertical", command=self.result_box.yview)
        self.result_box.configure(yscrollcommand=result_scroll.set)
        result_scroll.pack(side="right", fill="y")
        self.result_box.pack(side="left", fill="both", expand=True)
        self.status.trace_add("write", self.render_status)
        self.render_status()
        footer = ttk.Frame(content)
        footer.pack(side="bottom", fill="x")
        self.cancel_button = ttk.Button(footer, text="Cancel setup action", command=self.cancel_action, state="disabled")
        self.cancel_button.pack(side="left")
        ttk.Button(footer, text="Close", command=self.close).pack(side="right")
        self.poll_id = root.after(100, self.poll)

    def render_status(self, *args) -> None:
        if self.closed:
            return
        self.result_box.configure(state="normal")
        self.result_box.delete("1.0", "end")
        self.result_box.insert("1.0", self.status.get())
        self.result_box.configure(state="disabled")
        self.result_box.yview_moveto(0)

    def begin(self, action: str) -> None:
        if self.busy or self.closed:
            return
        if action not in _ACTION_NAMES:
            raise ValueError("Unknown caption setup action")
        target = MANUAL_FUTURE if action == "future" else MANUAL_CURRENT
        if not messagebox.askyesno(
            "Confirm Teams caption setup",
            f"{_ACTION_NAMES[action]}?\n\n"
            f"This will open visible Teams menus and try to enable only this setting:\n{target}\n\n"
            "It does not start Teams recording, change speaker identification, or change profanity filtering. "
            "Teams menus may be visible while you share your screen.\n\nContinue?",
            default=messagebox.NO,
            parent=self.root,
        ):
            return
        if self.closed:
            return
        self.busy = True
        self.cancel.clear()
        self.set_busy_controls(True)
        self.status.set("Working in Teams… Please leave its menus alone. You can cancel or close this window.")
        try:
            self.worker = threading.Thread(target=self.run_action, args=(action,), name="teams-caption-setup", daemon=True)
            self.worker.start()
        except Exception:
            self.busy = False
            self.set_busy_controls(False)
            logging.exception("Could not start caption setup worker")
            self.status.set("Setup could not start. Teams was not changed. Use the manual instructions above.")

    def set_busy_controls(self, busy: bool) -> None:
        for button in self.action_buttons:
            button.state(["disabled"] if busy else ["!disabled"])
        self.cancel_button.state(["!disabled"] if busy else ["disabled"])

    def run_action(self, action: str) -> None:
        """Worker thread: no Tk calls, clipboard operations, or capture changes."""
        try:
            if action not in _ACTION_NAMES:
                raise ValueError("Unknown caption setup action")
            if self.cancel.is_set():
                self.events.put(("cancelled", None))
                return
            auto = capture.import_uiautomation()
            with auto.UIAutomationInitializerInThread():
                if self.cancel.is_set():
                    self.events.put(("cancelled", None))
                    return
                operation = caption_setup.enable_always_show if action == "future" else caption_setup.enable_current_meeting
                result = operation(auto, self.cancel)
            self.events.put(("result", result))
        except Exception:
            logging.exception("Teams caption setup could not finish")
            self.events.put(("error", None))

    @staticmethod
    def describe_result(result) -> str:
        state = getattr(result, "state", "unverified")
        verified = getattr(result, "verified", False) is True
        detail = getattr(result, "message", "")
        if state in ("enabled", "already_enabled") and verified:
            heading = "Verified: captions were already enabled." if state == "already_enabled" else "Verified: captions are enabled."
        elif state == "cancelled":
            heading = "Setup was cancelled. Check Teams to see whether a change had already completed."
        elif state == "unavailable":
            heading = "Setup is unavailable in the current Teams window. Use the manual instructions above."
        else:
            heading = "Not verified: setup could not confirm that captions are enabled. Check Teams or use the manual instructions above."
        return f"{heading}\n{detail}" if isinstance(detail, str) and detail.strip() else heading

    def poll(self) -> None:
        if self.closed:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self.busy = False
                self.set_busy_controls(False)
                if kind == "result":
                    text = self.describe_result(payload)
                elif kind == "cancelled":
                    text = "Setup was cancelled before the requested action started."
                else:
                    text = "Setup could not finish because Windows could not read or operate the Teams controls. " \
                           "The result is not verified; check Teams or use the manual instructions above. Caption capture is independent."
                self.status.set(text)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(100, self.poll)

    def cancel_action(self) -> None:
        if self.busy and not self.closed:
            self.cancel.set()
            self.cancel_button.state(["disabled"])
            self.status.set("Cancellation requested. A Windows accessibility call may need to finish first.")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel.set()
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    CaptionSetupWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
