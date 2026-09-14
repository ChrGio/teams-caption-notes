"""Optional Microsoft 365 Copilot Chat summarization through Microsoft Graph."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import teams_caption_notes as capture


GRAPH_ROOT = "https://graph.microsoft.com/beta"
SCOPES = [
    "Sites.Read.All",
    "Mail.Read",
    "People.Read.All",
    "OnlineMeetingTranscript.Read.All",
    "Chat.Read",
    "ChannelMessage.Read.All",
    "ExternalItem.Read.All",
]
DEFAULT_PROMPT = """Summarize the supplied Microsoft Teams meeting transcript.

Return these sections in Markdown:
- Executive Summary
- Decisions Made
- Action Items (include owner and due date when explicitly stated)
- Risks / Blockers
- Open Questions

Use only the supplied transcript. Do not invent missing facts, owners, decisions, or dates. Clearly write \"None identified\" when a section has no supported information."""


class CopilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class CopilotConfig:
    enabled: bool
    auto_summarize: bool
    tenant_id: str
    client_id: str
    time_zone: str
    prompt: str
    max_transcript_chars: int

    @property
    def configured(self) -> bool:
        placeholders = {"", "YOUR_TENANT_ID", "YOUR_CLIENT_ID"}
        return self.tenant_id not in placeholders and self.client_id not in placeholders


def default_config() -> dict[str, Any]:
    return {
        "_instructions": "Enter the Entra tenant and public-client application IDs, then set enabled to true.",
        "enabled": False,
        "auto_summarize": True,
        "tenant_id": "YOUR_TENANT_ID",
        "client_id": "YOUR_CLIENT_ID",
        "time_zone": "America/New_York",
        "max_transcript_chars": 200000,
        "prompt": DEFAULT_PROMPT,
    }


def ensure_config(path: Path) -> Path:
    if not path.exists():
        capture.atomic_write(path, json.dumps(default_config(), indent=2, ensure_ascii=False) + "\n")
    return path


def load_config(path: Path) -> CopilotConfig:
    if not path.exists():
        data = default_config()
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CopilotError(f"Could not read {path.name}: {exc}") from exc
    try:
        maximum = int(data.get("max_transcript_chars", 200000))
    except (TypeError, ValueError) as exc:
        raise CopilotError("max_transcript_chars must be a number") from exc
    return CopilotConfig(
        enabled=bool(data.get("enabled", False)),
        auto_summarize=bool(data.get("auto_summarize", True)),
        tenant_id=str(data.get("tenant_id", "")).strip(),
        client_id=str(data.get("client_id", "")).strip(),
        time_zone=str(data.get("time_zone", "America/New_York")).strip() or "America/New_York",
        prompt=str(data.get("prompt", DEFAULT_PROMPT)).strip() or DEFAULT_PROMPT,
        max_transcript_chars=max(1000, maximum),
    )


def token_cache_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(capture.application_dir()))) / "Teams Caption Notes"
    root.mkdir(parents=True, exist_ok=True)
    return root / "copilot-token-cache.bin"


def get_access_token(config: CopilotConfig, interactive: bool = True, *, scopes=None, cache_file: Path | None = None, select_account: bool = False) -> str:
    if not config.configured:
        raise CopilotError("Copilot is not configured. Enter tenant_id and client_id in copilot-config.json.")
    try:
        import msal
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence

        persistence = build_encrypted_persistence(str(cache_file or token_cache_path()))
        cache = PersistedTokenCache(persistence)
        app = msal.PublicClientApplication(
            config.client_id,
            authority=f"https://login.microsoftonline.com/{config.tenant_id}",
            token_cache=cache,
        )
    except Exception as exc:
        raise CopilotError(f"Could not initialize encrypted Microsoft sign-in: {exc}") from exc

    requested_scopes = SCOPES if scopes is None else scopes
    result = None
    for account in ([] if select_account else app.get_accounts()):
        result = app.acquire_token_silent(requested_scopes, account=account)
        if result and "access_token" in result:
            break
    if (not result or "access_token" not in result) and interactive:
        options = {"prompt": "select_account"} if select_account else {}
        result = app.acquire_token_interactive(scopes=requested_scopes, timeout=300, **options)
    if result and "access_token" in result:
        return str(result["access_token"])
    detail = (result or {}).get("error_description") or (result or {}).get("error") or "Sign-in is required."
    raise CopilotError(f"Microsoft sign-in failed: {detail}")


