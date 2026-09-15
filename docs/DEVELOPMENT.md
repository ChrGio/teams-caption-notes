# Run, test, and build from source

[← Back to the project](../README.md)

Most users only need the [Windows release executable](https://github.com/ChrGio/teams-caption-notes/releases/latest).
The instructions below are for developers on Windows. Run commands in PowerShell
from the repository root. The v4.5 executable was built with Python 3.14 x64.

## Set up Python dependencies

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The runtime dependency versions are pinned in [requirements.txt](https://github.com/ChrGio/teams-caption-notes/blob/main/requirements.txt).
Windows accessibility and tray integration are Windows-specific.

## Run the app

Tray interface:

```powershell
.\.venv\Scripts\python.exe .\src\teams_caption_tray.py
```

Console caption watcher:

```powershell
.\.venv\Scripts\python.exe .\src\teams_caption_notes.py
```

Enable Teams captions before capturing. Use `Ctrl+C` to stop the console watcher.
To see its options without starting capture:

```powershell
.\.venv\Scripts\python.exe .\src\teams_caption_notes.py --help
```

In source mode, default transcripts, logs, and configuration files are kept
beside the scripts in `src/`. The packaged EXE instead keeps them beside itself.
Source mode does not enable Windows startup or show the first-run welcome dialog
automatically. Use a separate working copy for development; do not move an
existing installation's data into this repository.

The console watcher can use an explicit output path:

```powershell
.\.venv\Scripts\python.exe .\src\teams_caption_notes.py --output .\transcripts\weekly-sync.md
```

## Run the tests

```powershell
.\test.ps1
```

The wrapper adds `src/` to the import path, discovers tests under `tests/`, and
restores the caller's location and `PYTHONPATH` afterward. To use an existing
environment instead of this checkout's `.venv`:

```powershell
.\test.ps1 -PythonPath 'C:\Dev\venvs\caption-notes\Scripts\python.exe'
```

Tests mock Windows controls and external services where appropriate. Passing
them is not proof of live Teams compatibility, tenant consent, or successful
Windows startup on a managed computer.

The optional synthetic caption-journal benchmark uses temporary data:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_caption_journal.py
```

## Build the standalone Windows EXE

```powershell
.\build.ps1
```

The script installs [build dependencies](https://github.com/ChrGio/teams-caption-notes/blob/main/requirements-build.txt) into `.venv`
and runs PyInstaller against `src/teams_caption_tray.py`. Its default output is
`dist\TeamsCaptionNotes.exe`, with documentation and an example configuration
alongside it. Optional build flags:

```powershell
.\build.ps1 -DistDirectory dist-local -SkipDependencyInstall
```

Use `-SkipDependencyInstall` only when all pinned build dependencies are already
installed. Generated EXEs, build folders, `.spec` files, logs, transcripts,
settings, and token caches are intentionally ignored by Git. An EXE built this
way is unsigned; respect Windows and your organization's security policies.

## Repository layout

- `src/` — application modules; flat sibling imports are intentional.
- `tests/` — automated regression tests.
- `docs/` — user/developer guides, setup details, and release history.
- `examples/` — placeholder configuration, never real credentials.
- `scripts/` — developer utilities and benchmarks.
- `build.ps1` / `test.ps1` — build and test entry points.

Do not commit real meeting transcripts, chat exports, logs, UI Automation dumps,
account identifiers, or sign-in caches. Before publishing a build, run the tests,
review the exact files being packaged, and verify the artifact separately.

## Public release writing

Write release notes for anyone downloading the app: describe product changes,
update instructions, compatibility requirements, and relevant limitations.
Include artifact checksums when available. Keep individual troubleshooting
conversations, references to a particular user's computer, and internal
handoff or testing-session notes out of public release descriptions and guides.
Keep detailed verification procedures in developer documentation.
