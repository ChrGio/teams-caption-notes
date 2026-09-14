# Teams Caption Notes v4.5.1

This small maintenance release updates the displayed version and documentation.
It provides a newer version for testing the existing **Check for updates…**
workflow from v4.5. Caption capture, meeting detection, and updater/install
behavior are unchanged.

## Test the update path

1. Finish any meeting capture and close the notes and caption setup windows.
2. In the running v4.5 tray app, choose **Check for updates…**.
3. Review the offered v4.5.1 update and confirm only when ready to restart.
4. After restart, check that the tray and notes-window title show **v4.5.1** and
   that your existing transcript folder and preferences remain available.

The existing updater checks the GitHub-provided checksum before installation
and retains a backup. Windows permissions and organizational policy still apply;
the executable remains unsigned.

Focused automated tests cover `4.5 < 4.5.1 < 4.6` and avoid re-offering the same
patch version. Those tests are not a claim that a live in-place update has been
verified on your machine. See [update instructions](USER_GUIDE.md#updating-the-portable-app)
for the workflow and its limits.
