"""Telegram Bot API connector. No model invocation exists in a connector."""
from __future__ import annotations

import json
import mimetypes
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from akatikito.channel_sdk import ChannelError, HTTP, Inbound, chunks

DEFAULT_API_BASE = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{3,}:[A-Za-z0-9_-]{20,}$")
SAFE_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9:_-]{1,256}$")
TOKEN_LIKE = re.compile(r"bot\d{3,}:[A-Za-z0-9_-]{10,}")
CALLBACK_RE = re.compile(r"^a:([0-9a-f]{32}):([01])$")
MAX_APPROVAL_TEXT = 1500
MAX_FILE_BYTES = 10 * 1024 * 1024


def valid_token(token: str) -> bool:
    """Return True if token matches standard BotFather format (<digits>:<secret>)."""
    return bool(TOKEN_RE.match(str(token or "")))


def valid_api_base(base: str) -> bool:
    """Allow ONLY https://api.telegram.org or local loopback Bot API server (never foreign hosts)."""
    try:
        parts = urlsplit(str(base or ""))
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    if host in ("127.0.0.1", "localhost", "::1"):
        return parts.scheme in ("http", "https")
    return parts.scheme == "https" and host == "api.telegram.org"


def scrub_token(text: str, token: str = "") -> str:
    """Remove bot token and token-like substrings before logging or returning messages."""
    out = str(text or "")
    if token and len(token) >= 6:
        out = out.replace(token, "[hidden]")
    return TOKEN_LIKE.sub("bot[hidden]", out)


def _format_media(msg: dict) -> list[str]:
    media: list[str] = []
    if isinstance(msg.get("document"), dict):
        doc = msg["document"]
        name = doc.get("file_name") or "document"
        mime = doc.get("mime_type") or "file"
        media.append(f"[Συνημμένο έγγραφο Telegram: {name} ({mime})]")
    if isinstance(msg.get("voice"), dict):
        dur = msg["voice"].get("duration", 0)
        media.append(f"[Φωνητικό μήνυμα Telegram: {dur}s]")
    elif isinstance(msg.get("audio"), dict):
        aud = msg["audio"]
        label = aud.get("file_name") or aud.get("title") or "audio"
        media.append(f"[Αρχείο ήχου Telegram: {label}]")
    if isinstance(msg.get("photo"), list) and msg.get("photo"):
        media.append("[Συνημμένη εικόνα Telegram]")
    return media


def _parse_message_event(bot: str, ident: int, msg: dict) -> Inbound | None:
    user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    if user.get("is_bot") or not user.get("id") or not chat.get("id"):
        return None
    text = msg.get("text") or msg.get("caption") or ""
    media = _format_media(msg)
    if media:
        text = ("\n".join(media) + ("\n" + text if text else "")).strip()
    if not text:
        return None
    thread = str(msg.get("message_thread_id") or "")
    return Inbound("telegram", bot, str(chat["id"]), str(user["id"]), str(ident), text, thread, chat.get("type") != "private")


def _parse_callback_event(bot: str, ident: int, cb: dict, http) -> Inbound | None:
    user = cb.get("from") or {}
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    data = str(cb.get("data") or "")
    match = CALLBACK_RE.match(data)
    if user.get("is_bot") or not user.get("id") or not chat.get("id") or not match:
        return None
    cb_id = str(cb.get("id") or "")
    if cb_id:
        try:
            http.request("POST", "/answerCallbackQuery", {"callback_query_id": cb_id})
        except ChannelError:  # quality: allow S110 — best-effort Telegram callback acknowledgement
            pass
    ticket, decision = match.group(1), match.group(2)
    cmd = f"/approve {ticket} {decision}"
    thread = str(msg.get("message_thread_id") or "")
    return Inbound("telegram", bot, str(chat["id"]), str(user["id"]), str(ident), cmd, thread, chat.get("type") != "private")


