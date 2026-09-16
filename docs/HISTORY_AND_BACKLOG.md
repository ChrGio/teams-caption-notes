# Version history and feature backlog

[← Back to the project](../README.md) · [User guide](USER_GUIDE.md)

Older planning sections are historical. Current capabilities and installation
instructions are on the main page and in the user guide.

## Version 4.5.3

- Separates newly joined meetings even when the previous meeting window remains open.
- Keeps caption sources associated with the selected meeting and excludes held
  meetings and unrelated caption viewers from its transcript.
- Filters the Teams on-hold caption status out of spoken text.
- Preserves continuity for minimized windows and temporary accessibility outages.

See the [v4.5.3 release notes](RELEASE_NOTES-v4.5.3.md).

## Version 4.5.2

- Makes copy-to-AI handoffs easier to verify: source filename and copy time in
  the prompt, a persistent tray copy status, and clipboard readback checks.
- Adds **Copy latest for AI (no browser)**.
- Clarifies that opening an AI website does not paste text or clear an old draft.
- Explicitly reports when large selected notes are prepared as files instead
  of copied. Caption capture and latest-meeting selection are unchanged.

See the [v4.5.2 release notes](RELEASE_NOTES-v4.5.2.md).

## Version 4.5.1

- Version-label and documentation maintenance release.
- No changes to caption capture, meeting detection, or updater/install logic.
- Adds regression coverage for patch-version ordering.

See the [v4.5.1 release notes](RELEASE_NOTES-v4.5.1.md) for update instructions.

## Version 4.5

- Opt-in caption setup from the app: enable the current meeting's live captions
  or the supported Teams Accessibility preference **Always show captions in my
  calls and meetings**. Opening setup alone does not change settings; an action
  and confirmation are required.
- **Set up Teams captions…** remains available in the tray. Setup opens once on
  the first manual launch of a new packaged installation without existing tray
  settings, never at Windows sign-in. Existing installations/upgrades and Python
  source launches do not get a forced setup window. The setup status
  tracks whether the window was opened, not whether Teams captions are enabled.
- Read the recognized control's state, leave an already-on preference unchanged,
  and verify the resulting state. Use only named UI Automation controls in a
  verified Teams process; no coordinates, blind shortcuts, or private API calls.
- Missing, disabled, ambiguous, or unrecognized controls produce manual guidance
  rather than guessed clicks. Speaker-identification, profanity, language,
  transcription, and recording settings are outside the helper's scope.
- This does not add per-meeting menu automation to the watcher. Teams' own
  persistent setting is responsible for showing captions in future meetings.
- Retains v4.4 startup and checked, user-confirmed updates. See the
  [v4.5 release notes](RELEASE_NOTES-v4.5.md).

Caption setup support depends on the Teams version, language, and tenant policy.
Unrecognized or unavailable controls produce manual setup instructions.

## Version 4.4

- Manual **Check for updates…** from the tray reads the latest public GitHub
  release for `ChrGio/teams-caption-notes`. Installation requires confirmation;
  it is not a scheduled or unattended updater.
- Installation verifies the release's GitHub-provided SHA-256 digest, file size,
  and Windows x64 executable format. It is blocked during a capturing/uncertain
  meeting, while the notes window is open, or while summaries/sign-in are active.
- A separate local helper replaces only the current executable path after a
  clean shutdown, retains a backup, and restarts the app. Transcripts/settings
  remain in place. No forced process termination or administrator elevation is
  used. Update files and diagnostics remain in the executable's `.updates`
  folder. Immediate launch failures trigger a rollback attempt, not a guarantee
  that all later runtime failures can be detected or repaired.
- **Start with Windows (at sign-in)** now defaults on for packaged launches and
  remembers an explicit tray-menu opt-out in `tray-settings.json`. First v4.4
  launches with v4.3 preferences also default on because those preferences lack
  the new startup choice. Python source launches retain manual startup setup.

