"""1-Click User Routines / Macros stored outside workspaces (v2.3)."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .local_store import checked, save_json

BUILTIN_ROUTINES: list[dict] = [
    {
        "id": "builtin-organize",
        "name": "Οργάνωση Φακέλου",
        "icon": "folder",
        "prompt": "Κάνε έναν ασφαλή έλεγχο του τρέχοντος φακέλου και πρότεινε οργάνωση των αρχείων ανά κατηγορία χωρίς να διαγράψεις τίποτα.",
        "builtin": True,
    },
    {
        "id": "builtin-summarize-docs",
        "name": "Σύνοψη Εγγράφων",
        "icon": "doc",
        "prompt": "Ψάξε τα τοπικά έγγραφα (PDF, DOCX, XLSX, MD) του project και φτιάξε μια καθαρή σύνοψη των βασικών σημείων.",
        "builtin": True,
    },
    {
        "id": "builtin-code-check",
        "name": "Έλεγχος Κώδικα & Tests",
        "icon": "code",
        "prompt": "Έλεγξε την κατάσταση του project (git status, βασικά αρχεία, tests) και δώσε μου μια σύντομη αναφορά υγείας.",
        "builtin": True,
    },
    {
        "id": "builtin-daily-brief",
        "name": "Ημερήσια Σύνοψη",
        "icon": "clock",
        "prompt": "Δες το ιστορικό, τις εκκρεμότητες και τα τελευταία τροποποιημένα αρχεία του project και ετοίμασε μια ημερήσια λίστα προτεραιοτήτων.",
        "builtin": True,
    },
]

MAX_CUSTOM_ROUTINES = 50


class RoutinesStore:
    """Manages built-in and custom 1-click user routines."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _load_custom(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            checked(self.path)
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict) and r.get("id") and r.get("name") and r.get("prompt")]
        except Exception:
            pass
        return []

    def _save_custom(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        save_json(self.path, items)

    def list_routines(self) -> list[dict]:
        return list(BUILTIN_ROUTINES) + self._load_custom()

    def add_routine(self, name: str, prompt: str, icon: str = "star") -> dict:
        clean_name = (name or "").strip()[:80]
        clean_prompt = (prompt or "").strip()[:2000]
        if not clean_name:
            raise ValueError("Δώσε ένα όνομα για τη ρουτίνα.")
        if not clean_prompt:
            raise ValueError("Δώσε το κείμενο εντολής (prompt) για τη ρουτίνα.")
        custom = self._load_custom()
        if len(custom) >= MAX_CUSTOM_ROUTINES:
            raise ValueError(f"Μέγιστος αριθμός προσαρμοσμένων ρουτινών: {MAX_CUSTOM_ROUTINES}.")
        entry = {
            "id": f"rtn-{uuid.uuid4().hex[:10]}",
            "name": clean_name,
            "icon": (icon or "star").strip()[:20],
            "prompt": clean_prompt,
            "builtin": False,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        custom.append(entry)
        self._save_custom(custom)
        return entry

    def delete_routine(self, routine_id: str) -> bool:
        rid = (routine_id or "").strip()
        if not rid or rid.startswith("builtin-"):
            raise ValueError("Δεν μπορείς να διαγράψεις μια ενσωματωμένη ρουτίνα.")
        custom = self._load_custom()
        kept = [r for r in custom if r.get("id") != rid]
        if len(kept) == len(custom):
            return False
        self._save_custom(kept)
        return True
