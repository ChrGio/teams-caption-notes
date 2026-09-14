"""Optional startup at sign-in for the current Windows user."""

from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path


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
