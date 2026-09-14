"""Desktop notes library and explicitly selected Teams chat collection."""
from __future__ import annotations

import json
import logging
import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import copilot_summary
import notes_library
import teams_chat
import teams_caption_notes as capture


def setup_guide_path(base: Path) -> Path:
    """Keep the EXE's guide location; source checkouts use the docs directory."""
    if getattr(sys, "frozen", False):
        return base / "CHAT_NOTES_SETUP.md"
    return Path(__file__).resolve().parents[1] / "docs" / "CHAT_NOTES_SETUP.md"


class NotesWindow:
    def __init__(self, root):
        self.root = root
        self.base = capture.application_dir()
        self.config_path = self.base / "chat-config.json"
        self.library_path = self.base / "notes-library.json"
        self.library = notes_library.load_library(self.library_path)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.client = None
        self.chats = []
        self.rows = []
        self.buttons = []
        root.title("Teams Caption Notes v4.5 — Chats and notes")
        root.geometry("1000x680")
        root.minsize(820, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Ready. Chat collection runs only when you request it.")
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        chats = ttk.Frame(tabs, padding=12)
        notes = ttk.Frame(tabs, padding=12)
        tabs.add(notes, text="Saved notes")
        tabs.add(chats, text="Teams chats")
        self.build_notes(notes)
        self.build_chats(chats)
        ttk.Label(root, textvariable=self.status, wraplength=940).pack(fill="x", padx=12, pady=(0, 12))
        root.after(100, self.poll)
        self.refresh_notes()

    def button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=lambda: self.guard(command))
        button.pack(side="left", padx=(0, 6), pady=4)
        self.buttons.append(button)
        return button

    def guard(self, command):
        try:
            command()
        except Exception as exc:
            messagebox.showerror("Teams Caption Notes", str(exc), parent=self.root)

    def run_job(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for button in self.buttons:
            button.state(["disabled"])
        def worker():
            try:
                self.events.put(("done", (done, work())))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                    continue
                self.busy = False
                for button in self.buttons:
                    button.state(["!disabled"])
                if kind == "error":
                    self.status.set(payload)
                    messagebox.showerror("Operation did not finish", payload, parent=self.root)
                else:
                    done, value = payload
                    self.guard(lambda: done(value))
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def build_chats(self, parent):
        ttk.Label(parent, text="Collect selected chats into daily notes. Requires an Entra app with delegated Chat.Read permission.", wraplength=920).pack(anchor="w")
        fields = ttk.Frame(parent)
        fields.pack(fill="x", pady=8)
        saved = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
        self.tenant = tk.StringVar(value=saved.get("tenant_id", ""))
        self.client_id = tk.StringVar(value=saved.get("client_id", ""))
        for row, (label, value) in enumerate((("Tenant ID", self.tenant), ("Client ID", self.client_id))):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            ttk.Entry(fields, textvariable=value, width=60).grid(row=row, column=1, sticky="ew")
        fields.columnconfigure(1, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x")
        self.button(toolbar, "Save setup", self.save_setup)
        self.button(toolbar, "Sign in and load chats", self.load_chats)
        self.button(toolbar, "Setup guide", lambda: os.startfile(setup_guide_path(self.base)))
        dates = ttk.Frame(parent)
        dates.pack(fill="x", pady=6)
        self.start = tk.StringVar(value=str(date.today()))
        self.end = tk.StringVar(value=str(date.today()))
        for label, value in (("From", self.start), ("Through", self.end)):
            ttk.Label(dates, text=label).pack(side="left", padx=5)
            ttk.Entry(dates, textvariable=value, width=13).pack(side="left")
        ttk.Label(dates, text="YYYY-MM-DD · PC local dates").pack(side="left", padx=10)
        ttk.Label(parent, text="Select conversations (Ctrl or Shift for multiple). No chat is selected automatically.").pack(anchor="w")
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=6)
        self.chat_list = tk.Listbox(frame, selectmode="extended", exportselection=False)
        scrollbar = ttk.Scrollbar(frame, command=self.chat_list.yview)
        self.chat_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.chat_list.pack(fill="both", expand=True)
        footer = ttk.Frame(parent)
        footer.pack(fill="x")
        self.button(footer, "Collect selected chats", self.collect_chats)
        ttk.Button(footer, text="Cancel download", command=self.cancel.set).pack(side="left", padx=5)
        self.button(footer, "Open chat notes", self.open_chat_folder)

    def save_setup(self):
        teams_chat.save_settings(self.config_path, self.tenant.get(), self.client_id.get())
        self.status.set("Chat setup saved. Use Sign in and load chats to connect.")

    def load_chats(self):
        self.save_setup()
        if self.client:
            self.client.session.close()
        self.chat_list.delete(0, "end")
        self.chats = []
        self.client = None
        self.status.set("Sign in with your work account in the Microsoft browser window.")
        def work():
            client = teams_chat.GraphClient(teams_chat.token_provider(self.config_path), self.cancel)
            try:
                return client, client.chats()
            except Exception:
                client.session.close()
                raise
        def done(result):
            self.client, self.chats = result
            for chat in self.chats:
                self.chat_list.insert("end", f"{teams_chat.chat_title(chat)}   [{chat.get('chatType', 'chat')}]")
            self.status.set(f"Loaded {len(self.chats)} chats. Select chats and dates, then collect.")
        self.run_job(work, done)

    def collect_chats(self):
        if self.client is None:
            raise ValueError("Sign in and load chats first.")
        selected = [self.chats[i] for i in self.chat_list.curselection()]
        if not selected:
            raise ValueError("Select at least one chat.")
        start, end = date.fromisoformat(self.start.get()), date.fromisoformat(self.end.get())
        if start > end:
            raise ValueError("The start date must be on or before the end date.")
        folder = self.base / "chat-notes" / f"collection-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        self.status.set("Collecting selected chats…")
        def work():
            return teams_chat.export_chats(self.client, selected, start, end, folder,
                lambda text: self.events.put(("status", text)))
        def done(result):
            output, complete = result
            self.status.set(f"{'Saved' if complete else 'PARTIAL export saved'}: {output}. Refresh Saved notes to summarize.")
            os.startfile(output / "COLLECTION.md")
        self.run_job(work, done)

    def open_chat_folder(self):
        folder = self.base / "chat-notes"
        folder.mkdir(exist_ok=True)
        os.startfile(folder)

    def build_notes(self, parent):
        ttk.Label(parent, text="Search meeting transcripts and collected chats. Add your previous app's transcript folder to include older notes.", wraplength=920).pack(anchor="w")
        search = ttk.Frame(parent)
        search.pack(fill="x", pady=8)
        self.query = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda e: self.guard(self.refresh_notes) if not self.busy else None)
        self.button(search, "Search / refresh", self.refresh_notes)
        self.button(search, "Add folder", self.add_folder)
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        self.note_list = ttk.Treeview(frame, columns=("project", "file", "folder"), show="headings", selectmode="extended")
        for key, title, width in (("project", "Project", 140), ("file", "Note", 300), ("folder", "Folder", 400)):
            self.note_list.heading(key, text=title)
            self.note_list.column(key, width=width)
        scroll = ttk.Scrollbar(frame, command=self.note_list.yview)
        self.note_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.note_list.pack(fill="both", expand=True)
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=6)
        self.button(actions, "Open note", self.open_note)
        self.button(actions, "Set project", self.set_project)
        self.button(actions, "Copy selected → ChatGPT", lambda: self.handoff("ChatGPT", "https://chatgpt.com/"))
        self.button(actions, "Copy selected → Copilot", lambda: self.handoff("Copilot Chat", "https://m365.cloud.microsoft/chat"))
        self.button(actions, "Prepare long notes", self.prepare)
        extra = ttk.Frame(parent)
        extra.pack(fill="x")
        self.button(extra, "Copilot API summary", self.api_summary)
        self.button(extra, "Recover meeting journal", self.recover)
        ttk.Label(parent, text="AI web commands copy a prompt; you paste and send it. Long inputs are prepared as numbered parts.\nCopilot API summaries require the separate licensed Copilot setup.", wraplength=920).pack(anchor="w", pady=6)

    def save_library(self):
        capture.atomic_write(self.library_path, json.dumps(self.library, indent=2))

    def add_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Choose a notes folder")
        if folder and folder not in self.library["folders"]:
            self.library["folders"].append(folder)
            self.save_library()
            self.refresh_notes()

    def refresh_notes(self):
        selected = {self.rows[int(i)]["path"] for i in self.note_list.selection()}
        roots = [self.base / "transcripts", self.base / "chat-notes", *self.library["folders"]]
        query = self.query.get()
        self.status.set("Searching notes…")
        def done(result):
            self.rows, errors = result
            self.note_list.delete(*self.note_list.get_children())
            for i, row in enumerate(self.rows):
                self.note_list.insert("", "end", iid=str(i), values=(row["project"], row["path"].name, str(row["path"].parent)))
                if row["path"] in selected:
                    self.note_list.selection_add(str(i))
            self.status.set(f"{len(self.rows)} notes found. {len(errors)} unreadable files." + (f" {errors[0]}" if errors else ""))
        self.run_job(lambda: notes_library.search_notes(roots, query, self.library["projects"]), done)

    def selected_paths(self):
        paths = [self.rows[int(i)]["path"] for i in self.note_list.selection()]
        if not paths:
            raise ValueError("Select one or more notes first.")
        return paths

    def open_note(self):
        os.startfile(self.selected_paths()[0])

    def set_project(self):
        paths = self.selected_paths()
        project = simpledialog.askstring("Project", "Project label (blank removes it):", parent=self.root)
        if project is not None:
            for path in paths:
                self.library["projects"][str(path)] = project.strip()
            self.save_library()
            self.refresh_notes()

    def prepare(self, paths=None):
        paths = paths or self.selected_paths()
        self.status.set("Preparing numbered summary prompts…")
        def done(folder):
            os.startfile(folder)
            self.status.set(f"Summary prompts prepared in {folder}. Follow README.txt.")
        self.run_job(lambda: notes_library.prepare_bundle(paths, self.base / "summary-inputs"), done)

    def handoff(self, name, url):
        from teams_caption_tray import copy_to_clipboard
        paths = self.selected_paths()
        text = "\n\n".join(f"SOURCE FILE: {p.name}\n{p.read_text(encoding='utf-8-sig')}" for p in paths)
        if len(text) > 20000:
            self.prepare(paths)
            return
        names = ", ".join(p.name for p in paths)
        prompt = f"SELECTED FILES: {names}\n\n{notes_library.SUMMARY_PROMPT}\n\nBEGIN SOURCE DATA\n{text}\nEND SOURCE DATA"
        copy_to_clipboard(prompt)
        logging.info("AI handoff copied: service=%s sources=%s characters=%d", name,
                     [str(p.resolve()) for p in paths], len(prompt))
        self.status.set(f"Copied: {names}. Paste into a new {name} chat, review, and send.")
        os.startfile(url)

    def api_summary(self):
        paths = self.selected_paths()
        if len(paths) != 1:
            raise ValueError("Select one note for the API summary, or prepare multiple notes as parts.")
        path = paths[0]
        self.status.set("Requesting a Copilot API summary…")
        self.run_job(lambda: copilot_summary.summarize_transcript(path, path.stem, self.base / "copilot-config.json"),
                     lambda output: (os.startfile(output), self.status.set(f"Summary saved: {output}")))

    def recover(self):
        from caption_journal import recover_journal
        selected = filedialog.askopenfilename(parent=self.root, title="Recover caption journal", filetypes=[("Caption journals", "*.jsonl")])
        if selected:
            self.run_job(lambda: recover_journal(Path(selected)),
                         lambda output: (os.startfile(output), self.status.set(f"Recovered notes: {output}")))

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Cancellation requested. Wait for the current operation to finish, then close.")
            return
        if self.client:
            self.client.session.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        NotesWindow(root)
    except Exception as exc:
        messagebox.showerror("Could not open notes", str(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0
