# Teams Caption Notes v4.5

## What's new

- An **opt-in caption setup window** helps first-time users turn on Teams live
  captions. Opening it alone makes no Teams setting changes.
- New packaged installations show setup once on their first manual launch, not
  at Windows sign-in. Existing users can open **Set up Teams captions…** from the
  tray; upgrades do not force the setup window open.
- After explicit confirmation, try enabling **Always show captions in my calls
  and meetings** in Teams Accessibility, or enable captions in the current
  meeting. Already-on controls remain unchanged and results are checked.
- Uses recognized Windows accessibility controls belonging to Teams. Missing,
  disabled, or ambiguous controls produce manual instructions instead of guessed
  clicks. No coordinates, blind keyboard toggles, or private Microsoft APIs.
- Retains v4.4 checked/manual updates and saved Windows sign-in startup settings,
  v4.3 meeting continuity, and v4.2 quiet tray notifications/scan recovery.
- The updater also requires closing caption setup before replacing the app.

## Using setup

Keep Teams open, signed in, and visible. Choose **Set up Teams captions…** from
the app's tray, select the current-meeting or future-meeting action, and review
its confirmation.
Teams handles future caption display when its persistent preference is enabled;
the watcher does not repeatedly open caption menus during meetings.

The welcome window appears only when no `tray-settings.json` exists on first
manual packaged launch. **Teams captions: setup recommended** means setup has
not been opened yet, not that captions are necessarily off. After opening, the
status becomes **Teams captions: setup available in menu**. Opening setup is
remembered separately from verifying a Teams setting.

Setup may reveal Teams menus or settings. Use it before screen sharing if you
do not want that UI visible to others. Quiet tray notifications do not hide
Teams' own interface.

## Important limits

- Caption controls vary between Teams versions and languages. The helper is
  best-effort; automated tests and package checks are not live-client validation.
- The helper does not start recording/transcription or change language,
  speaker-identification, or profanity settings. Tenant restrictions are not
  bypassed. Follow your organization's rules before retaining caption text.
- Captions must still be available through Windows accessibility. An enabled
  setting does not recover speech that Teams did not expose.
- The Windows executable remains unsigned. IT approval may be required. Updates
  and startup remain subject to Windows permissions and organizational policy.

Keep the executable, settings, and `transcripts` folder together in a writable
location when upgrading. Python is not required for the standalone executable.
