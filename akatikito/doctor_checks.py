"""Extended «🩺 Γιατρός» checks & safe 1-click repairs for v2.5 runtime features:
- doc_index: SQLite FTS5 hybrid document index (.agent/doc-index.sqlite)
- staged_attachments: Staged Drag & Drop / screenshot attachments (.agent/attachments/)
- desktop_hotkey_tray: Global hotkey config validity & Windows tray icon asset
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .hotkey import parse_hotkey
from .settings import DEFAULT_PATH, Settings
from .tray import resolve_tray_icon

_ATTACH_OLD_DAYS = 30
_ATTACH_WARN_BYTES = 100 * 1024 * 1024  # 100 MiB


def _doc_index_path(doc) -> Path:
    return doc.project_root / ".agent" / "doc-index.sqlite"


def check_doc_index(doc, make_item) -> dict[str, Any]:
    db = _doc_index_path(doc)
    if not db.is_file():
        return make_item(
            "doc_index",
            "Ευρετήριο αναζήτησης εγγράφων (SQLite)",
            "ok",
            "Δεν υπάρχει ακόμα ευρετήριο εγγράφων (δημιουργείται αυτόματα στην πρώτη αναζήτηση).",
        )
    try:
        uri = f"{db.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            ok_integrity = bool(row and str(row[0]).lower() == "ok")
            tables = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")
            }
            has_schema = {"doc_meta", "doc_fts"} <= tables
            count = int(conn.execute("SELECT COUNT(*) FROM doc_meta").fetchone()[0]) if has_schema else 0
        finally:
            conn.close()
        if ok_integrity and has_schema:
            kb = max(1, round(db.stat().st_size / 1024))
            return make_item(
                "doc_index",
                "Ευρετήριο αναζήτησης εγγράφων (SQLite)",
                "ok",
                f"Το ευρετήριο εγγράφων είναι υγιές ({count} έγγραφα, {kb} KB).",
                meta={"docs": count, "bytes": db.stat().st_size},
            )
        reason = "ελλιπές σχήμα πινάκων" if ok_integrity else "αλλοίωση βάσης SQLite"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    return make_item(
        "doc_index",
        "Ευρετήριο αναζήτησης εγγράφων (SQLite)",
        "error",
        f"Πρόβλημα στο .agent/doc-index.sqlite ({reason}).",
        fixable=True,
        fix_label="Επαναφορά ευρετηρίου",
        fix_preview="Θα κρατήσει αντίγραφο του χαλασμένου .agent/doc-index.sqlite ως .broken και θα δημιουργήσει καθαρό ευρετήριο.",
    )


def fix_doc_index(doc) -> tuple[list[str], str]:
    from .doc_index import DocIndex

    db = _doc_index_path(doc)
    touched: list[str] = []
    ts = time.strftime("%Y%m%d-%H%M%S")
    if db.is_file():
        broken = db.with_name(f"doc-index.broken.{ts}.sqlite")
        db.rename(broken)
        touched.append(str(broken))
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.is_file():
            try:
                side.unlink(missing_ok=True)
                touched.append(str(side))
            except OSError:
                pass
    conn = DocIndex(doc.project_root)._connect()
    conn.close()
    touched.append(str(db))
    return touched, "Επαναφέρθηκε καθαρό ευρετήριο αναζήτησης εγγράφων (.agent/doc-index.sqlite)."


def _scan_attachments(doc, days: int = _ATTACH_OLD_DAYS) -> tuple[list[Path], int, int, int]:
    folder = doc.project_root / ".agent" / "attachments"
    if not folder.is_dir():
        return [], 0, 0, 0
    cutoff = time.time() - days * 86400
    old_files: list[Path] = []
    old_bytes = 0
    total_files = 0
    total_bytes = 0
    try:
        for entry in folder.iterdir():
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                st = entry.stat()
            except OSError:
                continue
            total_files += 1
            total_bytes += st.st_size
            if st.st_mtime < cutoff:
                old_files.append(entry)
                old_bytes += st.st_size
    except OSError:
        pass
    return old_files, old_bytes, total_files, total_bytes


def check_staged_attachments(doc, make_item) -> dict[str, Any]:
    old_files, old_bytes, total_files, total_bytes = _scan_attachments(doc)
    total_mb = round(total_bytes / (1024 * 1024), 2)
    if old_files:
        old_mb = round(old_bytes / (1024 * 1024), 2)
        return make_item(
            "staged_attachments",
            "Φάκελος συνημμένων (.agent/attachments)",
            "warn",
            f"Βρέθηκαν {len(old_files)} παλιά συνημμένα >30 ημερών ({old_mb} MB από σύνολο {total_files} αρχείων / {total_mb} MB).",
            fixable=True,
            fix_label="Καθάρισμα παλιών",
            fix_preview=f"Θα διαγράψει ΜΟΝΟ τα {len(old_files)} συνημμένα >30 ημερών ({old_mb} MB) από το .agent/attachments/ (τα πρόσφατα παραμένουν ανέπαφα).",
            meta={"old_count": len(old_files), "old_bytes": old_bytes, "total_count": total_files, "total_bytes": total_bytes},
        )
    if total_bytes >= _ATTACH_WARN_BYTES:
        return make_item(
            "staged_attachments",
            "Φάκελος συνημμένων (.agent/attachments)",
            "warn",
            f"Ο φάκελος συνημμένων έχει φτάσει τα {total_mb} MB ({total_files} πρόσφατα αρχεία <30 ημερών).",
            fixable=False,
            meta={"old_count": 0, "total_count": total_files, "total_bytes": total_bytes},
        )
    return make_item(
        "staged_attachments",
        "Φάκελος συνημμένων (.agent/attachments)",
        "ok",
        f"Καθαρός φάκελος συνημμένων ({total_files} αρχεία, {total_mb} MB).",
        meta={"old_count": 0, "total_count": total_files, "total_bytes": total_bytes},
    )


def fix_staged_attachments(doc) -> tuple[list[str], str]:
    old_files, old_bytes, _, _ = _scan_attachments(doc)
    touched: list[str] = []
    for p in old_files:
        try:
            p.unlink(missing_ok=True)
            touched.append(str(p))
        except OSError:
            pass
    old_mb = round(old_bytes / (1024 * 1024), 2)
    return touched, f"Καθαρίστηκαν {len(touched)} παλιά συνημμένα >30 ημερών ({old_mb} MB)."


def check_desktop_hotkey_tray(doc, make_item) -> dict[str, Any]:
    spath = doc.data_dir / "settings.json"
    raw = "Alt+Space"
    if spath.is_file():
        try:
            data = json.loads(spath.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("settings.json is not an object")
            raw = str(data.get("global_hotkey", "Alt+Space") or "").strip()
        except Exception:
            return make_item(
                "desktop_hotkey_tray",
                "Συντόμευση πληκτρολογίου & System Tray",
                "warn",
                "Το settings.json δεν διαβάζεται (δες πρώτα τον έλεγχο JSON).",
            )
    disabled = (not raw) or raw.casefold() in ("off", "none", "disabled")
    valid_hk = disabled or (parse_hotkey(raw) is not None)
    ico = resolve_tray_icon(doc.app_root)

    if not valid_hk:
        return make_item(
            "desktop_hotkey_tray",
            "Συντόμευση πληκτρολογίου & System Tray",
            "warn",
            f"Άκυρη ρύθμιση global_hotkey («{raw}») — η συντόμευση παραθύρου δεν μπορεί να ενεργοποιηθεί.",
            fixable=True,
            fix_label="Επαναφορά Alt+Space",
            fix_preview="Θα κρατήσει backup του settings.json και θα επαναφέρει το global_hotkey στην προεπιλογή «Alt+Space».",
            meta={"global_hotkey": raw, "icon_found": bool(ico)},
        )
    if os.name == "nt" and not ico:
        return make_item(
            "desktop_hotkey_tray",
            "Συντόμευση πληκτρολογίου & System Tray",
            "warn",
            f"Η συντόμευση («{raw or 'απενεργοποιημένη'}») είναι έγκυρη, αλλά λείπει το assets/akatikito.ico για το System Tray.",
            fixable=False,
            meta={"global_hotkey": raw, "icon_found": False},
        )
    hk_desc = "απενεργοποιημένη" if disabled else raw
    return make_item(
        "desktop_hotkey_tray",
        "Συντόμευση πληκτρολογίου & System Tray",
        "ok",
        f"Συντόμευση «{hk_desc}» έγκυρη" + (" · εικονίδιο Tray έτοιμο." if ico else "."),
        meta={"global_hotkey": raw, "icon_found": bool(ico)},
    )


def fix_desktop_hotkey_tray(doc, backup_fn) -> tuple[list[str], str]:
    spath = doc.data_dir / "settings.json"
    backup_fn(spath, "bak")
    if doc.state is not None and getattr(doc.state, "settings", None) is not None and doc.data_dir == DEFAULT_PATH.parent.resolve():
        doc.state.settings.global_hotkey = "Alt+Space"
        doc.state.settings.save()
    else:
        st = Settings.load(spath)
        st.global_hotkey = "Alt+Space"
        st.save(spath)
    return [str(spath)], "Επαναφέρθηκε η συντόμευση global_hotkey σε «Alt+Space»."


def run_extended_checks(doc, make_item) -> list[dict[str, Any]]:
    return [
        check_doc_index(doc, make_item),
        check_staged_attachments(doc, make_item),
        check_desktop_hotkey_tray(doc, make_item),
    ]


def apply_extended_fix(doc, check_id: str, backup_fn) -> tuple[list[str], str] | None:
    if check_id == "doc_index":
        return fix_doc_index(doc)
    if check_id == "staged_attachments":
        return fix_staged_attachments(doc)
    if check_id == "desktop_hotkey_tray":
        return fix_desktop_hotkey_tray(doc, backup_fn)
    return None
