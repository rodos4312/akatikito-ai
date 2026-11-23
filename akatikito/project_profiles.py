"""Per-project trust profiles stored outside the workspace (readonly | office | developer)."""
from __future__ import annotations

from pathlib import Path
from .local_store import LOCK, load_json, save_json

PROFILES = {
    "readonly": {
        "label": "Μόνο Ανάγνωση (Read-Only)",
        "hint": "Καμία εγγραφή, καμία εντολή συστήματος, καμία μετακίνηση αρχείων.",
    },
    "office": {
        "label": "Γραφείο & Έγγραφα (Office)",
        "hint": "Επιτρέπει Word/Excel/PDF, οργάνωση εγγράφων & ατζέντα. Απαγορεύει εντολές shell & αλλαγές κώδικα.",
    },
    "developer": {
        "label": "Πλήρης Ανάπτυξη (Developer)",
        "hint": "Πλήρη δικαιώματα σύμφωνα με το ενεργό mode (Κώδικας/Debug/Βοηθός).",
    },
}

DEFAULT_PROFILE = "developer"

_MUTATING_TOOLS = {
    "write_file",
    "edit_file",
    "run_command",
    "github_clone",
    "create_word_document",
    "create_excel_sheet",
    "create_pdf_document",
    "convert_data_file",
    "pdf_toolkit",
    "image_tools",
    "zip_manager",
    "plan_file_operations",
    "apply_file_operations",
    "plan_file_undo",
    "undo_file_operations",
    "add_reminder",
    "done_reminder",
}

_OFFICE_BLOCKED_TOOLS = {"run_command", "github_clone"}


class ProjectProfiles:
    """Persists owner-selected trust levels per project root in data/memory/project_profiles.json."""

    def __init__(self, path: Path | None = None):
        from . import memory

        self.path = Path(path) if path is not None else (Path(memory.DIR) / "project_profiles.json")

    def _load(self) -> dict[str, str]:
        try:
            data = load_json(self.path, dict)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if str(v) in PROFILES}
        except Exception:
            pass
        return {}

    def _key(self, root: str | Path) -> str:
        return str(Path(root).expanduser().resolve()).casefold()

    def get(self, root: str | Path) -> str:
        return self._load().get(self._key(root), DEFAULT_PROFILE)

    def set(self, root: str | Path, profile: str) -> dict:
        prof = str(profile or "").strip().lower()
        if prof not in PROFILES:
            raise ValueError(f"Μη έγκυρο προφίλ ασφαλείας project: {prof}")
        key = self._key(root)
        with LOCK:
            data = self._load()
            data[key] = prof
            save_json(self.path, data)
        return {"ok": True, "project": str(Path(root).expanduser().resolve()), "profile": prof, "profiles": PROFILES}

    @staticmethod
    def check_tool(profile: str, tool_name: str) -> str | None:
        """Return a Greek denial reason if the project profile forbids this tool, else None."""
        prof = profile if profile in PROFILES else DEFAULT_PROFILE
        if prof == "readonly" and tool_name in _MUTATING_TOOLS:
            return "ΑΠΟΡΡΙΨΗ: το project είναι σε προφίλ «Μόνο Ανάγνωση» (Read-Only). Άλλαξε προφίλ ασφαλείας για τροποποιήσεις."
        if prof == "office" and tool_name in _OFFICE_BLOCKED_TOOLS:
            return "ΑΠΟΡΡΙΨΗ: το project είναι σε προφίλ «Γραφείο & Έγγραφα» — δεν επιτρέπονται εντολές shell ή git clone."
        return None
