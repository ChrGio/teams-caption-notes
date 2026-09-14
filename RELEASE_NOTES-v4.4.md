# Teams Caption Notes v4.4

## What's new

- **Check for updates…** in the tray: manually check this project's latest public
  GitHub release and explicitly confirm installation of a newer Windows x64 app.
- Checks the download against GitHub's SHA-256 digest and expected size/format
  before installation. Missing or mismatched verification prevents installation.
- Keeps transcripts and settings in place. A helper waits for a clean shutdown,
  backs up and replaces only the current executable, then restarts it.
- **Start with Windows (at sign-in)** now defaults on for packaged launches.
  Uncheck it in the tray to save an opt-out across restarts and in-place updates.
- Retains v4.2 quiet notifications/scan recovery and v4.3 meeting continuity.

## Installing or upgrading

Place the executable in a permanent writable folder. Python is not required.
Versions older than v4.4 must first download v4.4 manually to gain the update menu.
Keep the existing `transcripts` folder and settings beside the executable. Existing
v4.3 settings lack the new startup preference, so the first v4.4 launch defaults
startup to on; turn it off in the tray if you do not want sign-in startup.

Updates require finishing the active meeting, closing the app's notes window,
and allowing summaries/sign-in to finish. An uncertain meeting is treated as
active for installation safety. The app does not force-kill processes, request
administrator elevation, or install updates unattended. Update files, a backup,
and helper diagnostics are retained under the executable's `.updates` folder.

## Important limits

- No transcript content or Microsoft sign-in credentials are sent to GitHub for
  update checks/downloads. These are ordinary public HTTPS requests.
- The executable is unsigned; IT approval may be needed on managed devices. A
  checksum is not a publisher signature, and Windows protections are not bypassed.
- If replacement cannot be launched immediately, the helper attempts rollback.
  A restart is not a full health check, and later application failures may not be
  detected automatically.
- Windows startup is per-user at sign-in, not before sign-in. Python source
  launches do not automatically register startup. Windows/IT policy may still
  disable or block launch.
- Caption capture still depends on what Teams exposes through Windows
  accessibility. Hidden or unavailable captions may be missed.
