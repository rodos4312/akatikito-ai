"""Local memory: MEMORY.md and a structured profile.json with facts, beliefs and revisions.

Legacy USER.md is preserved verbatim during migration. Agent proposals require
owner approval. Model excerpts are bounded; original notes are never trimmed.
"""

from __future__ import annotations

import os
import re
import unicodedata
import hashlib
from pathlib import Path
from .local_store import LOCK, checked, atomic_bytes

ROOT = Path(__file__).resolve().parents[1]
DIR = Path(os.getenv("AKATIKITO_MEMORY_DIR", str(ROOT / "data" / "memory")))
LIMIT = 4000  # χαρακτήρες ανά αρχείο

FILES = {"memory": "MEMORY.md", "user": "USER.md"}


def _path(kind: str) -> Path:
    path = checked(DIR / FILES.get(kind, FILES["memory"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read(kind: str = "memory") -> str:
    path = _path(kind)
    return path.read_text(encoding="utf-8-sig").strip() if path.is_file() else ""


def note_key(value):
    value="".join(c for c in unicodedata.normalize("NFD",str(value).casefold()) if not unicodedata.combining(c))
    # Ignore sentence punctuation, not meaningful symbols in C++, paths or versions.
    return " ".join(value.split()).rstrip('.!?;· ')


def contains(note, directory=None):
    path=checked(Path(directory if directory is not None else DIR)/FILES['memory'])
    with LOCK:
        text=path.read_bytes().decode('utf-8-sig') if path.is_file() else ''
        return any(note_key(line.lstrip('- '))==note_key(note) for line in text.splitlines())


def append_note(path, note, label, duplicate):
    """Append without rewriting old bytes; reject stale snapshots instead of losing edits."""
    from . import memory_guard
    reason=memory_guard.scan(note)
    if reason:
        return memory_guard.refusal(reason)
    note = " ".join(str(note).split()).strip()
    if not note:
        return "Κενή σημείωση — δεν αποθηκεύτηκε."
    with LOCK:
        path=checked(Path(path))
        exists=path.is_file()
        raw=path.read_bytes() if exists else b''
        current=raw.decode('utf-8-sig')
        if any(note_key(line.lstrip('- '))==note_key(note) for line in current.splitlines()):
            return duplicate + note
        newline=re.search(rb'\r\n|\r|\n',raw)
        newline=newline.group() if newline else b'\n'
        separator=b'' if not raw or raw.endswith((b'\r',b'\n')) else newline
        updated=raw+separator+b'- '+note.encode('utf-8')+newline
        atomic_bytes(path,updated,expected=hashlib.sha256(raw).hexdigest() if exists else None)
    return f"Αποθηκεύτηκε {label}: {note}"


def append(kind: str, note: str, *, directory=None) -> str:
    """Local append. Agent global/user proposals require owner approval in Profile."""
    path=Path(directory if directory is not None else DIR)/FILES.get(kind,FILES['memory'])
    label='στο προφίλ σου' if kind=='user' else 'στη γενική μνήμη'
    return append_note(path,note,label,'Υπάρχει ήδη στη μνήμη. Δεν γράφτηκε ξανά: ')


def snapshot() -> tuple[str, str]:
    """(γενικές σημειώσεις, στοιχεία χρήστη) — έτοιμα για το system prompt."""
    from .profile import Profile
    return read("memory"), Profile().prompt()