class Telegram:
    def __init__(self, config, http=None):
        token = str(config.get("token") or "")
        if not SAFE_TOKEN_CHARS.match(token):
            raise ChannelError("Μη έγκυρη μορφή Telegram bot token.")
        api_base = str(config.get("api_base") or DEFAULT_API_BASE).rstrip("/")
        if not valid_api_base(api_base):
            raise ChannelError("Μη επιτρεπτός host για Telegram Bot API.")
        self._token = token
        self.api_base = api_base
        self.http = http or HTTP(f"{api_base}/bot{token}")
        self.bot = ""

    def connect(self):
        result = self.http.request("POST", "/getMe", {})
        if not result.get("ok") or not result.get("result", {}).get("is_bot"):
            raise ChannelError("Δεν αναγνωρίστηκε το Telegram bot.")
        self.bot = str(result["result"]["id"])
        return self.bot

    def poll(self, cursor, stopped):
        payload = {
            "offset": int(cursor or 0),
            "timeout": 20,
            "limit": 50,
            "allowed_updates": ["message", "callback_query"],
        }
        body = self.http.request("POST", "/getUpdates", payload)
        if not body.get("ok"):
            raise ChannelError("Αποτυχία παραλαβής Telegram.")
        events: list[Inbound] = []
        next_cursor = int(cursor or 0)
        for row in sorted(body.get("result", []), key=lambda r: r.get("update_id", 0)):
            ident = row.get("update_id")
            if type(ident) is not int:
                continue
            next_cursor = max(next_cursor, ident + 1)
            event = None
            if isinstance(row.get("callback_query"), dict):
                event = _parse_callback_event(self.bot, ident, row["callback_query"], self.http)
            elif isinstance(row.get("message"), dict):
                event = _parse_message_event(self.bot, ident, row["message"])
            if event is not None:
                events.append(event)
        return str(next_cursor), events

    def send(self, event, text):
        safe_text = scrub_token(text, self._token)
        for part in chunks(safe_text, 3500):
            data: dict[str, object] = {"chat_id": event.chat, "text": part}
            if event.thread:
                data["message_thread_id"] = int(event.thread)
            body = self.http.request("POST", "/sendMessage", data)
            if not body.get("ok"):
                raise ChannelError("Η απάντηση Telegram δεν επιβεβαιώθηκε.")

    def send_approval(self, event, ticket: str, summary: str, *, dangerous: bool = False) -> None:
        """Send inline Approve/Deny buttons for safe-to-confirm actions; desktop-only for dangerous ones."""
        clean = scrub_token(summary, self._token)
        if dangerous or len(clean) > MAX_APPROVAL_TEXT or not re.fullmatch(r"[0-9a-f]{32}", str(ticket)):
            notice = (
                "Απαιτείται έγκριση στον υπολογιστή (επικίνδυνη ή μεγάλη ενέργεια — "
                "δεν επιτρέπεται απομακρυσμένη έγκριση).\n" + clean[:400]
            )
            self.send(event, notice)
            return
        data: dict[str, object] = {
            "chat_id": event.chat,
            "text": f"Απαιτείται έγκριση:\n{clean}",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "Έγκριση", "callback_data": f"a:{ticket}:1"},
                    {"text": "Απόρριψη", "callback_data": f"a:{ticket}:0"},
                ]]
            },
        }
        if event.thread:
            data["message_thread_id"] = int(event.thread)
        body = self.http.request("POST", "/sendMessage", data)
        if not body.get("ok"):
            raise ChannelError("Δεν στάλθηκε το αίτημα έγκρισης Telegram.")

    def send_file(self, event, file_path: str | Path, caption: str = "") -> None:
        """Upload a bounded regular file (<= 10 MiB) to the Telegram chat via multipart /sendDocument."""
        path = Path(file_path)
        if not path.is_file() or path.is_symlink():
            raise ChannelError("Το αρχείο προς αποστολή δεν είναι έγκυρο.")
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ChannelError("Το αρχείο υπερβαίνει το όριο των 10 MB για Telegram.")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        boundary = f"akatikito-{uuid.uuid4().hex}"
        body = _build_multipart(boundary, event, path.name, ctype, raw, scrub_token(caption, self._token))
        url = f"{self.api_base}/bot{self._token}/sendDocument"
        try:
            with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
                resp = client.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
                parsed = json.loads(resp.content)
        except (httpx.HTTPError, ValueError):
            raise ChannelError("Αποτυχία αποστολής αρχείου στο Telegram.") from None
        if resp.status_code >= 300 or not isinstance(parsed, dict) or not parsed.get("ok"):
            raise ChannelError("Το Telegram απέρριψε την αποστολή αρχείου.")

    def close(self):
        self.http.close()


def _build_multipart(boundary: str, event, filename: str, ctype: str, data: bytes, caption: str) -> bytes:
    parts: list[bytes] = []
    fields = [("chat_id", str(event.chat))]
    if event.thread:
        fields.append(("message_thread_id", str(int(event.thread))))
    if caption:
        fields.append(("caption", caption[:1000]))
    for key, val in fields:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode("utf-8"))
    safe_name = filename.replace('"', "_").replace("\r", "").replace("\n", "")
    header = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"document\"; filename=\"{safe_name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    parts.append(header + data + f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts)


def create(config):
    return Telegram(config)
