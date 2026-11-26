"""Email assistant plugin: read-only IMAP summary, local drafts, explicit-approval SMTP, injection guard (L4)."""
from __future__ import annotations

import email
import imaplib
import smtplib
import time
import uuid
from dataclasses import dataclass
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

from .injection_guard import guard_untrusted_text, scan_prompt_injection
from .local_store import load_json, save_json
from .settings import mask_secret, unmask_secret

URGENT_KEYWORDS = ("επείγον", "urgent", "τιμολόγιο", "invoice", "προθεσμία", "deadline", "πληρωμή", "payment", "ασφάλεια", "security")


@dataclass(frozen=True)
class EmailAccount:
    address: str
    secret_masked: str
    imap_host: str = "imap.gmail.com"
    smtp_host: str = "smtp.gmail.com"
    read_only: bool = True
    auth_mode: str = "password"  # "password" | "oauth2"

    @classmethod
    def create(
        cls,
        address: str,
        secret: str,
        *,
        imap_host: str = "imap.gmail.com",
        smtp_host: str = "smtp.gmail.com",
        read_only: bool = True,
        auth_mode: str = "password",
    ) -> "EmailAccount":
        clean_addr = str(address or "").strip()
        clean_sec = str(secret or "").strip()
        if "@" not in clean_addr or not clean_sec:
            raise ValueError("Χρειάζεται έγκυρη διεύθυνση email και κωδικός/token.")
        if auth_mode not in ("password", "oauth2"):
            raise ValueError("Ο τρόπος σύνδεσης πρέπει να είναι 'password' ή 'oauth2'.")
        return cls(
            address=clean_addr,
            secret_masked=mask_secret(clean_sec),
            imap_host=imap_host.strip() or "imap.gmail.com",
            smtp_host=smtp_host.strip() or "smtp.gmail.com",
            read_only=bool(read_only),
            auth_mode=auth_mode,
        )

    def secret(self) -> str:
        return unmask_secret(self.secret_masked)

    def to_public_dict(self) -> dict:
        return {
            "address": self.address,
            "imap_host": self.imap_host,
            "smtp_host": self.smtp_host,
            "read_only": self.read_only,
            "auth_mode": self.auth_mode,
            "has_secret": bool(self.secret_masked),
        }

    def save(self, path: Path) -> None:
        save_json(
            path,
            {
                "address": self.address,
                "secret_masked": self.secret_masked,
                "imap_host": self.imap_host,
                "smtp_host": self.smtp_host,
                "read_only": self.read_only,
                "auth_mode": self.auth_mode,
            },
        )

    @classmethod
    def load(cls, path: Path) -> "EmailAccount":
        raw = load_json(path, dict)
        return cls(
            address=str(raw.get("address") or ""),
            secret_masked=str(raw.get("secret_masked") or ""),
            imap_host=str(raw.get("imap_host") or "imap.gmail.com"),
            smtp_host=str(raw.get("smtp_host") or "smtp.gmail.com"),
            read_only=raw.get("read_only", True) is not False,
            auth_mode=str(raw.get("auth_mode") or "password"),
        )


def decode_mime_header(raw: str) -> str:
    parts: list[str] = []
    for text, enc in decode_header(raw or ""):
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(text))
    return "".join(parts).strip()


def sanitize_email_entry(uid: str, sender: str, subject: str, body: str) -> dict:
    """Wrap email content as strictly untrusted data and flag prompt-injection attempts."""
    combined = f"Subject: {subject}\n{body}".strip()
    signals = scan_prompt_injection(combined)
    safe_text = guard_untrusted_text(combined[:8000], source=f"email:{sender}")
    lowered = combined.casefold()
    important = any(kw in lowered for kw in URGENT_KEYWORDS)
    return {
        "uid": str(uid),
        "sender": sender,
        "subject": subject,
        "snippet": body.strip().replace("\n", " ")[:220],
        "important": important,
        "quarantined": bool(signals),
        "injection_signals": signals,
        "safe_data": f"<<<UNTRUSTED_EMAIL_DATA>>>\n{safe_text}\n<<<END_UNTRUSTED_EMAIL_DATA>>>",
    }


