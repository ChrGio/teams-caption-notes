# Version 4.5.4 — Caption continuity and health

[← Back to the project](../README.md)

- Preserves an existing transcript through recognized Teams compact-view
  transitions, including when the compact window has a different window ID.
- Pauses ambiguous same-name meeting windows instead of immediately splitting
  the transcript or mixing caption sources. Different named meetings remain separate.
- Shows amber **Captions unavailable** or waiting status when speech is not being
  captured. Green indicates recently received caption text, not just a detected call.
- Reports a quiet-or-unavailable status after 60 seconds without new captions.
  Caption-health changes do not generate pop-ups.
- Removes known empty-caption setup text from transcripts and adds window/source
  diagnostics to help troubleshoot accessibility transitions.

## Requirements and limitations

Windows 10/11 x64. The standalone executable does not require Python and is unsigned.
Follow your organization's security and meeting-notes policies.

Keep Teams captions enabled and accessible. Restore the meeting window and its
caption panel if captions disappear during screen sharing. The app does not
record audio, automatically reopen Teams windows, or recover speech that Teams
did not expose. Ambiguous simultaneous calls may require restoring the original
meeting window before capture can resume. Existing transcripts are not modified.

Keep the existing installation's transcripts and settings when replacing its
executable. Exit the old app before launching the replacement; avoid running two
copies against the same transcript folder.
