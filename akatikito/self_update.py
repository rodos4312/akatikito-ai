"""Candidate-based self updates. This is a validation workflow, not an OS sandbox."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from .verification import run_checks

ROOT_FILES = {"app.py", "Akatikito.cmd", "AkatikitoApp.cmd", "PLAN.md", "PLAN-UI.md", "PLAN-ROADMAP.md", "FEATURES.md", "VOICE.md", "requirements-features.txt"}
PROTECTED = {"akatikito/self_update.py", "akatikito/verification.py", "tests/test_self_update.py", "tests/test_ui.cjs"}


def source_files(root: Path) -> dict[str, bytes]:
    files = {}
    paths = [root / n for n in ROOT_FILES if (root / n).is_file()]
    for folder in ("akatikito", "tests"):
        for directory, dirs, names in os.walk(root / folder, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__" and not (Path(directory) / d).is_symlink()]
            for name in names:
                p = Path(directory) / name
                if p.suffix in {".py", ".html", ".css", ".js", ".cjs"}:
                    paths.append(p)
    for folder in (root/'skills',root/'.akatikito'/'rules'):
        if folder.is_dir(): paths.extend(sorted(folder.glob('*.md')))
    for p in paths:
        if p.is_symlink() or getattr(p.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Δεν επιτρέπεται σύνδεσμος: {p}")
        if not p.resolve().is_relative_to(root.resolve()):
            raise ValueError("Διαδρομή έξω από το υποψήφιο.")
        files[p.relative_to(root).as_posix()] = p.read_bytes()
    return files


def digest(files: dict[str, bytes]) -> dict[str, str]:
    return {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}


class SelfUpdate:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.id = uuid.uuid4().hex[:12]
        from .process_lock import writable_dir
        env_temp = os.environ.get("AKATIKITO_TEMP_DIR", "").strip()
        candidates: list[Path] = []
        if env_temp:
            candidates.append(Path(env_temp))
        if Path("D:/").exists():
            candidates.append(Path("D:/Meu-Temp/akatikito-updates"))
        candidates.append(Path(tempfile.gettempdir()) / "akatikito-updates")
        staging_root = writable_dir(*candidates)
        self.base = staging_root / self.id
        self.stage = self.base / "candidate"
        self.original = source_files(self.root)
        if not self.original:
            raise ValueError("Δεν βρέθηκε πηγαίος κώδικας.")
        for name, data in self.original.items():
            p = self.stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.validated = None
        self.report = "Δεν έχουν τρέξει ακόμη οι έλεγχοι."
        self.backup = ""
        self.applied = False

    def restore_pending(self) -> list[str]:
        pointer = self.root / "data" / "pending-upgrade.json"
        if not pointer.is_file():
            return []
        pending = json.loads(pointer.read_text(encoding="utf-8-sig"))
        staged = []
        for change in pending.get("changes", []):
            name = change["path"]
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or name in PROTECTED:
                raise ValueError("Μη έγκυρη αποθηκευμένη αλλαγή υποψηφίου.")
            if not (name in ROOT_FILES or relative.parts[0] in ("akatikito", "tests") and relative.suffix in (".py", ".html", ".css", ".js", ".cjs")):
                raise ValueError("Η αποθηκευμένη αλλαγή δεν ανήκει στον πηγαίο κώδικα.")
            content = change["content"].encode("utf-8")
            current = self.original.get(name)
            if current == content:
                continue
            current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
            if current_hash != change.get("base_sha256"):
                raise ValueError(f"Η προηγούμενη δουλειά κρατήθηκε αλλά υπάρχει σύγκρουση στο {name}. Δεν αντικαταστάθηκε κανένα αρχείο.")
            if name.startswith("tests/") and current is not None:
                raise ValueError("Δεν μεταφέρεται αλλαγή σε υπάρχον προστατευμένο test.")
            target = self.stage / relative
            if not target.resolve().is_relative_to(self.stage.resolve()):
                raise ValueError("Η αποθηκευμένη διαδρομή βγαίνει έξω από το υποψήφιο.")
            staged.append((target, content, name))
        # Validate all entries before writing any into the fresh candidate.
        for target, content, name in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self._pending_loaded = True
        return [name for target, content, name in staged]

    def _candidate(self):
        candidate = source_files(self.stage)
        if set(self.original) - set(candidate):
            raise ValueError("Δεν επιτρέπεται αφαίρεση αρχείων στην αυτόματη αναβάθμιση.")
        for name, original in self.original.items():
            if (name.startswith("tests/") or name in PROTECTED) and candidate[name] != original:
                raise ValueError(f"Ο υπάρχων έλεγχος/μηχανισμός προστασίας δεν αλλάζει αυτόματα: {name}")
        if digest(source_files(self.root)) != digest(self.original):
            raise ValueError("Τα κανονικά αρχεία άλλαξαν μετά τη δημιουργία του υποψηφίου. Φτιάξε νέο υποψήφιο.")
        return candidate

    def validate(self) -> tuple[bool, str]:
        self.validated = None
        try:
            candidate = self._candidate()
            before = digest(candidate)
            # Run all baseline tests and any newly added tests. Existing tests cannot be weakened.
            scripts = sorted(n for n in candidate if n.startswith("tests/test_") and n.endswith(".py"))
            if "tests/smoke.py" in candidate:
                scripts.append("tests/smoke.py")
            if not scripts:
                raise ValueError("Δεν υπάρχουν εκτελέσιμοι έλεγχοι.")
            ok, self.report = run_checks(self.stage, scripts)
            if not ok:
                return False, self.report
            if digest(self._candidate()) != before:
                raise ValueError("Το υποψήφιο άλλαξε όσο έτρεχαν οι έλεγχοι. Χρειάζεται νέος έλεγχος.")
            self.validated = before
            return True, self.report
        except Exception as exc:
            self.report = f"ΑΠΟΡΡΙΨΗ: {exc}"
            return False, self.report

    def status(self) -> dict:
        try:
            candidate = self._candidate()
            changed = sorted(k for k, v in candidate.items() if self.original.get(k) != v)
            ready = self.validated is not None and digest(candidate) == self.validated and bool(changed)
        except Exception:
            changed, ready = [], False
        return {"candidate": str(self.stage), "changed": changed, "ready": ready,
                "report": self.report, "backup": self.backup, "applied": self.applied}

    def apply(self) -> dict:
        candidate = self._candidate()
        if self.applied or self.validated is None or digest(candidate) != self.validated:
            raise ValueError("Απαιτείται επιτυχής έλεγχος των ίδιων ακριβώς αρχείων πριν την εφαρμογή.")
        changed = {k: v for k, v in candidate.items() if self.original.get(k) != v}
        if not changed:
            raise ValueError("Δεν υπάρχουν αλλαγές.")
        default_backups = Path("D:/Akatikito-Backups") if Path("D:/").exists() else (self.root.parent / "Akatikito-Backups")
        backup_root = Path(os.environ.get("AKATIKITO_BACKUP_DIR") or default_backups)
        backup = backup_root / ("self-update-" + self.id)
        backup.mkdir(parents=True, exist_ok=False)
        for name in changed:
            if name in self.original:
                p = backup / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(self.original[name])
        (backup / "manifest.json").write_text(json.dumps({"root": str(self.root), "changed": sorted(changed),
            "new": [k for k in changed if k not in self.original], "before": digest(self.original),
            "after": self.validated}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.backup = str(backup)
        written = []
        try:
            for name, data in changed.items():
                target = self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".update-" + self.id)
                tmp.write_bytes(data)
                os.replace(tmp, target)
                written.append(name)
            for name, data in changed.items():
                if (self.root / name).read_bytes() != data:
                    raise OSError(f"Δεν επαληθεύτηκε η εγγραφή: {name}")
        except Exception:
            for name in reversed(written):
                target = self.root / name
                if name in self.original:
                    target.write_bytes(self.original[name])
                else:
                    target.unlink(missing_ok=True)
            raise
        self.applied = True
        if getattr(self, "_pending_loaded", False):
            try:
                (self.root / "data" / "pending-upgrade.json").unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": True, "changed": sorted(changed), "backup": self.backup,
                "notice": "Οι έλεγχοι πέρασαν και οι αλλαγές εφαρμόστηκαν. Κλείσε και άνοιξε την εφαρμογή."}
