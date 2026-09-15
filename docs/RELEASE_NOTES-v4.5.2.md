# Teams Caption Notes v4.5.2

This maintenance update makes copy-to-AI handoffs clearer and adds clipboard
verification. **Choose New chat and paste with Ctrl+V** to summarize notes;
opening a website does not replace an existing draft or send the transcript.

## Improvements

- A tray status shows the copied filename, even with pop-up notifications hidden.
  Capture events do not erase that status.
- **Copy latest for AI (no browser)** uses the same latest-meeting
  selection without opening an AI website.
- Clipboard writes are checked by reading back the exact Unicode text. The
  clipboard is checked again after the browser launch is requested. Failed or
  unverified handoffs have an explicit tray status instead of silent success.
- Prompts include source filenames, the copy time, and instructions to summarize
  only the supplied source instead of mixing it with earlier meetings.
- Large selected notes prepared as separate prompt files now clearly state that
  the clipboard was not updated.

Meeting selection, caption capture, default-browser settings, startup preferences,
and the updater/install mechanism are unchanged. Clipboard checks are point-in-time;
another app may change the clipboard later, so check the source header before sending.
The app does not automatically paste into, clear, or submit an AI conversation.

## Update

Finish meeting capture, close helper windows, then choose **Check for updates…**
from the tray and confirm the offered update. Keep the app in its existing folder
to preserve its transcripts and settings. The executable remains unsigned.

See the [user guide](USER_GUIDE.md) for handoff and update troubleshooting.
