# Teams Caption Notes v4.5.3

This update fixes transcript separation when switching between Teams meetings.

## Fixes

- Joining a new meeting can now start a separate transcript even when the
  previous meeting window remains open or on hold.
- Caption capture stays associated with the selected meeting. Unrelated or
  stale caption viewers are not combined into the new meeting's notes.
- Teams' “Live captions are paused while on hold” status is no longer saved
  as something a participant said.
- Resuming a previously held call starts another transcript segment. Rapid
  switches use distinct filenames instead of overwriting earlier segments.
- Minimized meeting windows and temporary accessibility outages retain the
  existing session instead of unnecessarily splitting it.

When Teams does not expose enough information to distinguish simultaneous
meetings, capture waits for a clear source instead of combining their text.
Caption availability still depends on the Teams accessibility interface.
Existing transcripts are not rewritten or split by this update.

## Update

Finish meeting capture, close helper windows, then choose **Check for updates…**
from the tray and confirm the offered update. Keep the app in its existing
folder to preserve transcripts and settings. The Windows x64 executable remains
unsigned; Python is not required.

See the [user guide](USER_GUIDE.md) for capture and update troubleshooting.
