# Teams chats and notes

[← Back to the project](../README.md) · [User guide](USER_GUIDE.md)

Open the tray menu and select **Chats and notes…**. Meeting capture continues while this window is open.

## Chat connection

An Entra administrator can use a dedicated single-tenant public-client app registration, or add the permission to an existing approved registration. Configure Mobile and desktop applications with redirect URI `http://localhost`, and enable public-client flows. Add **Microsoft Graph > Delegated permissions > Chat.Read** and grant consent as required by your tenant. No client secret is needed. This app uses the global Microsoft Graph endpoint.

Chat collection uses Graph v1.0 chat endpoints, separately from the Copilot Chat API. It requests only `Chat.Read`, not the seven Copilot permissions. Your basic Copilot entitlement is not used to authorize chat downloads. Microsoft documents this delegated permission for [listing chats](https://learn.microsoft.com/en-us/graph/api/chat-list?view=graph-rest-1.0) and [reading chat messages](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0).

In the **Teams chats** tab:

1. Enter Directory (tenant) ID and Application (client) ID. Choose **Save setup**.
2. Choose **Sign in and load chats** and select your work account in Microsoft's sign-in window.
3. Select conversations using Ctrl or Shift. Choose the start and end dates, inclusive, in your PC's local time zone.
4. Choose **Collect selected chats**. The app retrieves only the selected conversations for those dates.

Setup is saved beside the EXE as `chat-config.json`. Sign-in tokens are encrypted with Windows DPAPI under `%LOCALAPPDATA%\Teams Caption Notes\teams-chat-token-cache.bin`, separate from the Copilot cache.

## Daily notes and summary workflow

Each request creates a fresh folder under `chat-notes\collection-TIMESTAMP-ID`. It contains `COLLECTION.md`, daily `Chats-YYYY-MM-DD.md` notes, and `snapshot.json` with the downloaded records. Daily notes are grouped by conversation. They retain authors, timestamps, message IDs, modification dates, and source links when Microsoft supplies them. HTML message bodies are converted to text. Attachments are listed but not downloaded. Channels are not included.

If the connection fails or you cancel, downloaded messages are saved with **PARTIAL** status. This is not a complete daily record. Retry by running a fresh collection. Within a collection, message IDs are deduplicated and the latest returned edit wins. Returned deletions appear as deletion markers. A new collection reflects the API's current responses; old snapshots are retained and are not continuously synchronized with later edits or deletions.

In **Saved notes**, refresh after collecting. Select the daily notes and choose **ChatGPT** or **Copilot Chat**. The app copies a summary prompt and opens that service. Review, paste, and send it. Browser summaries are not automatically saved back to the app. **Copilot API summary** generates a saved summary for one selected note, but still requires the separate licensed [Copilot setup](COPILOT_SETUP.md).

The prompt asks for supported decisions, actions, owners, dates, risks, and questions. It distinguishes named mentions from attendance and tells the AI to treat the notes as data. Partial export warnings remain part of the summary input.

## Notes across projects

Saved notes searches meeting transcripts and chat notes by filename, contents, and project label. **Add folder** includes transcripts from older versions or another folder. **Set project** labels the selected notes; the label is stored in `notes-library.json` without changing the original notes. Select several notes to prepare a combined summary input.

For long inputs, **Prepare long notes** creates numbered prompt files under `summary-inputs`. Inputs over 20,000 characters in the notes window use this preparation automatically. Source text is split without dropping characters. Summarize each part, then use `combine-summaries.txt` with all returned part summaries. These files are prompts, not generated summaries; they do not increase a provider's actual context limit.

## Long meeting durability

Meeting updates are appended and flushed to a `.captions.jsonl` journal beside the transcript. The complete Markdown file is refreshed about every ten seconds when it changes, and immediately when the meeting ends or the watcher stops. The journal retains revisions to the latest caption without rewriting the entire meeting on each update.

After a crash, use **Saved notes > Recover meeting journal** and choose the journal. Recovery creates a separate Markdown file using complete records, preserving the original journal. An unfinished final record is ignored. If disk writes are blocked, the watcher attempts Markdown saving and its existing recovery locations; no file can guarantee preservation when Windows rejects all writes.

Journals and snapshots contain meeting/chat text and are retained alongside your notes. A journal is not a Teams audio recording. No scheduled chat collection is enabled by this version.

## Remaining practical checks

The original version 4 validation passed 49 automated tests, including API pagination/retry/cancellation, partial exports, edit deduplication, source splitting, and capture-to-journal finalization. This is a historical test count, not the current suite total. A synthetic four-hour meeting (2,880 caption updates) recovered every caption from a 955,653-byte journal; writes took about 4.8 seconds total on the development machine. This tests local durability and output scaling, not four hours of live Teams accessibility behavior. The notes window passed a construction check; the tenant API has not been exercised with real credentials.

The application does not own Teams' caption appearance settings. Use Teams Caption settings > Caption styles for smaller captions, and the available Accessibility setting to keep captions enabled. Keep the caption source open. For screen sharing, select the intended application window or a separate display and inspect the sharing preview.

Since version 4.4, the packaged app defaults to starting at Windows sign-in and remembers an explicit tray-menu opt-out. Launching a relocated copy updates the startup path when that preference is on. Source launches do not enable startup automatically. See the [user guide](USER_GUIDE.md#standalone-system-tray-app). Managed-machine startup, real multi-hour meetings, and tenant-specific Graph consent require validation on your organization's machines; the app does not bypass those controls.
