"""WhatsApp Cloud API connector (Channel API v1). No model invocation exists in a connector."""
from __future__ import annotations

import threading
from urllib.parse import urlsplit

from akatikito.channel_sdk import ChannelError, HTTP, Inbound, chunks

DEFAULT_GRAPH_BASE = "https://graph.facebook.com"
API_VERSION = "v21.0"


def valid_whatsapp_api_base(base: str) -> bool:
    """Allow ONLY https://graph.facebook.com or local loopback (never arbitrary foreign hosts)."""
    try:
        parts = urlsplit(str(base or ""))
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    if host in ("127.0.0.1", "localhost", "::1"):
        return parts.scheme in ("http", "https")
    return parts.scheme == "https" and host == "graph.facebook.com"


def _extract_message_text(msg: dict) -> str:
    mtype = str(msg.get("type") or "")
    if mtype == "text":
        return str((msg.get("text") or {}).get("body") or "").strip()
    if mtype == "audio":
        return "[Φωνητικό μήνυμα WhatsApp]"
    if mtype == "document":
        doc = msg.get("document") or {}
        name = doc.get("filename") or "document"
        cap = str(doc.get("caption") or "").strip()
        return f"[Συνημμένο έγγραφο WhatsApp: {name}]" + (f"\n{cap}" if cap else "")
    if mtype == "image":
        cap = str((msg.get("image") or {}).get("caption") or "").strip()
        return "[Συνημμένη εικόνα WhatsApp]" + (f"\n{cap}" if cap else "")
    return ""


def _iter_webhook_messages(payload: dict) -> list[dict]:
    found: list[dict] = []
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            for msg in value.get("messages") or []:
                if isinstance(msg, dict):
                    found.append(msg)
    return found


class WhatsAppChannel:
    """WhatsApp Cloud API adapter; token format: `phone_number_id|access_token`."""

    def __init__(self, config: dict, http=None) -> None:
        raw = str(config.get("token") or "").strip()
        parts = [p.strip() for p in raw.split("|", 1)]
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1] or any(ch.isspace() for ch in parts[1]):
            raise ChannelError("Το token WhatsApp πρέπει να έχει μορφή: phone_number_id|access_token.")
        api_base = str(config.get("api_base") or DEFAULT_GRAPH_BASE).rstrip("/")
        if not valid_whatsapp_api_base(api_base):
            raise ChannelError("Μη επιτρεπτός host για WhatsApp Cloud API.")
        self.phone_number_id = parts[0]
        self._access_token = parts[1]
        self.api_base = api_base
        self.http = http or HTTP(api_base, headers={"Authorization": f"Bearer {self._access_token}"})
        self.bot = ""
        self._lock = threading.Lock()
        self._queue: list[Inbound] = []
        self._seen: set[str] = set()

    def connect(self) -> str:
        res = self.http.request("GET", f"/{API_VERSION}/{self.phone_number_id}")
        ident = str(res.get("id") or "")
        if ident != self.phone_number_id:
            raise ChannelError("Δεν επαληθεύτηκε ο λογαριασμός WhatsApp Cloud API.")
        self.bot = ident
        return self.bot

    def ingest_webhook_payload(self, payload: dict) -> list[Inbound]:
        """Normalize an incoming Meta webhook JSON payload into deduplicated Inbound events."""
        added: list[Inbound] = []
        for msg in _iter_webhook_messages(payload):
            msg_id = str(msg.get("id") or "").strip()
            sender = str(msg.get("from") or "").strip()
            text = _extract_message_text(msg)
            if not msg_id or not sender or not text:
                continue
            with self._lock:
                if msg_id in self._seen:
                    continue
                self._seen.add(msg_id)
                ev = Inbound("whatsapp", self.bot or self.phone_number_id, sender, sender, msg_id, text[:12000], "", False)
                self._queue.append(ev)
                added.append(ev)
        return added

    def poll(self, cursor: str, stopped) -> tuple[str, list[Inbound]]:
        if stopped():
            return str(cursor or "0"), []
        with self._lock:
            batch = list(self._queue)
            self._queue.clear()
        next_cursor = str(int(cursor) + len(batch)) if str(cursor or "").isdigit() else str(len(batch))
        return next_cursor, batch

    def send(self, event: Inbound, text: str) -> None:
        safe = str(text or "").replace(self._access_token, "[hidden]")
        for part in chunks(safe, 3500):
            payload = {
                "messaging_product": "whatsapp",
                "to": event.chat,
                "type": "text",
                "text": {"preview_url": False, "body": part},
            }
            res = self.http.request("POST", f"/{API_VERSION}/{self.phone_number_id}/messages", payload)
            if not res.get("messages"):
                raise ChannelError("Η αποστολή μηνύματος WhatsApp δεν επιβεβαιώθηκε.")

    def close(self) -> None:
        self.http.close()


def create(config: dict) -> WhatsAppChannel:
    return WhatsAppChannel(config)
