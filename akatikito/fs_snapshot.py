"""Bounded no-link snapshots and native copies, including NTFS named data streams."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

from .local_store import checked

MAX_ENTRIES = 5000
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_SECONDS = 60


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class Budget:
    def __init__(self, stopped=lambda: False):
        self.stopped = stopped
        self.deadline = time.monotonic() + MAX_SECONDS

    def check(self):
        if self.stopped():
            raise ValueError("Η οργάνωση αρχείων ακυρώθηκε.")
        if time.monotonic() > self.deadline:
            raise ValueError("Ο έλεγχος αρχείων ξεπέρασε το χρονικό όριο. Χώρισε την εργασία σε μικρότερα σύνολα.")


def exact_exists(path):
    path = Path(path)
    if os.name != "nt":
        return path.exists()
    # Case-only renames need the actual spelling, not Windows case-insensitive exists().
    if not path.exists():
        return False
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if part not in os.listdir(current):
            return False
        current = current / part
    return True


def streams(path):
    if os.name != "nt":
        return []
    from ctypes import wintypes as w

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", w.WCHAR * 296)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first = kernel.FindFirstStreamW
    first.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    first.restype = w.HANDLE
    next_ = kernel.FindNextStreamW
    next_.argtypes = [w.HANDLE, ctypes.c_void_p]
    next_.restype = w.BOOL
    close = kernel.FindClose
    close.argtypes = [w.HANDLE]
    close.restype = w.BOOL
    data = StreamData()
    handle = first(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (38, 87):
            return []  # EOF, or a filesystem without streams (Microsoft fileapi contract).
        raise ctypes.WinError(error)
    result = []
    try:
        while True:
            name = data.name
            if name != "::$DATA":
                if not name.startswith(":") or not name.endswith(":$DATA") or any(c in name for c in ("/", "\\", "\0")):
                    raise ValueError("Μη έγκυρη ονομασία stream.")
                result.append((name, int(data.size)))
            if not next_(handle, ctypes.byref(data)):
                error = ctypes.get_last_error()
                if error != 38:
                    raise ctypes.WinError(error)
                break
    finally:
        close(handle)
    if len(result) > 64:
        raise ValueError("Πάρα πολλά πρόσθετα streams στο αρχείο.")
    return sorted(result)


def content(entries):
    return [{k: v for k, v in row.items() if k in ("path", "kind", "size", "sha256", "streams")} for row in entries]


def snapshot(path, budget=None):
    budget = budget or Budget()
    path = checked(Path(path))
    budget.check()
    if not exact_exists(path):
        return None
    entries = []
    total = 0
    pending = [(path, "")]

    def hash_file(target, size):
        if size > MAX_FILE:
            raise ValueError("Αρχείο/stream πάνω από το όριο 512 MiB.")
        h = hashlib.sha256()
        count = 0
        with open(target, "rb") as stream:
            while True:
                budget.check()
                raw = stream.read(1024 * 1024)
                if not raw:
                    break
                count += len(raw)
                if count > MAX_FILE:
                    raise ValueError("Το αρχείο μεγάλωσε κατά τον έλεγχο.")
                h.update(raw)
        if count != size:
            raise ValueError("Το αρχείο άλλαξε κατά τον έλεγχο.")
        return h.hexdigest()

    while pending:
        budget.check()
        current, rel = pending.pop()
        checked(current)
        before = current.lstat()
        if getattr(before, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(before.st_mode):
            raise ValueError("Δεν επιτρέπονται σύνδεσμοι, junctions ή reparse points.")
        kind = "dir" if stat.S_ISDIR(before.st_mode) else "file" if stat.S_ISREG(before.st_mode) else ""
        if not kind:
            raise ValueError("Υποστηρίζονται μόνο κανονικά αρχεία και φάκελοι.")
        row = {
            "path": rel,
            "kind": kind,
            "identity": [before.st_dev, before.st_ino],
            "mtime_ns": before.st_mtime_ns,
            "mode": stat.S_IMODE(before.st_mode),
            "size": 0 if kind == "dir" else before.st_size,
            "streams": [],
        }
        named = streams(current)
        if kind == "file":
            row["sha256"] = hash_file(current, before.st_size)
            total += before.st_size
        for name, size in named:
            row["streams"].append({"name": name, "size": size, "sha256": hash_file(str(current) + name, size)})
            total += size
        if streams(current) != named:
            raise ValueError("Τα streams άλλαξαν κατά τον έλεγχο.")
        after = current.lstat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
        ):
            raise ValueError("Το αρχείο ή ο φάκελος άλλαξε κατά τον έλεγχο.")
        entries.append(row)
        if len(entries) > MAX_ENTRIES or total > MAX_TOTAL:
            raise ValueError("Η εργασία ξεπερνά το όριο 5000 εγγραφών / 2 GiB.")
        if kind == "dir":
            with os.scandir(current) as children:
                for child in children:
                    pending.append((Path(child.path), rel + "/" + child.name if rel else child.name))
                    if len(entries) + len(pending) > MAX_ENTRIES:
                        raise ValueError("Πάρα πολλά αρχεία/φάκελοι.")
    entries.sort(key=lambda x: x["path"])
    return {
        "entries": entries,
        "bytes": total,
        "files": sum(r["kind"] == "file" for r in entries),
        "identity": entries[0]["identity"],
        "content": digest(content(entries)),
        "version": digest(entries),
    }


def copy_file(source, target, budget):
    budget.check()
    checked(source)
    checked(target)
    if os.name == "nt":
        from ctypes import wintypes as w

        callback_type = ctypes.WINFUNCTYPE(
            w.DWORD,
            ctypes.c_longlong,
            ctypes.c_longlong,
            ctypes.c_longlong,
            ctypes.c_longlong,
            w.DWORD,
            w.DWORD,
            w.HANDLE,
            w.HANDLE,
            ctypes.c_void_p,
        )

        def progress(*args):
            try:
                budget.check()
                return 0
            except ValueError:
                return 1  # PROGRESS_CANCEL: CopyFileEx removes its own partial output.

        callback = callback_type(progress)
        cancel = w.BOOL(False)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        copy = kernel.CopyFileExW
        copy.argtypes = [w.LPCWSTR, w.LPCWSTR, callback_type, ctypes.c_void_p, ctypes.POINTER(w.BOOL), w.DWORD]
        copy.restype = w.BOOL
        if not copy(str(source), str(target), callback, None, ctypes.byref(cancel), 1):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        with open(source, "rb") as src, open(target, "xb") as dst:
            while True:
                budget.check()
                raw = src.read(1024 * 1024)
                if not raw:
                    break
                dst.write(raw)
        shutil.copystat(source, target, follow_symlinks=False)


def copy_tree(source, target, expected, budget):
    source = checked(source)
    target = checked(target)
    if target.exists():
        raise FileExistsError("Το αντίγραφο προορισμού υπάρχει ήδη.")
    for row in sorted(expected["entries"], key=lambda r: (r["path"].count("/"), r["kind"] != "dir", r["path"])):
        budget.check()
        src = source / row["path"] if row["path"] else source
        dst = target / row["path"] if row["path"] else target
        checked(src)
        checked(dst)
        if row["kind"] == "dir":
            dst.mkdir()
        else:
            copy_file(src, dst, budget)
    # Directory named streams are data too; preserve them explicitly.
    for row in reversed(expected["entries"]):
        if row["kind"] != "dir":
            continue
        src = source / row["path"] if row["path"] else source
        dst = target / row["path"] if row["path"] else target
        for named in row["streams"]:
            with open(str(src) + named["name"], "rb") as inp, open(str(dst) + named["name"], "xb") as out:
                while True:
                    budget.check()
                    raw = inp.read(1024 * 1024)
                    if not raw:
                        break
                    out.write(raw)
        shutil.copystat(src, dst, follow_symlinks=False)
    actual = snapshot(target, budget)
    if actual["content"] != expected["content"]:
        raise ValueError("Το αντίγραφο δεν επαληθεύτηκε. Δεν θα μετακινηθεί η πηγή.")
