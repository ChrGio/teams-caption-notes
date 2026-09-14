# Teams Caption Notes

Version 4.5 adds an **opt-in Teams caption setup window**. It can help enable
captions for the current meeting or set Teams' **Always show captions in my
calls and meetings** preference when the installed client exposes those controls.
Opening setup does not change Teams; each change requires a button click and
confirmation. See [First-time caption setup](#first-time-caption-setup) and the
[v4.5 release notes](RELEASE_NOTES-v4.5.md).

Version 4.4 adds **Check for updates…** to the tray menu and makes **Start with
Windows (at sign-in)** the default for the packaged executable. Update checks are
manual, installation requires confirmation, and saved startup opt-outs are
respected. See [Updating the portable app](#updating-the-portable-app) and the
[v4.4 release notes](RELEASE_NOTES-v4.4.md).

Version 4.3 fixes false meeting splits when a previously detected meeting window
is minimized, compact, or temporarily exposes no meeting controls. It tracks that
specific window (handle, process, and title), including a native-window check when
UI Automation omits it. The ordinary Teams home/chat window alone does not keep a
meeting open. Hidden windows are tracked but their caption trees are not read.

The tray turns amber when meeting visibility is uncertain; the same transcript
remains open and saving. Visibility loss/restoration and closure decisions are
logged. After tracked meeting windows disappear, a full eight-second confirmation
period is required before finalizing. A clearly different named meeting starts a
new transcript. Brief same-meeting reconnects inside the grace period stay together.

This is window-based detection, not an official Teams call-state API. A stale
post-call/caption window can delay finalization until closed; multiple concurrent
meetings are not reliably isolated. It cannot recover captions that Teams stops
exposing while hidden. Use **Stop watcher** to save/finalize manually if needed.

Version 4.2 adds **Hide tray pop-up notifications**, enabled by default. The
right-click tray checkbox controls all app tray balloons, including capture start,
save confirmations, and errors. Tray colors, status text, and logs still update.
The choice is saved beside the executable in `tray-settings.json` and survives
restarts. Enabling it also dismisses the currently displayed app tray notification.
It does not change Teams notifications, automatically detect screen sharing, or
hide dialogs in windows you deliberately open (such as sign-in/setup/errors).
The v4.5 welcome/setup window is a one-time first manual launch dialog for a new
installation, not a capture-start balloon; it never opens at Windows sign-in.

UI Automation COM failures while scanning windows are retried with a 2–30 second
backoff. The tray turns red while retrying. The active transcript is preserved and
an unreadable scan does not count as leaving the meeting. Error logs are throttled
to once per minute during an outage. This handles returned COM errors, not calls
that hang indefinitely; captions that disappear during the outage may be missed.

Version 4.1 orders meeting notes by their capture-start time (not later file edits).
Tray **Copy latest** uses the newest meeting in this executable's `transcripts` folder;
**Copy selected → ChatGPT** in the notes window uses only the highlighted rows.
The copied prompt and confirmation identify the filenames. Successful handoffs are
logged without transcript contents (`teams-caption-notes.log` for tray actions,
`notes-window.log` for notes-window actions). Refresh preserves selection by file.
Web handoffs still require pasting into a new chat; they do not upload or send automatically.

Version 4 adds **Chats and notes…**: select Teams chats and dates for daily exports, search notes across folders, assign project labels, prepare long inputs for AI summaries, and recover meeting journals. See [CHAT_NOTES_SETUP.md](CHAT_NOTES_SETUP.md) for setup and limitations. Chat downloads require a tenant-approved Entra app with delegated `Chat.Read`; they do not use the Copilot summary API.

Meeting capture now journals each changed batch and refreshes Markdown about every ten seconds, with an immediate final save. Chat downloads, AI work, and the notes window run independently of capture.

A local Windows utility that watches the accessibility tree of an active Microsoft Teams meeting, extracts visible live captions, removes repeated/rolling updates, and continuously saves an AI-friendly Markdown transcript.

Both captions shown inside the meeting and the detached **Captions — Pinned window — Web content** viewer are supported.

When the detached viewer is open, it is treated as the authoritative caption source. This prevents lagging copies from other Teams windows from being repeated under the wrong speaker. Rolling caption updates are merged, substantial verbatim replays are removed, and sentence-like text such as “Hi, Jordan.” is not accepted as a participant name.

It does **not** record microphone or system audio. The watcher reads captions
without clicking Teams controls; the separate v4.5 setup helper can operate only
the supported caption controls after explicit confirmation. Captions must be
enabled before text can be captured. Transcripts stay local unless a user
explicitly sends them through an optional AI workflow or their folder is synced.

## Install

For the standalone Windows x64 app, download the executable from the project's
[GitHub releases](https://github.com/ChrGio/teams-caption-notes/releases), put it in
a permanent folder where you can write files, then run it. Python is not required.
Version 4.4 registers this copy to start when the current user signs into Windows;
uncheck **Start with Windows (at sign-in)** in the tray menu to opt out. This is
not a Windows service and does not run before sign-in.

To run from Python source instead, use PowerShell:

```powershell
cd "C:\Apps\teams-caption-notes"
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Use

1. Start the watcher:

```powershell
.\.venv\Scripts\python.exe .\teams_caption_notes.py
```

2. Join a Teams meeting and turn on **More > Language and speech > Show live captions**.

The standalone tray app also offers the optional caption setup helper described
below. Python command-line capture does not change Teams settings automatically.

The watcher starts a new timestamped transcript when Teams' meeting controls appear. After the tracked meeting windows disappear, it waits through an eight-second confirmation period before finalizing and waiting for the next meeting. A minimized or temporarily unreadable tracked meeting window keeps the same transcript open; a stale post-call window can delay finalization until closed. The meeting name is included in the filename, such as `Team_standup-20260908-103000.md`, and in the document heading. It prints a short status every 15 seconds. Press `Ctrl+C` to stop the watcher.

Choose a file or change the timeout:

```powershell
.\.venv\Scripts\python.exe .\teams_caption_notes.py --output .\transcripts\weekly-sync.md --leave-grace 12
```

To save one meeting and exit automatically after you leave:

```powershell
.\.venv\Scripts\python.exe .\teams_caption_notes.py --exit-after-meeting
```

## Standalone system-tray app

**Start with Windows (at sign-in)** applies only to the current Windows user.
Starting with v4.4, the packaged app enables it on first launch and stores the
choice in `tray-settings.json` beside the executable. Turning it off in the tray
removes this app's startup entry and remembers the opt-out across restarts and
in-place updates. Existing v4.3 settings do not contain this new preference, so
the first v4.4 launch also defaults it to on; uncheck the option if unwanted.
Keep the executable and its settings together in a permanent writable folder.
When the saved choice is on, launching an upgraded or relocated copy updates
the startup path to that copy. Launching from Python source does not enable
startup automatically.

Windows Settings > Apps > Startup may separately disable execution, and
organizational policy can block registration or launch. The app reports setup
failures without interrupting capture. It does not request administrator access
or change Windows security policies.

**Copy latest for ChatGPT** copies the complete latest transcript with the summary prompt and opens ChatGPT in your default browser. Review, paste, and send it there. It needs no API key or Entra IDs. For a transcript too large to paste, use **Open transcripts** and attach the file manually in your AI tool. This command does not fetch or automatically save the generated summary.

For caption display tips, screen sharing, and the chat-aggregation investigation, see [NEXT_VERSION.md](NEXT_VERSION.md).

`TeamsCaptionNotes.exe` runs in the Windows notification area and does not require Python on the destination computer. Its icon is blue while waiting, green while capturing, amber while meeting visibility is uncertain, gray when stopped, and red after an error or while retrying a failed scan. Right-click it to start or stop watching, open transcripts, view the diagnostic log, or exit. Double-clicking the icon opens the transcript folder.

If a transcript is open in an application that locks the file, capture continues in memory and retries automatically. If the file remains locked when the meeting ends, the app writes a timestamped `-recovered-` copy beside it, falling back to `%LOCALAPPDATA%\Teams Caption Notes\transcripts` if necessary.

### First-time caption setup

Choose **Set up Teams captions…** from the tray when you want help turning
captions on. For a brand-new packaged installation with no `tray-settings.json`,
this window also opens once on the first manual launch. It does not open at
Windows sign-in. Existing installations/upgrades are not forced through setup;
use the tray command whenever you want it. Source launches do not show this
welcome window automatically.

The tray status **Teams captions: setup recommended** means the setup window has
not yet been opened. After it opens, the status changes to **Teams captions:
setup available in menu**. Neither status diagnoses whether Teams captions are
on or off. Opening the window is remembered, but does not enable captions or
claim they were verified. Keep Teams open, signed in, and visible while using setup. Choose
the action you want and review its confirmation before the helper changes
anything:

- **Future meetings:** enable Teams' Accessibility preference **Always show
  captions in my calls and meetings**, if that exact supported setting is
  available. The helper leaves an already-on setting unchanged and reads back
  its state before reporting success.
- **Current meeting:** after joining a meeting, enable **Show live captions** in
  the meeting's **More actions > Language and speech** menu. An already-enabled
  caption control is left alone. A successful control change is not proof that
  speech has already appeared in the transcript.

This is a user-initiated setup tool, not another process that repeatedly opens
menus on every call. Teams itself handles future meetings when its persistent
preference is on. You can also enable the preference manually in Teams under
**Settings > Accessibility**, or use **More actions > Language and speech > Show
live captions** during a meeting. Microsoft documents the
[in-meeting caption controls](https://support.microsoft.com/en-us/teams/meetings/use-live-captions-in-microsoft-teams-meetings)
and describes [keeping captions on for future meetings](https://www.microsoft.com/en-us/microsoft-teams/accessibility-closed-captions-transcriptions).

The helper uses named Windows accessibility controls belonging to the Teams
process, not screen coordinates, blind keyboard shortcuts, or an undocumented
Microsoft Graph setting. If the intended control is missing, disabled,
unreadable, or ambiguous, it stops and provides manual steps. It does not guess
from a quiet meeting or the absence of new caption text. Client updates and
language differences can prevent recognition; a UI Automation error must not
cause unrelated controls to be selected.

Setup does not change speaker-identification, profanity-filter, or language
preferences and does not start Teams recording or transcription. It cannot
override an organization's policy disabling captions. Run setup at a convenient
time: Teams menus/settings may become visible, including to anyone seeing a
shared screen. The app's quiet tray-notification setting does not hide Teams UI.
Tell participants and follow organizational policy before saving caption text.

The setup commands are best-effort. Automated tests and packaging checks do not
replace validation against a real installed Teams client. A confirmed on/off
state still does not guarantee that Teams will expose every caption while
minimized, off-screen, or disconnected.

### Updating the portable app

1. Right-click the tray icon and choose **Check for updates…**. The check reads
   the latest non-prerelease release from the fixed public repository
   [ChrGio/teams-caption-notes](https://github.com/ChrGio/teams-caption-notes).
   There are no scheduled update checks or unattended installations.
2. Review the offered version and explicitly confirm installation. Finish any
   meeting, close the app's notes and caption setup windows, and let summaries or
   sign-in finish first. Installation is refused while a meeting is capturing or
   its visibility is uncertain, while either of those windows is open, or while
   these background tasks are active.
3. The app downloads the Windows x64 executable to `.updates` beside the running
   executable and verifies its size, Windows x64 format, and GitHub-provided
   SHA-256 digest. A missing digest or failed check prevents installation.
4. After a clean stop of the idle watcher, a separate helper waits for the old
   app to exit, backs up that executable, replaces only that same executable
   path, and starts it again. Transcripts, settings, logs, and other files are
   not replaced. If launching the replacement fails immediately, the helper
   attempts to restore and restart the previous executable.

The check/download sends no transcript content or Microsoft sign-in credentials
to GitHub. GitHub still receives the ordinary network request, including the
requester's IP address. The app accepts assets only from the configured public
repository over approved HTTPS GitHub download hosts; it does not install an
arbitrary download link supplied in release notes.

The update helper does not forcibly terminate applications or elevate to
administrator. If the executable remains locked, permissions prevent replacement,
or the watcher cannot stop cleanly, the update fails instead of forcing a change.
Update diagnostics and the previous executable backup are retained under
`.updates` for troubleshooting. A successful restart request is not a full
health check of the new version.

The executable is unsigned. A checksum detects a mismatched download; it is not
a publisher signature or a security review. SmartScreen, antivirus, or a managed
device policy may require IT approval or allowlisting. The updater does not
bypass these protections. Versions older than v4.4 need a manual download first
to gain this update menu; Python source installations are updated manually.

### Optional Microsoft 365 Copilot summaries

The tray menu can sign in to Microsoft 365 Copilot, summarize the latest transcript on demand, or generate a structured summary automatically when each meeting ends. Summaries contain an executive summary, decisions, action items with supported owners/dates, risks, blockers, and open questions. They are saved beside the transcript as `-summary.md`.

This integration is disabled by default. It requires a tenant-provided Entra public-client application registration, a Microsoft 365 Copilot add-on license for each user, delegated Graph consent, and the current preview `/beta` API. See [COPILOT_SETUP.md](COPILOT_SETUP.md) for exact administrator and user setup.

The standard/basic Microsoft 365 Copilot Chat entitlement does not currently include access to this Graph API. For those users, choose **Copy latest for basic Copilot Chat** from the tray menu. The app copies a structured prompt plus the latest transcript and opens the official Copilot Chat page; the user reviews, pastes, and sends it manually. This is intentionally user-assisted because Microsoft provides no supported background API for the basic entitlement.

Build it from PowerShell:

```powershell
.\build.ps1
```

The executable is written to `dist\TeamsCaptionNotes.exe`. Copy that single file to another Windows computer and double-click it. Transcripts and `teams-caption-notes.log` are created beside the executable, so place it in a folder where the user can write files.

## If no captions are found

Teams' accessibility layout varies by release. While a meeting and live captions are visible, create a diagnostic file:

```powershell
.\.venv\Scripts\python.exe .\teams_caption_notes.py --diagnose .\teams-uia-diagnostic.json
```

The diagnostic contains text exposed by the Teams window, so review it before sharing. If captions are present but not under a caption-labelled accessibility region, retry with the conservative positional fallback:

```powershell
.\.venv\Scripts\python.exe .\teams_caption_notes.py --allow-positional-fallback
```

That fallback can capture unrelated text displayed near the bottom of the Teams window. Inspect the transcript before sending it to an AI system.

## Privacy and operational notes

- Tell participants and follow your organization's policy before transcribing or summarizing a meeting.
- Files remain local unless your sync software, output location, or later AI workflow uploads them.
- UI Automation can only read captions Teams exposes to Windows. Keep the meeting window open and not minimized for the best chance of capturing text. v4.3 and later can preserve the session while minimized, but cannot recover captions that Teams does not expose.
- Speaker labels depend on what the current Teams build exposes. Unlabelled caption text is still retained.
