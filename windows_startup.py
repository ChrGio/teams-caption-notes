"""Per-user startup registration with a persisted, reversible default for EXEs."""

from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path

import tray_settings


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TeamsCaptionNotes"


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        args = [str(Path(sys.executable).resolve())]
    else:
        python = Path(sys.executable).resolve()
        windowed = python.with_name("pythonw.exe")
        args = [str(windowed if windowed.exists() else python),
                str(Path(__file__).resolve().with_name("teams_caption_tray.py"))]
    command = subprocess.list2cmdline(args)
    # The documented Run value limit includes the complete command line.
    if len(command) > 260:
        raise ValueError("Move the app to a shorter folder path before enabling startup.")
    return command


def registered_command() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None


def is_enabled() -> bool:
    """Report whether this copy is registered; Windows can separately disable it."""
    return registered_command() == startup_command()


def set_enabled(enabled: bool) -> None:
    if enabled:
        command = startup_command()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


def set_preference(enabled: bool, preferences_path: Path) -> dict:
    """Persist an explicit choice and apply it to this user's Run registration.

    Save first so a denied registry operation cannot lose an explicit opt-out.
    OSError/ValueError deliberately propagate for the tray to report without
    terminating capture. Unrelated tray preferences are preserved.
    """
    if not isinstance(enabled, bool):
        raise ValueError("startup_enabled must be true or false")
    preferences = tray_settings.load_strict(preferences_path)
    if enabled:
        startup_command()  # Validate the path before persisting the new choice.
    preferences["startup_enabled"] = enabled
    tray_settings.save(preferences_path, preferences)
    set_enabled(enabled)
    return preferences


def apply_default(preferences_path: Path) -> bool:
    """Apply a packaged app's saved startup choice; first runs default to on.

    Developer/source launches never register themselves automatically. Existing
    valid legacy preferences gain the new default; an explicit False is never
    replaced with True. A True choice follows an upgraded or relocated EXE.
    Malformed/unreadable preferences and denied registry writes raise a
    controlled OSError/ValueError instead of silently resetting the choice.
    """
    if not getattr(sys, "frozen", False):
        return False
    preferences = tray_settings.load_strict(preferences_path)
    enabled = preferences.get("startup_enabled", True)
    desired_command = startup_command() if enabled else None
    if "startup_enabled" not in preferences:
        preferences["startup_enabled"] = enabled
        tray_settings.save(preferences_path, preferences)
    current_command = registered_command()
    if current_command != desired_command:
        set_enabled(enabled)
    return enabled
