"""Bounded local JSON storage; atomic writes never follow links or junctions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import uuid

from .process_lock import FileLock
LOCK = FileLock(Path(os.getenv('AKATIKITO_MEMORY_DIR',str(Path(__file__).resolve().parents[1]/'data'/'memory')))/'.storage.lock')


def checked(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Η αποθήκευση δεν επιτρέπεται μέσω συνδέσμου ή junction.")
    return path


def load_json(path, default, limit=4_000_000):
    path = checked(Path(path))
    if not path.exists():
        return default()
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Το αρχείο μνήμης ξεπερνά το όριο ασφαλούς ανάγνωσης.")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Το αρχείο μνήμης είναι μη έγκυρο· δεν αντικαταστάθηκε.") from exc


def digest(path):
    path = checked(Path(path))
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def atomic_bytes(path, raw, expected=False):
    path = checked(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    checked(path)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        checked(path)
        if expected is not False and digest(path) != expected:
            raise ValueError("Το αρχείο άλλαξε μετά την προεπισκόπηση· χρειάζεται νέα πρόταση.")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_json(path, data):
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
