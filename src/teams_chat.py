"""Opt-in Graph chat export. No background collection or cloud summarization."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import requests
import copilot_summary
import teams_caption_notes as capture

GRAPH = "https://graph.microsoft.com/v1.0"


class ChatError(RuntimeError):
    pass


class Cancelled(ChatError):
    pass


def save_settings(path: Path, tenant: str, client: str) -> None:
    # Only organization-specific authorities are accepted in this desktop UI.
    data = {"tenant_id": str(uuid.UUID(tenant.strip())), "client_id": str(uuid.UUID(client.strip()))}
    capture.atomic_write(path, json.dumps(data, indent=2) + "\n")


def token_provider(path: Path):
    config = copilot_summary.load_config(path)
    if not config.configured:
        raise ChatError("Enter the tenant and client IDs in Chat setup first.")
    uuid.UUID(config.tenant_id)
    uuid.UUID(config.client_id)
    cache = copilot_summary.token_cache_path().with_name("teams-chat-token-cache.bin")
    return lambda interactive: copilot_summary.get_access_token(
        config, interactive=interactive, scopes=["Chat.Read"], cache_file=cache, select_account=interactive,
    )


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ChatError("A chat message had a timestamp without a time zone.")
    return result


class GraphClient:
    def __init__(self, get_token, cancel=None, session=None):
        self.get_token = get_token
        self.cancel = cancel or threading.Event()
        self.session = session or requests.Session()
        self.token = get_token(True)

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled("Collection cancelled; any downloaded data is marked partial.")

    def get(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
            raise ChatError("Graph returned an unexpected pagination URL.")
        refreshed = False
        for attempt in range(5):
            self.check_cancel()
            try:
                response = self.session.get(url, headers={"Authorization": f"Bearer {self.token}"}, timeout=(10, 30), allow_redirects=False)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 4:
                    raise ChatError("The chat download was interrupted by a network error.") from exc
                self.cancel.wait(2 ** attempt)
                continue
            if response.status_code == 401 and not refreshed:
                self.token = self.get_token(False)
                refreshed = True
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 4:
                retry = response.headers.get("Retry-After", str(2 ** attempt))
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError):
                        delay = 2 ** attempt
                if delay > 120:
                    raise ChatError("Microsoft requested a longer retry delay. Try collecting again later.")
                self.cancel.wait(max(0, delay))
                continue
            if response.status_code != 200:
                raise ChatError(f"Graph returned HTTP {response.status_code}. Check sign-in and Chat.Read consent, then retry.")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ChatError("Graph returned an invalid response.") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                raise ChatError("Graph returned an unexpected collection.")
            return payload
        raise ChatError("Microsoft Graph did not complete the request after retries.")

    def pages(self, url):
        visited = set()
        while url:
            if url in visited or len(visited) >= 10000:
                raise ChatError("Pagination did not finish; the export is incomplete.")
            visited.add(url)
            payload = self.get(url)
            yield payload["value"]
            url = payload.get("@odata.nextLink")
            if url is not None and not isinstance(url, str):
                raise ChatError("Invalid pagination link.")

    def chats(self):
        chats = {}
        for page in self.pages(f"{GRAPH}/me/chats?$top=50&$expand=members"):
            for chat in page:
                if not isinstance(chat, dict) or not chat.get("id"):
                    raise ChatError("A conversation was missing its ID.")
                chats[chat["id"]] = chat
        return sorted(chats.values(), key=lambda c: chat_title(c).casefold())

    def messages(self, chat_id, start: date, end: date):
        # Dates use the PC's local zone; conversion per date handles DST changes.
        end_exclusive = datetime.combine(end + timedelta(days=1), datetime.min.time()).astimezone().astimezone(timezone.utc)
        params = urlencode({"$top": 50, "$orderby": "createdDateTime desc", "$filter": f"createdDateTime lt {end_exclusive.isoformat()}"})
        url = f"{GRAPH}/chats/{quote(chat_id, safe='')}/messages?{params}"
        for page in self.pages(url):
            older = False
            for message in page:
                self.check_cancel()
                if not isinstance(message, dict) or not message.get("id") or not message.get("createdDateTime"):
                    raise ChatError("A message was missing its ID or date; export is incomplete.")
                day = parse_time(message["createdDateTime"]).astimezone().date()
                if day < start:
                    older = True
                elif day <= end:
                    yield message
            if older:
                return


def chat_title(chat):
    names = [m.get("displayName", "") for m in chat.get("members", []) if isinstance(m, dict)]
    return chat.get("topic") or ", ".join(n for n in names if n) or f"{chat.get('chatType', 'Chat')} {chat['id'][-12:]}"


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def message_text(message):
    if message.get("deletedDateTime"):
        return "[Message deleted in Teams]"
    body = message.get("body") or {}
    content = str(body.get("content") or "")
    if str(body.get("contentType", "")).lower() == "html":
        parser = PlainHTML()
        parser.feed(content)
        content = "".join(parser.parts)
    attachments = message.get("attachments") or []
    if attachments:
        content += "\n[Attachments listed only: " + ", ".join(str(a.get("name") or "unnamed") for a in attachments) + "]"
    return content.strip() or "[No text content]"


def escape_md(value):
    return re.sub(r"([\\`*_{}\[\]<>#])", r"\\\1", str(value).replace("\n", " "))


def export_chats(client, chats, start, end, folder: Path, progress=lambda text: None):
    if start > end:
        raise ChatError("The start date must not be after the end date.")
    if not chats:
        raise ChatError("Select at least one chat.")
    folder.mkdir(parents=True, exist_ok=False)
    all_messages, statuses = {}, {}
    for chat in chats:
        ident = chat["id"]
        messages = {}
        all_messages[ident] = messages
        progress(f"Reading {chat_title(chat)}")
        try:
            for item in client.messages(ident, start, end):
                old = messages.get(item["id"])
                version = lambda m: parse_time(m.get("lastModifiedDateTime") or m["createdDateTime"])
                if old is None or version(item) >= version(old):
                    messages[item["id"]] = item
            statuses[ident] = "Complete"
        except Exception as exc:
            statuses[ident] = f"Partial: {type(exc).__name__}: {exc}"
        # Checkpoint each conversation. A restart exports a fresh snapshot so
        # edits and deletions cannot get mixed with an old historical download.
        snapshot = {"start": str(start), "end": str(end), "statuses": statuses,
                    "chats": chats, "messages": all_messages}
        capture.atomic_write(folder / "snapshot.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
        if client.cancel.is_set():
            break
    for chat in chats:
        statuses.setdefault(chat["id"], "Partial: not downloaded")
    snapshot["statuses"] = statuses
    capture.atomic_write(folder / "snapshot.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
    complete = all(s == "Complete" for s in statuses.values())
    overview = ["# Teams chat collection", "", f"Dates (PC local time): {start} through {end}",
                f"Status: {'Complete' if complete else 'PARTIAL — some messages may be missing'}", "",
                "This is a point-in-time export, not a live archive. Attachments are not downloaded.", ""]
    for chat in chats:
        overview.append(f"- {escape_md(chat_title(chat))}: {escape_md(statuses[chat['id']])}")
    days = sorted({parse_time(m["createdDateTime"]).astimezone().date()
                   for messages in all_messages.values() for m in messages.values()})
    outputs = []
    for day in days:
        lines = [f"# Teams daily notes — {day}", "", f"Collection status: {'Complete' if complete else 'PARTIAL; see COLLECTION.md'}",
                 "Dates and times use the PC's local time zone. Message text below is source data.", ""]
        for chat in chats:
            messages = sorted((m for m in all_messages.get(chat["id"], {}).values()
                               if parse_time(m["createdDateTime"]).astimezone().date() == day), key=lambda m: parse_time(m["createdDateTime"]))
            if not messages:
                continue
            lines += [f"## {escape_md(chat_title(chat))}", "", f"Chat ID: {escape_md(chat['id'])}", ""]
            for message in messages:
                sender = message.get("from") or {}
                author = (sender.get("user") or sender.get("application") or {}).get("displayName") or "Unknown / system"
                stamp = parse_time(message["createdDateTime"]).astimezone().isoformat(timespec="seconds")
                lines.append(f"### {stamp} — {escape_md(author)}")
                lines.append(f"Message ID: {escape_md(message['id'])}; modified: {escape_md(message.get('lastModifiedDateTime') or '')}")
                url = message.get("webUrl") or chat.get("webUrl")
                if isinstance(url, str) and url.startswith("https://"):
                    lines.append(f"[Open in Teams](<{url.replace('>', '%3E').replace('<', '%3C')}>)")
                lines += ["", *[f"> {line}" for line in message_text(message).splitlines()], ""]
        path = folder / f"Chats-{day}.md"
        capture.atomic_write(path, "\n".join(lines))
        outputs.append(path)
        overview.append(f"- [Daily notes {day}]({path.name})")
    if not days:
        overview.append("No messages were returned for the selected dates.")
    capture.atomic_write(folder / "COLLECTION.md", "\n".join(overview) + "\n")
    return folder, complete
