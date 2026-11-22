"""Συνεδρίες: οι συζητήσεις αποθηκεύονται ανά project και μπορείς να τις συνεχίσεις.

Κάθε συνεδρία είναι ένα αρχείο `.agent/sessions/<id>.json` μέσα στο project. Κρατάει
τα πλήρη μηνύματα (για να συνεχίσει το μοντέλο από εκεί που έμεινε), τον μετρητή
tokens και ένα σύντομο «transcript» (μόνο όσα λες εσύ και όσα απαντάει) για την οθόνη.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .workspace import AGENT_DIR

SESSIONS_DIR = "sessions"
DEFAULT_TITLE = "Νέα συνομιλία"
TITLE_MAX = 60


class SessionStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.dir = self.root / AGENT_DIR / SESSIONS_DIR

    # ---------- αρχεία ----------

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def _write(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self._path(str(rec["id"]))
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            temp.replace(target)
        except OSError:
            # Σπάνια περίπτωση: αν το replace αποτύχει, προσπάθησε άμεση εγγραφή.
            target.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------- ενέργειες ----------

    def create(self, source=None, session_id=None) -> str:
        """Φτιάχνει μια κενή συνεδρία και γυρίζει το id της."""
        rec = {
            "id": session_id or uuid.uuid4().hex[:12],
            "source": dict(source or {"platform":"desktop"}),
            "title": DEFAULT_TITLE,
            "created_at": time.time(),
            "updated_at": time.time(),
            "messages": [],
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
            "transcript": [],
        }
        if not str(rec["id"]).isalnum() or self._path(rec["id"]).exists():
            raise ValueError("Μη έγκυρη ή υπάρχουσα συνεδρία.")
        self._write(rec)
        return rec["id"]

    def load(self, session_id: str) -> dict | None:
        """Διαβάζει μια συνεδρία. Αν λείπει ή είναι χαλασμένη, γυρίζει None (να μη σκάσει)."""
        p = self._path(session_id)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        data.setdefault("messages", [])
        data.setdefault("tokens", {"prompt_tokens": 0, "completion_tokens": 0})
        data.setdefault("transcript", [])
        return data

    def save(
        self,
        session_id: str,
        *,
        messages: list[dict] | None = None,
        tokens: dict | None = None,
        transcript: list[dict] | None = None,
        title: str | None = None,
        repair: dict | None = None,
        task_state: dict | None = None,
        request_cache: dict | None = None,
        files: list[dict] | None = None,
    ) -> dict:
        """Γράφει (ή ενημερώνει) μια συνεδρία. Ο τίτλος βγαίνει από το πρώτο σου μήνυμα."""
        rec = self.load(session_id) or {"id": session_id, "created_at": time.time()}
        rec["id"] = session_id
        if messages is not None:
            rec["messages"] = list(messages)
        if tokens is not None:
            rec["tokens"] = dict(tokens)
        if transcript is not None:
            rec["transcript"] = list(transcript)
        if repair is not None:
            rec["repair"] = dict(repair)
        if task_state is not None:
            rec["task_state"] = dict(task_state)
        if request_cache is not None:
            rec['request_cache'] = dict(request_cache)
        if files is not None:
            rec['files'] = list(files)
        if title is not None:
            rec["title"] = str(title)[:TITLE_MAX] or DEFAULT_TITLE
        elif not rec.get("title") or rec.get("title") == DEFAULT_TITLE:
            rec["title"] = self._title_from(rec.get("transcript") or []) or DEFAULT_TITLE
        rec.setdefault("messages", [])
        rec.setdefault("tokens", {"prompt_tokens": 0, "completion_tokens": 0})
        rec.setdefault("transcript", [])
        rec["updated_at"] = time.time()
        self._write(rec)
        return rec

    def list(self) -> list[dict]:
        """Όλες οι συνεδρίες του project, οι πιο πρόσφατες πρώτα (χωρίς το βαρύ περιεχόμενο)."""
        if not self.dir.is_dir():
            return []
        rows: list[dict] = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(data, dict) or not data.get("id"):
                continue
            rows.append(
                {
                    "id": data.get("id"),
                    "title": data.get("title") or DEFAULT_TITLE,
                    "created_at": float(data.get("created_at") or 0),
                    "updated_at": float(data.get("updated_at") or 0),
                    "messages": len(data.get("messages") or []),
                    "source": data.get("source") or {"platform":"desktop"},
                }
            )
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return rows

    def delete(self, session_id: str) -> bool:
        p = self._path(session_id)
        if not p.is_file():
            return False
        p.unlink()
        return True

    # ---------- βοηθητικά ----------

    @staticmethod
    def _title_from(transcript: list[dict]) -> str:
        for item in transcript:
            if item.get("role") == "user":
                text = " ".join(str(item.get("text", "")).split()).strip()
                if text:
                    return text[:TITLE_MAX]
        return ""
