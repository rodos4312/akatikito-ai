"""Extended «🩺 Γιατρός» checks & safe 1-click repairs (extracted to keep doctor.py < 800 lines).

Covers:
- Core system/runtime checks: uncertain_jobs, api_key_live, daily_budget, checkpoint_repo,
  disk_space, audit_chain, source_syntax (AST syntax verification across app.py & akatikito/)
- v2.5 runtime checks: doc_index (.agent/doc-index.sqlite), staged_attachments (.agent/attachments/),
  desktop_hotkey_tray (global_hotkey + assets/akatikito.ico)
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .audit_log import AuditLog
from . import doctor_checks_v26 as _v26_checks
from .hotkey import parse_hotkey
from .model import PROVIDERS
from .settings import DEFAULT_PATH, Settings, unmask_secret
from .tray import resolve_tray_icon

_ATTACH_OLD_DAYS = 30
_ATTACH_WARN_BYTES = 100 * 1024 * 1024  # 100 MiB
_CORE_PACKAGES = ("httpx", "openai", "snowballstemmer")
_FEATURE_PACKAGES = ("docx", "openpyxl", "pypdf", "PIL")


def check_python_packages(doc, make_item) -> dict[str, Any]:
    req_core = doc.app_root / "requirements.txt"
    req_feat = doc.app_root / "requirements-features.txt"
    missing_core = [m for m in _CORE_PACKAGES if importlib.util.find_spec(m) is None]
    missing_feat = [m for m in _FEATURE_PACKAGES if importlib.util.find_spec(m) is None] if req_feat.is_file() else []
    missing = missing_core + missing_feat
    if not missing:
        return make_item("python_packages", "Πακέτα Python (.venv)", "ok", "Όλα τα απαιτούμενα πακέτα Python είναι εγκατεστημένα.")
    cmd_parts = [f"{sys.executable} -m pip install -r {req_core.name}"]
    if req_feat.is_file():
        cmd_parts.append(f"-r {req_feat.name}")
    return make_item(
        "python_packages",
        "Πακέτα Python (.venv)",
        "error" if missing_core else "warn",
        f"Λείπουν πακέτα Python: {', '.join(missing)}.",
        fixable=req_core.is_file(),
        fix_label="Εγκατάσταση πακέτων",
        fix_preview="Θα τρέξει: " + " ".join(cmd_parts),
        meta={"missing": missing},
    )


def shortcut_paths() -> list[Path]:
    if os.name != "nt":
        return []
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Akatikito.lnk")
    userprofile = os.environ.get("USERPROFILE")
    if not userprofile:
        try:
            userprofile = str(Path.home())
        except RuntimeError:
            userprofile = ""
    if userprofile:
        paths.append(Path(userprofile) / "Desktop" / "Akatikito.lnk")
    return paths


def check_windows_shortcuts(doc, make_item) -> dict[str, Any]:
    if os.name != "nt":
        return make_item("windows_shortcuts", "Συντομεύσεις Desktop / Start Menu", "ok", "Έλεγχος συντομεύσεων Windows (δεν απαιτείται σε αυτό το OS).")
    missing: list[Path] = []
    restricted: list[str] = []
    for p in shortcut_paths():
        try:
            if not p.is_file():
                missing.append(p)
        except OSError:
            restricted.append("Desktop" if "Desktop" in str(p) else "Start Menu")
    if not missing and not restricted:
        return make_item("windows_shortcuts", "Συντομεύσεις Desktop / Start Menu", "ok", "Οι συντομεύσεις σε Desktop και Start Menu υπάρχουν.")
    if not missing and restricted:
        return make_item("windows_shortcuts", "Συντομεύσεις Desktop / Start Menu", "ok", f"Δεν ήταν δυνατός ο έλεγχος πρόσβασης σε: {', '.join(restricted)}.")
    labels = ["Desktop" if "Desktop" in str(p) else "Start Menu" for p in missing]
    return make_item(
        "windows_shortcuts",
        "Συντομεύσεις Desktop / Start Menu",
        "warn",
        f"Λείπουν συντομεύσεις: {', '.join(labels)}.",
        fixable=True,
        fix_label="Επαναδημιουργία",
        fix_preview=f"Θα καλέσει το scripts/create_shortcuts.py για να δημιουργήσει τις συντομεύσεις pythonw.exe με εικονίδιο σε: {', '.join(labels)}.",
        meta={"missing": [str(p) for p in missing]},
    )


def _read_settings_readonly(doc) -> dict[str, Any]:
    spath = doc.data_dir / "settings.json"
    if not spath.is_file():
        return {}
    try:
        data = json.loads(spath.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def check_uncertain_jobs(doc, make_item) -> dict[str, Any]:
    qpath = doc._scheduled_path()
    if not qpath.is_file():
        return make_item("uncertain_jobs", "Προγραμματισμένες εργασίες", "ok", "Δεν υπάρχουν κολλημένες προγραμματισμένες εργασίες.")
    try:
        data = json.loads(qpath.read_text(encoding="utf-8"))
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
    except Exception:
        return make_item("uncertain_jobs", "Προγραμματισμένες εργασίες", "warn", "Δεν διαβάστηκε το scheduled.json.")
    stuck = [j for j in jobs if isinstance(j, dict) and j.get("status") == "uncertain"]
    if not stuck:
        return make_item("uncertain_jobs", "Προγραμματισμένες εργασίες", "ok", "Καμία προγραμματισμένη εργασία δεν είναι κολλημένη σε uncertain.")
    return make_item(
        "uncertain_jobs",
        "Προγραμματισμένες εργασίες",
        "warn",
        f"Βρέθηκαν {len(stuck)} εργασίες σε κατάσταση 'uncertain' μετά από διακοπή.",
        fixable=True,
        fix_label="Ακύρωση uncertain",
        fix_preview=f"Μπορείς να ξανατρέξεις ή να ακυρώσεις τις {len(stuck)} κολλημένες εργασίες.",
        actions=[{"action": "retry", "label": "Ξανατρέξε"}, {"action": "cancel", "label": "Ακύρωσε"}],
        meta={"ids": [j.get("id", "") for j in stuck]},
    )


def check_api_key(doc, make_item, *, run_live: bool = False) -> dict[str, Any]:
    if not run_live:
        return make_item(
            "api_key_live",
            "Έλεγχος API key",
            "ok",
            "Δεν έγινε κλήση δικτύου (πατήστε «Δοκιμή API key» μόνο αν θέλετε ζωντανό έλεγχο /models).",
            meta={"checked": False},
        )
    raw = _read_settings_readonly(doc)
    provider = str(raw.get("provider", "") or "DeepSeek")
    keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
    key = unmask_secret(keys.get(provider, "")) if keys else ""
    base_url = str(raw.get("base_url", "") or "").strip()
    if not base_url:
        prov_info = PROVIDERS.get(provider)
        if isinstance(prov_info, str):
            base_url = prov_info
        elif isinstance(prov_info, dict):
            base_url = str(prov_info.get("base_url", "") or "")
    base_url = base_url.rstrip("/")
    if not base_url:
        return make_item("api_key_live", "Έλεγχος API key", "warn", f"Δεν έχει οριστεί URL για τον πάροχο '{provider}' στις Ρυθμίσεις.")
    if not key and "127.0.0.1" not in base_url and "localhost" not in base_url:
        return make_item("api_key_live", "Έλεγχος API key", "error", f"Δεν έχει αποθηκευτεί API key για τον πάροχο '{provider}'.")
    try:
        import httpx

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        resp = httpx.get(f"{base_url}/models", headers=headers, timeout=6.0)
        if resp.status_code in (401, 403):
            return make_item("api_key_live", "Έλεγχος API key", "error", "Το API key έληξε ή είναι λάθος (HTTP 401/403).", meta={"status_code": resp.status_code})
        if resp.status_code >= 400:
            return make_item("api_key_live", "Έλεγχος API key", "warn", f"Ο πάροχος απάντησε με HTTP {resp.status_code}.", meta={"status_code": resp.status_code})
        return make_item("api_key_live", "Έλεγχος API key", "ok", f"Το API key λειτουργεί κανονικά ({provider}).", meta={"checked": True})
    except Exception as exc:
        return make_item("api_key_live", "Έλεγχος API key", "warn", f"Αδυναμία σύνδεσης στον πάροχο: {str(exc)[:160]}")


def check_daily_budget(doc, make_item) -> dict[str, Any]:
    raw = _read_settings_readonly(doc)
    try:
        budget = max(0.0, float(raw.get("daily_budget", 0) or 0))
    except (TypeError, ValueError):
        budget = 0.0
    if budget > 0:
        return make_item("daily_budget", "Ημερήσιο όριο κόστους", "ok", f"Έχει οριστεί ημερήσιο όριο κόστους: {budget:.2f} €.")
    return make_item(
        "daily_budget",
        "Ημερήσιο όριο κόστους",
        "warn",
        "Δεν έχει οριστεί ημερήσιο όριο κόστους (προτείνεται 1,00 € για ασφάλεια).",
        fixable=True,
        fix_label="Ορισμός 1 €",
        fix_preview="Θα κρατήσει backup του settings.json και θα ορίσει daily_budget = 1.0 €.",
        meta={"suggested_eur": 1.0},
    )


def check_checkpoint_repo(doc, make_item) -> dict[str, Any]:
    repo = doc.project_root / ".agent" / "checkpoints" / "objects.git"
    if not repo.exists():
        return make_item("checkpoint_repo", "Αποθετήριο Checkpoints (Git)", "ok", "Δεν υπάρχει ακόμα checkpoint repo (θα δημιουργηθεί αυτόματα όταν χρειαστεί).")
    git_bin = shutil.which("git")
    if not git_bin:
        return make_item("checkpoint_repo", "Αποθετήριο Checkpoints (Git)", "warn", "Δεν βρέθηκε το git στο σύστημα για έλεγχο των checkpoints.")
    try:
        res = subprocess.run(
            [git_bin, f"--git-dir={repo}", "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0 and res.stdout.strip() == "true":
            return make_item("checkpoint_repo", "Αποθετήριο Checkpoints (Git)", "ok", "Το τοπικό checkpoint repo είναι υγιές.")
    except Exception:
        pass
    return make_item(
        "checkpoint_repo",
        "Αποθετήριο Checkpoints (Git)",
        "error",
        "Το τοπικό checkpoint repo (objects.git) φαίνεται κατεστραμμένο.",
        fixable=True,
        fix_label="Νέο checkpoint repo",
        fix_preview="Θα μετακινήσει το χαλασμένο objects.git σε objects.git.broken και θα αρχικοποιήσει νέο καθαρό checkpoint repo.",
    )


def check_disk_space(doc, make_item) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(doc.project_root)
        free_gb = round(usage.free / (1024**3), 2)
    except OSError:
        return make_item("disk_space", "Ελεύθερος χώρος δίσκου", "warn", "Αδυναμία ανάγνωσης ελεύθερου χώρου δίσκου.")
    if free_gb < 2.0:
        return make_item("disk_space", "Ελεύθερος χώρος δίσκου", "warn", f"Χαμηλός ελεύθερος χώρος στον δίσκο: {free_gb} GB (συνιστώνται τουλάχιστον 2 GB).", meta={"free_gb": free_gb})
    return make_item("disk_space", "Ελεύθερος χώρος δίσκου", "ok", f"Ελεύθερος χώρος δίσκου: {free_gb} GB.", meta={"free_gb": free_gb})


def check_audit_chain(doc, make_item) -> dict[str, Any]:
    audit = AuditLog(doc.project_root)
    if not audit.path.is_file():
        return make_item("audit_chain", "Ακεραιότητα Audit Log", "ok", "Δεν υπάρχει ακόμα αρχείο audit.jsonl για αυτό το project.")
    res = audit.verify_chain()
    if res.get("ok"):
        return make_item("audit_chain", "Ακεραιότητα Audit Log", "ok", f"Η κρυπτογραφική αλυσίδα του audit log είναι ακέραιη ({res.get('entries', 0)} εγγραφές).")
    return make_item(
        "audit_chain",
        "Ακεραιότητα Audit Log",
        "error",
        f"Έσπασε η αλυσίδα του audit log ({res.get('error', 'αλλοίωση')}). Μόνο προειδοποίηση — δεν γίνεται αυτόματη επισκευή για διατήρηση αποδεικτικών.",
        fixable=False,
        meta=res,
    )


def check_source_syntax(doc, make_item) -> dict[str, Any]:
    """Read-only AST syntax check on app.py and akatikito/**/*.py."""
    targets: list[Path] = []
    app_py = doc.app_root / "app.py"
    if app_py.is_file():
        targets.append(app_py)
    pkg = doc.app_root / "akatikito"
    if pkg.is_dir():
        for p in sorted(pkg.rglob("*.py")):
            if "__pycache__" not in p.parts and not p.is_symlink():
                targets.append(p)
    if not targets:
        return make_item("source_syntax", "Σύνταξη κώδικα εφαρμογής (Python)", "ok", "Δεν βρέθηκαν αρχεία Python εφαρμογής προς έλεγχο.")
    broken: list[str] = []
    for p in targets:
        try:
            ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
        except (SyntaxError, UnicodeError, ValueError, OSError):
            broken.append(p.relative_to(doc.app_root).as_posix())
    if not broken:
        return make_item("source_syntax", "Σύνταξη κώδικα εφαρμογής (Python)", "ok", f"Έγκυρη σύνταξη Python σε όλα τα {len(targets)} αρχεία της εφαρμογής.", meta={"checked": len(targets), "broken": []})
    return make_item(
        "source_syntax",
        "Σύνταξη κώδικα εφαρμογής (Python)",
        "error",
        f"Σφάλμα σύνταξης Python σε: {', '.join(broken[:5])}{'…' if len(broken) > 5 else ''}.",
        fixable=False,
        meta={"checked": len(targets), "broken": broken},
    )


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
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")}
            has_schema = {"doc_meta", "doc_fts"} <= tables
            count = int(conn.execute("SELECT COUNT(*) FROM doc_meta").fetchone()[0]) if has_schema else 0
        finally:
            conn.close()
        if ok_integrity and has_schema:
            kb = max(1, round(db.stat().st_size / 1024))
            return make_item("doc_index", "Ευρετήριο αναζήτησης εγγράφων (SQLite)", "ok", f"Το ευρετήριο εγγράφων είναι υγιές ({count} έγγραφα, {kb} KB).", meta={"docs": count, "bytes": db.stat().st_size})
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


def _scan_attachments(doc, days: int = _ATTACH_OLD_DAYS) -> tuple[list[Path], int, int, int]:
    folder = doc.project_root / ".agent" / "attachments"
    if not folder.is_dir():
        return [], 0, 0, 0
    cutoff = time.time() - days * 86400
    old_files: list[Path] = []
    old_bytes = total_files = total_bytes = 0
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
        return make_item("staged_attachments", "Φάκελος συνημμένων (.agent/attachments)", "warn", f"Ο φάκελος συνημμένων έχει φτάσει τα {total_mb} MB ({total_files} πρόσφατα αρχεία <30 ημερών).", fixable=False, meta={"old_count": 0, "total_count": total_files, "total_bytes": total_bytes})
    return make_item("staged_attachments", "Φάκελος συνημμένων (.agent/attachments)", "ok", f"Καθαρός φάκελος συνημμένων ({total_files} αρχεία, {total_mb} MB).", meta={"old_count": 0, "total_count": total_files, "total_bytes": total_bytes})


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
            return make_item("desktop_hotkey_tray", "Συντόμευση πληκτρολογίου & System Tray", "warn", "Το settings.json δεν διαβάζεται (δες πρώτα τον έλεγχο JSON).")
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
        return make_item("desktop_hotkey_tray", "Συντόμευση πληκτρολογίου & System Tray", "warn", f"Η συντόμευση («{raw or 'απενεργοποιημένη'}») είναι έγκυρη, αλλά λείπει το assets/akatikito.ico για το System Tray.", fixable=False, meta={"global_hotkey": raw, "icon_found": False})
    hk_desc = "απενεργοποιημένη" if disabled else raw
    return make_item("desktop_hotkey_tray", "Συντόμευση πληκτρολογίου & System Tray", "ok", f"Συντόμευση «{hk_desc}» έγκυρη" + (" · εικονίδιο Tray έτοιμο." if ico else "."), meta={"global_hotkey": raw, "icon_found": bool(ico)})


def _memory_db_path(doc) -> Path:
    return doc.data_dir / "memory" / "memory.db"


def check_memory_db(doc, make_item) -> dict[str, Any]:
    db = _memory_db_path(doc)
    if not db.is_file():
        return make_item(
            "memory_db",
            "Κεντρική βάση μνήμης (SQLite)",
            "ok",
            "Δεν υπάρχει ακόμα data/memory/memory.db (δημιουργείται αυτόματα στην πρώτη χρήση μνήμης).",
        )
    try:
        uri = f"{db.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            ok_integrity = bool(row and str(row[0]).lower() == "ok")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")}
            has_schema = {"memories", "history"} <= tables
            count = int(conn.execute("SELECT COUNT(*) FROM memories WHERE superseded_by IS NULL").fetchone()[0]) if has_schema else 0
        finally:
            conn.close()
        if ok_integrity and has_schema:
            kb = max(1, round(db.stat().st_size / 1024))
            return make_item("memory_db", "Κεντρική βάση μνήμης (SQLite)", "ok", f"Η βάση μνήμης είναι υγιής ({count} ενεργές μνήμες, {kb} KB).", meta={"memories": count, "bytes": db.stat().st_size})
        reason = "ελλιπές σχήμα πινάκων" if ok_integrity else "αλλοίωση βάσης SQLite"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    return make_item(
        "memory_db",
        "Κεντρική βάση μνήμης (SQLite)",
        "error",
        f"Πρόβλημα στο data/memory/memory.db ({reason}).",
        fixable=True,
        fix_label="Επαναφορά μνήμης",
        fix_preview="Θα κρατήσει αντίγραφο του χαλασμένου memory.db ως .broken και θα αρχικοποιήσει καθαρή βάση (τα MEMORY.md/profile.json δεν αγγίζονται).",
    )


def _fix_doc_index(doc) -> tuple[list[str], str]:
    from .doc_index import DocIndex

    db = _doc_index_path(doc)
    touched = []
    if db.is_file():
        broken = db.with_name(f"doc-index.broken.{time.strftime('%Y%m%d-%H%M%S')}.sqlite")
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


def _fix_memory_db(doc) -> tuple[list[str], str]:
    from .memory_store import MemoryStore

    db = _memory_db_path(doc)
    touched = []
    if db.is_file():
        broken = db.with_name(f"memory.broken.{time.strftime('%Y%m%d-%H%M%S')}.db")
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
    db.parent.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(db)
    store.close()
    touched.append(str(db))
    return touched, "Επαναφέρθηκε καθαρή βάση μνήμης (data/memory/memory.db)· οι παλιές σημειώσεις (MEMORY.md/profile.json) συγχρονίζονται αυτόματα."


def run_extended_checks(doc, make_item, *, check_api: bool = False) -> list[dict[str, Any]]:
    return [
        check_python_packages(doc, make_item),
        check_windows_shortcuts(doc, make_item),
        check_uncertain_jobs(doc, make_item),
        check_api_key(doc, make_item, run_live=check_api),
        check_daily_budget(doc, make_item),
        check_checkpoint_repo(doc, make_item),
        check_disk_space(doc, make_item),
        check_audit_chain(doc, make_item),
        check_source_syntax(doc, make_item),
        check_doc_index(doc, make_item),
        check_memory_db(doc, make_item),
        check_staged_attachments(doc, make_item),
        check_desktop_hotkey_tray(doc, make_item),
        _v26_checks.check_skills_security(doc, make_item),
        _v26_checks.check_context_archive(doc, make_item),
        _v26_checks.check_ui_assets(doc, make_item),
    ]


def _fix_setting_field(doc, backup_fn, attr: str, value, message: str) -> tuple[list[str], str]:
    spath = doc.data_dir / "settings.json"
    backup_fn(spath, "bak")
    if doc.state is not None and getattr(doc.state, "settings", None) is not None and doc.data_dir == DEFAULT_PATH.parent.resolve():
        setattr(doc.state.settings, attr, value)
        doc.state.settings.save()
    else:
        st = Settings.load(spath)
        setattr(st, attr, value)
        st.save(spath)
    return [str(spath)], message


def _fix_uncertain_jobs(doc, action: str, backup_fn):
    from .scheduler import ScheduledQueue, queue_for

    act = action if action in ("retry", "cancel") else "cancel"
    qpath = doc._scheduled_path()
    if not qpath.is_file():
        return {"ok": False, "error": "Δεν βρέθηκε το scheduled.json."}
    backup_fn(qpath, "bak")
    queue = queue_for(doc.state) if (doc.state is not None and doc.data_dir == DEFAULT_PATH.parent.resolve()) else ScheduledQueue(qpath)
    stuck_ids = [j["id"] for j in queue.view() if j.get("status") == "uncertain"]
    acted = 0
    for jid in stuck_ids:
        try:
            queue.action(jid, act)
            acted += 1
        except (OSError, ValueError, KeyError):
            continue
    return [str(qpath)], f"Εφαρμόστηκε '{act}' σε {acted} uncertain εργασίες."


def _fix_checkpoint_repo(doc):
    repo = doc.project_root / ".agent" / "checkpoints" / "objects.git"
    git_bin = shutil.which("git")
    if not git_bin:
        return {"ok": False, "error": "Δεν βρέθηκε το git στο σύστημα."}
    touched: list[str] = []
    if repo.exists():
        broken = repo.with_name(f"objects.git.broken.{time.strftime('%Y%m%d-%H%M%S')}")
        repo.rename(broken)
        touched.append(str(broken))
    repo.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([git_bin, "init", "--bare", str(repo)], capture_output=True, check=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    touched.append(str(repo))
    return touched, "Δημιουργήθηκε νέο καθαρό checkpoint repo."


def _fix_python_packages(doc):
    req_core = doc.app_root / "requirements.txt"
    req_feat = doc.app_root / "requirements-features.txt"
    if not req_core.is_file():
        return {"ok": False, "error": "Δεν βρέθηκε το requirements.txt."}
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req_core)]
    if req_feat.is_file():
        cmd.extend(["-r", str(req_feat)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "pip install failed")[:400]}
    return [], "Εγκαταστάθηκαν τα απαιτούμενα πακέτα Python (requirements + features)."


def _fix_windows_shortcuts(doc):
    if os.name != "nt":
        return {"ok": False, "error": "Μόνο για Windows."}
    script = doc.app_root / "scripts" / "create_shortcuts.py"
    if not script.is_file():
        return {"ok": False, "error": "Δεν βρέθηκε το scripts/create_shortcuts.py."}
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "create_shortcuts failed")[:400]}
    touched = []
    for p in shortcut_paths():
        try:
            if p.is_file():
                touched.append(str(p))
        except OSError:
            continue
    return touched, "Επαναδημιουργήθηκαν οι συντομεύσεις Windows (pythonw.exe + εικονίδιο)."


def _fix_staged_attachments(doc) -> tuple[list[str], str]:
    old_files, old_bytes, _, _ = _scan_attachments(doc)
    touched = []
    for p in old_files:
        try:
            p.unlink(missing_ok=True)
            touched.append(str(p))
        except OSError:
            continue
    return touched, f"Καθαρίστηκαν {len(touched)} παλιά συνημμένα >30 ημερών ({round(old_bytes / (1024 * 1024), 2)} MB)."


_EXTENDED_FIX_HANDLERS = {
    "daily_budget": lambda d, _a, b: _fix_setting_field(d, b, "daily_budget", 1.0, "Ορίστηκε ημερήσιο όριο κόστους 1,00 €."),
    "uncertain_jobs": lambda d, a, b: _fix_uncertain_jobs(d, a, b),
    "checkpoint_repo": lambda d, _a, _b: _fix_checkpoint_repo(d),
    "python_packages": lambda d, _a, _b: _fix_python_packages(d),
    "windows_shortcuts": lambda d, _a, _b: _fix_windows_shortcuts(d),
    "doc_index": lambda d, _a, _b: _fix_doc_index(d),
    "memory_db": lambda d, _a, _b: _fix_memory_db(d),
    "staged_attachments": lambda d, _a, _b: _fix_staged_attachments(d),
    "context_archive": lambda d, _a, _b: _v26_checks.fix_context_archive(d),
    "desktop_hotkey_tray": lambda d, _a, b: _fix_setting_field(d, b, "global_hotkey", "Alt+Space", "Επαναφέρθηκε η συντόμευση global_hotkey σε «Alt+Space»."),
}


def apply_extended_fix(doc, check_id: str, action: str, backup_fn) -> dict[str, Any] | tuple[list[str], str] | None:
    handler = _EXTENDED_FIX_HANDLERS.get(check_id)
    return handler(doc, action, backup_fn) if handler is not None else None
