# Version 4.5.5 — Screen-sharing window detection

[← Back to the project](../README.md)

- Prevents Teams' sharing control bar from being selected as another meeting
  or read as a source of captions.
- Recognizes the initial **Meeting join** title as provisional. The same native
  meeting window can receive its real name without creating another transcript.
- Updates the transcript heading when the real name becomes available. A file
  started with the generic **Meeting** name keeps that filename during capture.
- Includes compact-view continuity, ambiguous-window safeguards, setup-text
  filtering, and silent caption-health tray statuses introduced in v4.5.4.
- Adds window-role diagnostics without including meeting names in those fields.

## Requirements and limitations

Windows 10/11 x64. No Python is required for the standalone executable. It is
unsigned; follow your organization's security and meeting-notes policies.

Keep live captions enabled and accessible. If captions disappear during screen
sharing, restore the full meeting window and caption panel. The app does not
record audio or recover speech that Teams did not expose. A restored panel may
provide recent caption history, but this is not guaranteed to cover a capture gap.
Ambiguous simultaneous calls may require restoring the original meeting window.

Exit the old app before launching the replacement. Preserve existing transcripts
and settings, and do not run two copies against the same transcript folder.
Existing transcripts are not changed or automatically merged.