def _response_json(response: requests.Response, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CopilotError(f"Copilot {operation} returned HTTP {response.status_code} without JSON.") from exc
    if not response.ok:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        detail = error.get("message") if isinstance(error, dict) else None
        raise CopilotError(f"Copilot {operation} failed (HTTP {response.status_code}): {detail or payload}")
    if not isinstance(payload, dict):
        raise CopilotError(f"Copilot {operation} returned an unexpected response.")
    return payload


def ask_copilot(config: CopilotConfig, transcript_text: str) -> tuple[str, list[dict[str, Any]]]:
    if len(transcript_text) > config.max_transcript_chars:
        raise CopilotError(
            f"Transcript has {len(transcript_text):,} characters, above the configured "
            f"{config.max_transcript_chars:,}-character safety limit."
        )
    token = get_access_token(config)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    created = _response_json(
        requests.post(f"{GRAPH_ROOT}/copilot/conversations", headers=headers, json={}, timeout=30),
        "conversation creation",
    )
    conversation_id = created.get("id")
    if not conversation_id:
        raise CopilotError("Copilot conversation response did not include an id.")
    body = {
        "message": {"text": config.prompt},
        "additionalContext": [{"description": "Teams live-caption transcript", "text": transcript_text}],
        "locationHint": {"timeZone": config.time_zone},
        "contextualResources": {"webContext": {"isWebEnabled": False}},
    }
    chatted = _response_json(
        requests.post(
            f"{GRAPH_ROOT}/copilot/conversations/{conversation_id}/chat",
            headers=headers,
            json=body,
            timeout=180,
        ),
        "chat request",
    )
    messages = chatted.get("messages") or []
    for message in reversed(messages):
        text = str(message.get("text", "")).strip() if isinstance(message, dict) else ""
        if text and text != config.prompt:
            attributions = message.get("attributions") or []
            return text, attributions if isinstance(attributions, list) else []
    raise CopilotError("Copilot returned no summary text.")


def render_summary(meeting_title: str, transcript_path: Path, summary: str, attributions: list[dict[str, Any]]) -> str:
    lines = [
        f"# Copilot summary — {meeting_title}",
        "",
        f"- Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- Source transcript: `{transcript_path.name}`",
        "- Review required: AI-generated content can be inaccurate.",
        "",
        summary.strip(),
        "",
    ]
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in attributions:
        if not isinstance(item, dict):
            continue
        url = str(item.get("seeMoreWebUrl", "")).strip()
        if url and url not in seen:
            seen.add(url)
            sources.append((str(item.get("providerDisplayName", "Source")).strip() or "Source", url))
    if sources:
        lines.extend(["## Copilot attributions", ""])
        lines.extend(f"- [{label}]({url})" for label, url in sources)
        lines.append("")
    return "\n".join(lines)


def summarize_transcript(transcript_path: Path, meeting_title: str, config_path: Path) -> Path:
    config = load_config(config_path)
    if not config.configured:
        raise CopilotError("Copilot is not configured. Open Configure Copilot from the tray menu first.")
    try:
        transcript_text = transcript_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CopilotError(f"Could not read transcript {transcript_path}: {exc}") from exc
    summary, attributions = ask_copilot(config, transcript_text)
    summary_path = transcript_path.with_name(f"{transcript_path.stem}-summary.md")
    capture.atomic_write(summary_path, render_summary(meeting_title, transcript_path, summary, attributions))
    return summary_path