def _extract_plain(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition") or "").lower()
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()


def default_imap_fetch(account: EmailAccount, limit: int = 20) -> list[dict]:
    """Read-only IMAP4_SSL fetch using BODY.PEEK[] (never marks messages as Seen)."""
    items: list[dict] = []
    with imaplib.IMAP4_SSL(account.imap_host, 993) as imap:
        imap.login(account.address, account.secret())
        imap.select("INBOX", readonly=True)
        status, data = imap.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        uids = [int(u) for u in data[0].split() if u.isdigit()][-max(1, min(limit, 50)):]
        for uid in reversed(uids):
            st, fetched = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
            if st != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            parsed = email.message_from_bytes(fetched[0][1])
            auth = str(parsed.get("Authentication-Results") or "").lower()
            if any(bad in auth for bad in ("dmarc=fail", "spf=fail", "dkim=fail")):
                continue
            sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
            subject = decode_mime_header(parsed.get("Subject", ""))
            body = _extract_plain(parsed)
            items.append({"uid": str(uid), "sender": sender, "subject": subject, "body": body})
    return items


def default_smtp_send(account: EmailAccount, draft: dict) -> None:
    msg = EmailMessage()
    msg["From"] = account.address
    msg["To"] = draft["to"]
    msg["Subject"] = draft["subject"]
    msg.set_content(draft["body"])
    with smtplib.SMTP_SSL(account.smtp_host, 465, timeout=20) as smtp:
        smtp.login(account.address, account.secret())
        smtp.send_message(msg)


class EmailDraftStore:
    """Persists outbound email drafts locally; sending requires explicit owner approval."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def list_drafts(self) -> list[dict]:
        data = load_json(self.path, lambda: {"drafts": []})
        return list(data.get("drafts") or []) if isinstance(data, dict) else []

    def create(self, to: str, subject: str, body: str, *, in_reply_to: str = "") -> dict:
        clean_to = str(to or "").strip()
        if "@" not in clean_to or any(ch in clean_to for ch in ("\r", "\n")):
            raise ValueError("Μη έγκυρος παραλήπτης email.")
        draft = {
            "id": f"drf-{uuid.uuid4().hex[:10]}",
            "to": clean_to,
            "subject": str(subject or "(χωρίς θέμα)").replace("\r", " ").replace("\n", " ").strip()[:200],
            "body": str(body or "").strip()[:20_000],
            "in_reply_to": str(in_reply_to or "").strip()[:120],
            "status": "draft",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sent_at": "",
        }
        drafts = self.list_drafts()
        drafts.append(draft)
        save_json(self.path, {"drafts": drafts})
        return draft

    def mark_sent(self, draft_id: str) -> dict:
        drafts = self.list_drafts()
        for row in drafts:
            if row.get("id") == draft_id:
                row["status"] = "sent"
                row["sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                save_json(self.path, {"drafts": drafts})
                return row
        raise ValueError(f"Δεν βρέθηκε πρόχειρο με ID: {draft_id}")


class EmailAssistant:
    """High-level email assistant: summarize inbox, draft replies, never send without owner click."""

    def __init__(
        self,
        account: EmailAccount,
        drafts_path: Path,
        *,
        fetcher: Callable[[EmailAccount, int], list[dict]] | None = None,
        sender: Callable[[EmailAccount, dict], None] | None = None,
    ) -> None:
        self.account = account
        self.drafts = EmailDraftStore(drafts_path)
        self.fetcher = fetcher or default_imap_fetch
        self.sender = sender or default_smtp_send

    def summarize_inbox(self, limit: int = 20) -> dict:
        raw_items = self.fetcher(self.account, limit)
        entries = [
            sanitize_email_entry(
                str(r.get("uid") or idx),
                str(r.get("sender") or ""),
                str(r.get("subject") or ""),
                str(r.get("body") or ""),
            )
            for idx, r in enumerate(raw_items, start=1)
        ]
        important = [e for e in entries if e["important"]]
        quarantined = [e for e in entries if e["quarantined"]]
        return {
            "total": len(entries),
            "important_count": len(important),
            "quarantined_count": len(quarantined),
            "messages": entries,
            "important": important,
        }

    def draft_reply(self, to: str, subject: str, body: str, *, in_reply_to: str = "") -> dict:
        """Always creates a local draft — NEVER sends an email automatically."""
        return self.drafts.create(to, subject, body, in_reply_to=in_reply_to)

    def send_approved_draft(self, draft_id: str, *, user_approved: bool = False) -> dict:
        """Send an existing draft ONLY when read_only is False AND user explicitly approved."""
        if not user_approved:
            raise PermissionError("Απαιτείται ρητή έγκριση χρήστη (κουμπί Αποστολή) πριν φύγει οποιοδήποτε email.")
        if self.account.read_only:
            raise PermissionError("Ο λογαριασμός Email είναι σε λειτουργία μόνο ανάγνωσης (read-only). Δεν έγινε αποστολή.")
        draft = next((d for d in self.drafts.list_drafts() if d.get("id") == draft_id), None)
        if draft is None:
            raise ValueError(f"Δεν βρέθηκε πρόχειρο email: {draft_id}")
        if draft.get("status") == "sent":
            raise ValueError("Το πρόχειρο έχει ήδη σταλεί.")
        self.sender(self.account, draft)
        return self.drafts.mark_sent(draft_id)
