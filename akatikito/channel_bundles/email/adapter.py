"""Email (IMAP/SMTP) channel connector using Python stdlib only."""
from __future__ import annotations

import email
import imaplib
import smtplib
import time
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr

from akatikito.channel_sdk import ChannelError, Inbound, chunks


def _decode_hdr(raw: str) -> str:
    parts = []
    for text, enc in decode_header(raw or ""):
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(text))
    return "".join(parts).strip()


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp.lower():
                payload = part.get_payload(decode=True)
                if payload is not None:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace").strip()


class EmailChannel:
    """Polls an IMAP4_SSL inbox and replies via SMTP_SSL.

    Token format: ``user@example.com|app_password`` (defaults to Gmail IMAP/SMTP)
    or ``user@example.com|app_password|imap.host|smtp.host``.
    """

    def __init__(self, config: dict):
        raw = str(config.get("token") or "").strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 2 or "@" not in parts[0] or not parts[1]:
            raise ChannelError(
                "Το token για Email πρέπει να έχει μορφή: email@domain.com|app_password (ή |imap_host|smtp_host)."
            )
        self.address = parts[0]
        self.password = parts[1]
        self.imap_host = parts[2] if len(parts) >= 3 and parts[2] else "imap.gmail.com"
        self.smtp_host = parts[3] if len(parts) >= 4 and parts[3] else "smtp.gmail.com"
        self._subjects: dict[str, str] = {}

    def connect(self) -> str:
        try:
            with imaplib.IMAP4_SSL(self.imap_host, 993) as imap:
                imap.login(self.address, self.password)
                imap.select("INBOX", readonly=True)
            return self.address
        except Exception as exc:
            raise ChannelError(f"Αποτυχία σύνδεσης Email IMAP ({self.imap_host}): {exc}") from exc

    def poll(self, cursor: str, stopped) -> tuple[str, list[Inbound]]:
        last_uid = int(cursor) if str(cursor or "").isdigit() else 0
        events: list[Inbound] = []
        next_uid = last_uid
        try:
            with imaplib.IMAP4_SSL(self.imap_host, 993) as imap:
                imap.login(self.address, self.password)
                # Read-only select + BODY.PEEK[] ensures we NEVER mark user emails as \Seen behind their back
                imap.select("INBOX", readonly=True)
                criteria = f"UID {last_uid + 1}:*" if last_uid > 0 else "UNSEEN"
                status, data = imap.uid("search", None, criteria)
                if status != "OK" or not data or not data[0]:
                    time.sleep(5)
                    return str(next_uid), []
                uids = sorted(int(u) for u in data[0].split() if u.isdigit() and int(u) > last_uid)
                # Process oldest-first in bounded batches so pending older messages are NEVER skipped
                for uid in uids[:20]:
                    if stopped():
                        break
                    st, msg_data = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
                    if st != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                        continue
                    next_uid = max(next_uid, uid)
                    parsed = email.message_from_bytes(msg_data[0][1])
                    # Guard against obvious spoofed senders failing SPF/DKIM/DMARC
                    auth_res = str(parsed.get("Authentication-Results") or "").lower()
                    if any(bad in auth_res for bad in ("dmarc=fail", "spf=fail", "dkim=fail")):
                        continue
                    sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
                    if not sender or sender == self.address.lower():
                        continue
                    subject = _decode_hdr(parsed.get("Subject", ""))
                    body = _extract_body(parsed)
                    text = f"[Subject: {subject}]\n{body}".strip() if subject else body
                    if not text:
                        continue
                    self._subjects[str(uid)] = subject or "Akatikito"
                    events.append(
                        Inbound(
                            "email",
                            self.address,
                            sender,
                            sender,
                            str(uid),
                            text[:12000],
                            str(uid),
                            False,
                        )
                    )
        except Exception as exc:
            raise ChannelError(f"Σφάλμα λήψης Email: {exc}") from exc
        if not events:
            time.sleep(5)
        return str(next_uid), events

    def send(self, event: Inbound, text: str) -> None:
        subj = self._subjects.get(event.thread, "Akatikito")
        reply_subj = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        body = "\n\n".join(chunks(text, 20000))
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = event.chat
        msg["Subject"] = reply_subj
        msg.set_content(body)
        try:
            with smtplib.SMTP_SSL(self.smtp_host, 465, timeout=20) as smtp:
                smtp.login(self.address, self.password)
                smtp.send_message(msg)
        except Exception as exc:
            raise ChannelError(f"Αποτυχία αποστολής απάντησης Email: {exc}") from exc

    def close(self) -> None:
        pass


def create(config: dict) -> EmailChannel:
    return EmailChannel(config)
