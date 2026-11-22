"""Ο χώρος εργασίας: περιορισμός μέσα στο project, αντίγραφα ασφαλείας, μνήμη."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

BACKUP_DIR = ".agent_backups"
AGENT_DIR = ".agent"
MEMORY_FILE = "MEMORY.md"


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Ο φάκελος του project δεν υπάρχει: {self.root}")

    # ---------- περιορισμός ----------

    def resolve(self, path: str) -> Path:
        """Επιστρέφει τη διαδρομή μόνο αν είναι μέσα στο project."""
        raw = Path(str(path).strip().strip('"'))
        target = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
        if target != self.root and self.root not in target.parents:
            raise PermissionError(
                f"Απαγορεύεται: η διαδρομή '{path}' είναι έξω από το project "
                f"({self.root}). Δούλεψε μέσα στο project."
            )
        return target

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(path)

    # ---------- αντίγραφα ασφαλείας ----------

    def _backup_dir(self) -> Path:
        d = self.root / BACKUP_DIR
        d.mkdir(exist_ok=True)
        return d

    def _index(self) -> Path:
        return self._backup_dir() / "index.jsonl"

    def backup(self, path: Path) -> str | None:
        """Κρατά αντίγραφο πριν από αλλαγή. Επιστρέφει το id του αντιγράφου."""
        if not path.is_file():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        rel = self.relative(path)
        name = rel.replace("/", "__")
        dest = self._backup_dir() / f"{stamp}-{uuid.uuid4().hex[:8]}__{name}"
        shutil.copy2(path, dest)
        with self._index().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"backup": dest.name, "original": rel, "at": stamp}, ensure_ascii=False) + "\n")
        return dest.name

    def list_backups(self, limit: int = 20) -> list[dict]:
        index = self._index()
        if not index.exists():
            return []
        rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
        return list(reversed(rows))[:limit]

    def restore(self, backup: str) -> str:
        """Γυρίζει ένα αρχείο στην κατάσταση του αντιγράφου."""
        entries = {row["backup"]: row for row in self.list_backups(limit=500)}
        row = entries.get(backup)
        if row is None:
            raise ValueError(f"Δεν βρέθηκε αντίγραφο '{backup}'.")
        target = self.resolve(row["original"])
        shutil.copy2(self._backup_dir() / backup, target)
        return row["original"]

    # ---------- μνήμη ----------

    def memory_path(self) -> Path:
        d = self.root / AGENT_DIR
        d.mkdir(exist_ok=True)
        return d / MEMORY_FILE

    def read_memory(self) -> str:
        from .local_store import checked
        p = self.memory_path()
        p = checked(p)
        return p.read_text(encoding="utf-8-sig").strip() if p.exists() else ""

    def append_memory(self, note: str) -> str:
        from .memory import append_note
        return append_note(self.root/AGENT_DIR/MEMORY_FILE,note,'στη μνήμη του project',
                           'Υπάρχει ήδη στη μνήμη του project. Δεν γράφτηκε ξανά: ')

    def append_decision(self, context: str, decision: str, outcome: str = "") -> str:
        """Κρατάει στο project memory τις σημαντικές αποφάσεις με αποτέλεσμα."""
        note = f"[{context}] {decision}"
        if outcome:
            note += f" → {outcome}"
        return self.append_memory(note)

    # ---------- κανόνες ----------

    def rules(self, mode='') -> str:
        sections = []
        for name in ("RULES.md", "AGENTS.md"):
            p = self.root / name
            if p.is_file():
                sections.append((name, p.read_text(encoding="utf-8-sig").strip()))
        folder = self.root / '.akatikito' / 'rules'
        if folder.is_dir():
            for p in sorted(folder.glob('*.md')):
                if p.is_symlink(): continue
                if p.stem.startswith('rules-') and p.stem != 'rules-'+mode: continue
                sections.append((str(p.relative_to(self.root)),p.read_text(encoding='utf-8-sig').strip()))
        if len(sections) == 1:
            return sections[0][1]
        return "\n\n".join(f"--- {name} ---\n{body}" for name, body in sections)


def detect_verify(root: str | Path) -> str:
    """Μαντεύει την εντολή ελέγχου του project (tests), ώστε να μη τη γράφει ο χρήστης."""
    root = Path(root)
    py = str(root / ".venv" / "Scripts" / "python.exe")
    if not Path(py).is_file():
        py = "python"
    tests = root / "tests"
    if tests.is_dir():
        import ast
        scripts = sorted(tests.glob("test_*.py"))
        standalone = []
        collected = []
        for script in scripts:
            try:
                tree = ast.parse(script.read_text(encoding="utf-8-sig"))
            except (OSError, SyntaxError):
                continue
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main" for n in tree.body):
                standalone.append(script)
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") or isinstance(n, ast.ClassDef) and n.name.startswith("Test") for n in tree.body):
                collected.append(script)
        commands = [f'"{py}" "{s}"' for s in standalone]
        if collected:
            commands.append(f'"{py}" -m pytest ' + " ".join(f'"{s}"' for s in collected) + " -q")
        if commands:
            return " && ".join(commands)
    for name in ("pytest.ini", "pyproject.toml", "tox.ini"):
        if (root / name).is_file():
            return f'"{py}" -m pytest -q'
    if (root / "package.json").is_file():
        return "npm test"
    return ""