See [the v4.4 release notes](RELEASE_NOTES-v4.4.md) and
[update instructions](USER_GUIDE.md#updating-the-portable-app). The executable is
unsigned; organizational policy may require IT approval for installation,
updates, or sign-in startup.

## Versions 4.2 and 4.3

- v4.2: saved quiet-notification preference and retry/backoff for returned Windows
  UI Automation errors while preserving the current transcript.
- v4.3: continuity tracking for the same known meeting window when minimized or
  temporarily unreadable, with amber status and clearer visibility logs.
- These changes do not recover captions that Teams stops exposing, detect every
  possible call-state transition, or fix Windows calls that hang indefinitely.

## Earlier planning record

**Version 4 update:** chat/date selection, daily chat exports, notes search, project labels, manual combined summary inputs, lossless long-input splitting, caption journals, and journal recovery are now implemented. See [CHAT_NOTES_SETUP.md](CHAT_NOTES_SETUP.md). The version 3 planning record below is historical. Scheduled chat collection, automatic browser-summary retrieval, and managed-device deployment validation remain outside the implemented features. Version 4.5 adds explicit, user-confirmed caption setup; unattended per-meeting caption changes are not implemented.

Planning record from September 11, 2026. Suggestions are not commitments; consult the release notes for implemented features.

## Included in version 3

- Optional startup when the current user signs into Windows after reboot. In version 3 it was enabled or disabled manually from the tray, and an existing registration for another copy was replaced only when the user enabled this copy. Version 4.4 changes the packaged-app default as described above. Windows policy or Startup settings may prevent execution.
- Copy latest for ChatGPT: copies the full transcript and structured summary instructions, then opens the ChatGPT website. The user pastes and sends it. Existing Copilot workflows remain available.
- Clipboard owner fix for copying from a windowless tray application. A failed copy is reported without stopping the watcher.

Startup uses the current user's Windows Run entry and stores the executable's absolute path. Leave the executable in a permanent writable folder. If Windows blocks startup, check Settings > Apps > Startup or ask IT to approve the app. This feature does not alter Windows security settings. See [Microsoft's Run key documentation](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys).

The ChatGPT workflow uses the normal website and a user-submitted prompt as described in the [official ChatGPT quickstart](https://learn.chatgpt.com/docs/quickstart). It is not an automatic summary API.

## Teams display settings

Teams owns its caption controls. In a meeting, open Caption settings > Caption styles to change font size, height, and placement. The pop-out viewer can also be resized. See [Microsoft's caption customization instructions](https://support.microsoft.com/en-us/teams/meetings/use-live-captions-in-microsoft-teams-meetings) and [pop-out caption guide](https://support.microsoft.com/en-us/accessibility/teams/use-pop-out-captions-in-microsoft-teams-meetings).

If your Teams version exposes **Always show captions in my calls and meetings**
under Accessibility, enable it there, or use the v4.5 setup helper and confirm
the requested change. The helper must verify the actual control and its state;
the label appearing in one client does not prove availability in every
organizational build. The recorder still requires Teams captions to be enabled.

For a less cluttered presentation, share the intended application window or use a separate display for Teams and its caption viewer. Check the sharing preview. The recorder cannot guarantee that visible captions are excluded when sharing the entire screen. Keep the caption source open and not minimized so Windows accessibility can expose its text.

## Teams chat collection investigation

Feasible route: an optional Microsoft Graph v1.0 integration with delegated work/school sign-in. List the signed-in user's chats, let the user select conversations and a date range, then read messages with Chat.Read. Tenant consent and an Entra registration are still required; this is a separate capability from the licensed Copilot summary API. See [List chats](https://learn.microsoft.com/en-us/graph/api/chat-list?view=graph-rest-1.0) and [List chat messages](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0).

Proposed acceptance criteria:

- Collect only selected conversations and dates; channel posts are a separate scope.
- Follow pagination and preserve message IDs, author labels, timestamps, conversation titles, and available source links.
- Update edited messages without creating duplicates and define deletion handling.
- Handle expired authentication, throttling, and interrupted downloads; show partial-data status.
- Produce a daily Markdown note grouped by conversation, with a manual ChatGPT/Copilot handoff.
- Treat chat text as source material rather than executable instructions.

Visible-chat scraping is not a complete-history solution: Teams may only expose messages currently loaded in the UI. No chat collection or daily summaries are implemented in version 3.

## Remaining backlog

- Long meetings: validate real multi-hour Teams capture on representative machines. Journaling, periodic Markdown refresh, synthetic durability benchmarks, and long-input splitting already exist; they do not guarantee continuous live-caption availability.
- Project knowledge: expand on the existing project labels, searchable notes, and explicitly selected combined-summary inputs after gathering user feedback.
- Startup support: test on representative managed Windows machines and document IT-approved installation/signing procedures.
- Caption reliability: further real-meeting validation of built-in and detached caption sources, speaker changes, and final caption revisions.
- Caption setup: real-client testing across managed Teams versions, settings
  layouts, and languages; keep the fallback instructions when strict control
  verification cannot succeed. No silent configuration changes or policy bypass.
