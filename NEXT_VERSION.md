# Version 3 and feature backlog

**Version 4 update:** chat/date selection, daily chat exports, notes search, project labels, manual combined summary inputs, lossless long-input splitting, caption journals, and journal recovery are now implemented. See [CHAT_NOTES_SETUP.md](CHAT_NOTES_SETUP.md). The version 3 planning record below is historical. Scheduled chat collection, automatic browser-summary retrieval, managed-device deployment validation, and automatic Teams caption settings remain outside the implemented features.

Updated September 11, 2026 from the user's meeting-derived feature list. The underlying meeting transcript was not provided for this planning update; suggestions are not treated as confirmed commitments.

## Included in version 3

- Optional startup when the current user signs into Windows after reboot. Enable or disable it from the tray. An existing app registration for another copy is replaced only when the user enables this copy. Windows policy or Startup settings may prevent execution.
- Copy latest for ChatGPT: copies the full transcript and structured summary instructions, then opens the ChatGPT website. The user pastes and sends it. Existing Copilot workflows remain available.
- Clipboard owner fix for copying from a windowless tray application. A failed copy is reported without stopping the watcher.

Startup uses the current user's Windows Run entry and stores the executable's absolute path. Leave the executable in a permanent writable folder. If Windows blocks startup, check Settings > Apps > Startup or ask IT to approve the app. This feature does not alter Windows security settings. See [Microsoft's Run key documentation](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys).

The ChatGPT workflow uses the normal website and a user-submitted prompt as described in the [official ChatGPT quickstart](https://learn.chatgpt.com/docs/quickstart). It is not an automatic summary API.

## Teams display settings

Teams owns its caption controls. In a meeting, open Caption settings > Caption styles to change font size, height, and placement. The pop-out viewer can also be resized. See [Microsoft's caption customization instructions](https://support.microsoft.com/en-us/teams/meetings/use-live-captions-in-microsoft-teams-meetings) and [pop-out caption guide](https://support.microsoft.com/en-us/accessibility/teams/use-pop-out-captions-in-microsoft-teams-meetings).

If your Teams version exposes an always-show-captions setting under Accessibility, enable it there. Availability needs checking in the user's work/school Teams version; the setting is documented for Teams Free, which is not proof of availability in every organizational build. The recorder still requires Teams captions to be enabled.

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

- Long meetings: benchmark multi-hour capture, reduce full-file rewrite overhead, add a recoverable journal, and split large AI inputs with traceable per-part summaries. Current capture and API size limits are unchanged.
- Project knowledge: optional project labels and a searchable index across meeting notes, then explicitly selected cross-meeting summaries.
- Startup support: test on representative managed Windows machines and document IT-approved installation/signing procedures.
- Caption reliability: further real-meeting validation of built-in and detached caption sources, speaker changes, and final caption revisions.

## Validation

31 automated tests passed, including startup path quoting, current-user registry targeting, disabled/missing entries, ChatGPT handoff, and clipboard failure handling. Registry writes and browser launches were mocked in those tests. A real reboot, a managed coworker machine, and a live meeting were not exercised in this build.
