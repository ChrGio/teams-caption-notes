"""Local tray preferences, kept beside the executable across restarts."""
import json
import logging
from pathlib import Path

import teams_caption_notes as capture


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("hide_popups", True), bool):
            raise ValueError("hide_popups must be true or false")
        return {**data, "hide_popups": data.get("hide_popups", True)}
    except FileNotFoundError:
        return {"hide_popups": True}
    except (OSError, ValueError):
        logging.exception("Could not read tray preferences; keeping pop-ups hidden")
        return {"hide_popups": True}


def save(path: Path, preferences: dict) -> None:
    capture.atomic_write(path, json.dumps(preferences, indent=2) + "\n")
