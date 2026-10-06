"""Honest, transition-only status for a selected meeting's caption feed.

This does not infer that a quiet meeting is broken, or that readable old text
means new speech is arriving. Callers supply accessibility evidence separately
from whether an actual usable caption changed the transcript.
"""

from __future__ import annotations


class CaptionHealth:
    def __init__(self, stale_after: float = 60.0) -> None:
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        self.stale_after = stale_after
        self.reset()

    def reset(self) -> None:
        self.started_at: float | None = None
        self.last_speech_at: float | None = None
        self._status_key: str | None = None

    def begin(self, now: float) -> tuple[str, str]:
        self.reset()
        self.started_at = now
        self._status_key = "waiting"
        return "captions_waiting", "Meeting detected — waiting for new captions"

    def invalidate_status(self) -> None:
        """Re-emit the next observation after a higher-priority status clears.

        For example, a scan-error status can temporarily hide caption health.
        Keep speech timestamps, but do not infer reception from the recovery.
        """
        self._status_key = None

    def observe(
        self, now: float, *, available: bool, speech: bool, reason: str = ""
    ) -> tuple[str, str] | None:
        """Return a status transition, never a notification for every poll.

        ``available`` means the selected meeting has readable caption UI.
        ``speech`` means a usable caption changed the transcript this scan;
        visible old lines, setup labels and silence must pass ``False``.
        Missing/ambiguous UI takes precedence over inconsistent speech input.
        """
        if self.started_at is None:
            self.started_at = now
        if not available:
            detail = " ".join(reason.split())
            message = "Captions unavailable"
            message += f" — {detail}" if detail else " — restore Teams and reopen its captions panel"
            key, event = f"unavailable:{detail}", "captions_unavailable"
        elif speech:
            self.last_speech_at = now
            key, event, message = "receiving", "captions_receiving", "Receiving new meeting captions"
        else:
            reference = self.last_speech_at if self.last_speech_at is not None else self.started_at
            if now - reference >= self.stale_after:
                key, event = "stale", "captions_waiting"
                message = (
                    f"No new captions for {self.stale_after:g}+ seconds — "
                    "meeting may be quiet or captions unavailable"
                )
            elif self._status_key == "receiving":
                # Recently received speech remains healthy for a short pause.
                return None
            else:
                key, event, message = "waiting", "captions_waiting", "Caption panel available — waiting for new captions"
        if key == self._status_key:
            return None
        self._status_key = key
        return event, message
