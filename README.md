# Teams Caption Notes

**Turn Microsoft Teams live captions into meeting notes you can keep.**

A Windows system-tray app that saves visible Teams captions as readable Markdown
transcripts, with meeting titles, timestamps, and speaker names when available.
No microphone or system audio is recorded.

## Download and get started

### [Download the Windows app (.exe)](https://github.com/ChrGio/teams-caption-notes/releases/latest)

**Windows 10/11 · x64 · No Python required**

On the release page, download the `TeamsCaptionNotes-v*.exe` asset.
You do not need GitHub's **Source code** ZIP.

1. Put the EXE in a permanent folder where you can save files, then open it.
2. Find the app icon near the Windows clock; check the hidden-icons arrow if needed.
3. Join a Teams meeting and turn on live captions. The app's **Set up Teams captions…** option can
   help enable them for the current meeting or future meetings, after confirmation.
4. The app watches for meetings and saves a separate transcript
   for each one. Right-click the tray icon → **Open transcripts** to find your notes.

The packaged app starts with Windows **at sign-in** by default. Uncheck
**Start with Windows (at sign-in)** in the tray menu to turn that off.
The executable is unsigned; your organization's IT approval may be required.

## What it does

- **One file per meeting** — meeting title and capture-start time in the filename.
- **Readable caption history** — merges rolling text updates and reduces repeated
  lines; preserves speaker labels when Teams exposes them.
- **Built-in and pop-out captions** — supports captions inside the meeting window
  and Teams' detached caption viewer.
- **Quiet tray controls** — status colors show waiting, capture, and recovery.
  Capture-start pop-ups are hidden by default.
- **Notes and AI handoff** — browse/search saved notes and copy the latest or
  selected transcript with a summary prompt for ChatGPT or Copilot.
- **Recovery and updates** — retries temporary accessibility/file errors, keeps
  recovery journals, and offers checked, user-confirmed updates from the tray.

Copying to ChatGPT or Copilot opens the website and puts the prompt on your
clipboard. **You review, paste, and send it**; browser summaries are not
automatically collected or saved.

## Where your notes go

By default, the EXE saves meeting notes in a **`transcripts` folder beside itself**.
For example: `Team_standup-20260914-093000.md`.

Settings and `teams-caption-notes.log` are also kept beside the EXE.
Keep this folder when updating. The tray's **Open log** command opens the log;
**Check for updates…** offers an update when one is available.

## Good to know

Captions must be enabled and accessible to Windows. Keep Teams and its caption
source visible for the best results. The app can preserve a meeting session when
its window is minimized, but **cannot recover caption text Teams does not expose**.
Caption wording, speaker labels, and meeting detection can be imperfect.

Tell participants and follow your organization's rules before saving or sharing
meeting text. Files stay in your chosen folder, which may be synced by OneDrive
or other software. Optional AI/chat workflows can send or retrieve data online.

Basic caption capture and copy-to-AI need **no Graph app registration or API key**.
Optional Teams chat collection and direct Copilot API summaries have separate
Microsoft sign-in, permission, and licensing requirements.

## Learn more

- [User guide: caption setup, tray controls, updates, and troubleshooting](docs/USER_GUIDE.md)
- [Browse notes and optionally collect Teams chats](docs/CHAT_NOTES_SETUP.md)
- [Optional direct Microsoft 365 Copilot summaries](docs/COPILOT_SETUP.md)
- [What's new in v4.5](docs/RELEASE_NOTES-v4.5.md) · [Version history and backlog](docs/HISTORY_AND_BACKLOG.md)
- [Run from source, test, or build the EXE](docs/DEVELOPMENT.md)
- [Report a bug](https://github.com/ChrGio/teams-caption-notes/issues) — remove meeting
  text, account details, and other sensitive data before sharing diagnostics.

Application code lives in [src/](https://github.com/ChrGio/teams-caption-notes/tree/main/src),
automated tests in [tests/](https://github.com/ChrGio/teams-caption-notes/tree/main/tests),
and developer utilities in [scripts/](https://github.com/ChrGio/teams-caption-notes/tree/main/scripts).
