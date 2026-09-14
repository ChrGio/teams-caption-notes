"""Local tray preferences, kept beside the executable across restarts."""
import json
import logging
from pathlib import Path

import teams_caption_notes as capture


def load_strict(path: Path) -> dict:
    """Read saved choices without mistaking a damaged/locked file for a first run.

    Callers that change Windows startup must use this reader: a permissive
    fallback could discard a user's saved opt-out and enable startup again.
    Missing files are first runs; all other read/validation errors propagate.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"hide_popups": True}
    if not isinstance(data, dict):
        raise ValueError("Tray preferences must be a JSON object")
    for name in ("hide_popups", "startup_enabled", "caption_setup_seen"):
        if name in data and not isinstance(data[name], bool):
            raise ValueError(f"{name} must be true or false")
    return {**data, "hide_popups": data.get("hide_popups", True)}


def load(path: Path) -> dict:
    try:
        return load_strict(path)
    except (OSError, ValueError):
        logging.exception("Could not read tray preferences; keeping pop-ups hidden")
        return {"hide_popups": True}


def save(path: Path, preferences: dict) -> None:
    # Do not let a permissive load fallback overwrite a saved startup opt-out.
    # Merging also keeps keys added since a tray window loaded its preferences.
    combined = {**load_strict(path), **preferences}
    for name in ("hide_popups", "startup_enabled", "caption_setup_seen"):
        if name in combined and not isinstance(combined[name], bool):
            raise ValueError(f"{name} must be true or false")
    capture.atomic_write(path, json.dumps(combined, indent=2) + "\n")
