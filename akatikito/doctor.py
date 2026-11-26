"""«🩺 Γιατρός» — Human-only read-only diagnostics & 1-click safe repair for Akatikito.

Safety invariants:
1. ``diagnose()`` is strictly read-only and offline by default (no mutations, no external
   network calls unless ``check_api=True`` is explicitly requested by the human).
2. Every ``apply_fix()`` targets ONLY Akatikito's own app/runtime files (never user code,
   never Safe Trash in ``.agent_backups/organizer/``, never secrets/API keys, never memory),
   backs up before mutating, and records an entry in the project's hash-chained ``AuditLog``.
3. ``audit_chain`` is NEVER auto-repaired (evidence must remain intact).
4. Never exposed as an LLM tool — strictly human UI + ``python -m akatikito.doctor`` / ``Doctor.cmd``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .audit_log import AuditLog
from . import doctor_checks as _ext_checks
from .mobile_bridge import DEFAULT_MOBILE_PORT, SESSION_TTL_SECONDS
from .model import PROVIDERS
from .process_lock import _lock_dir
from .settings import DEFAULT_PATH, Settings, unmask_secret

BADGES = {
    "ok": "✅ Εντάξει",
    "warn": "⚠️ Προσοχή",
    "error": "❌ Χρειάζεται επισκευή",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Exact pattern produced by mobile_bridge.save_upload_stream: .{name}.{8 hex}.part
_MOBILE_PART_RE = re.compile(r"^\..+\.[0-9a-f]{8}\.part$", re.IGNORECASE)
_SKIP_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
_CORE_PACKAGES = ("httpx", "openai")
_FEATURE_PACKAGES = ("docx", "openpyxl", "pypdf", "PIL")
_STALE_TEMP_SECONDS = 3600  # 1 hour: never touch active uploads or in-flight writes


def _item(
    check_id: str,
    title: str,
    status: str,
    details: str,
    *,
    fixable: bool = False,
    fix_label: str = "Επισκευή",
    fix_preview: str = "",
    actions: list[dict[str, str]] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = status if status in BADGES else "warn"
    return {
        "id": check_id,
        "title": title,
        "status": st,
        "badge": BADGES[st],
        "details": details,
        "fixable": bool(fixable),
        "fix_label": fix_label if fixable else "",
        "fix_preview": fix_preview if fixable else "",
        "actions": list(actions or []),
        "meta": dict(meta or {}),
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == 259  # STILL_ACTIVE
                return True
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _file_lock_is_held(lock_path: Path) -> bool:
    """Non-blocking probe to test whether a FileLock file is currently held by another process."""
    try:
        with lock_path.open("r+b") as stream:
            if lock_path.stat().st_size == 0:
                return False
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                return False
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                return True
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            return False
    except OSError:
        return True  # Conservative: if we cannot probe it, assume it is in use


def _backup_file(path: Path, suffix: str = "bak") -> Path | None:
    if not path.is_file():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.stem}.{suffix}.{ts}{path.suffix}")
    shutil.copy2(path, dest)
    return dest


def _find_latest_valid_json_backup(target: Path) -> Path | None:
    parent = target.parent
    if not parent.is_dir():
        return None
    candidates = sorted(
        (
            p for p in parent.glob(f"{target.stem}.bak*{target.suffix}")
            if p.is_file() and ".broken." not in p.name
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for cand in candidates:
        try:
            json.loads(cand.read_text(encoding="utf-8"))
            return cand
        except Exception:
            continue
    return None


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def _is_akatikito_bridge_on_port(port: int) -> bool:
    """Check if the listener on localhost:<port> is Akatikito's own MobileBridge."""
    try:
        import httpx
        resp = httpx.get(f"http://127.0.0.1:{port}/api/mobile/ping", timeout=0.5)
        if resp.status_code == 200:
            body = resp.json()
            return bool(isinstance(body, dict) and body.get("app") == "Akatikito" and body.get("bridge"))
    except Exception:
        pass
    return False


def _suggest_free_port(start: int = DEFAULT_MOBILE_PORT + 1) -> int:
    for p in range(start, start + 30):
        if not _is_port_in_use(p):
            return p
    return 0


class Doctor:
    """Read-only diagnostics & safe 1-click repairs for Akatikito app state."""

    def __init__(
        self,
        *,
        app_root: str | Path | None = None,
        data_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        state: Any = None,
        lock_dir: str | Path | None = None,
    ) -> None:
        self.app_root = Path(app_root).resolve() if app_root else Path(__file__).resolve().parents[1]
        self.data_dir = Path(data_dir).resolve() if data_dir else DEFAULT_PATH.parent.resolve()
        if project_root:
            self.project_root = Path(project_root).resolve()
        elif state is not None and hasattr(state, "_files_root"):
            self.project_root = Path(state._files_root()).resolve()
        else:
            self.project_root = self.app_root
        self.state = state
        self._custom_lock_dir = Path(lock_dir).resolve() if lock_dir else None

    # ------------------------------------------------------------------ paths

    def _json_targets(self) -> list[tuple[str, Path, Any]]:
        return [
            ("settings.json", self.data_dir / "settings.json", {}),
            ("mcp.json", self.data_dir / "mcp.json", {}),
            ("mobile_bridge.json", self.data_dir / "mobile_bridge.json", {"auto_start": False, "sessions": {}}),
            ("usage.json", self.data_dir / "usage.json", {"days": {}, "sessions": {}}),
            ("data/agenda.json", self.data_dir / "agenda.json", []),
            (".agent/agenda.json", self.project_root / ".agent" / "agenda.json", []),
        ]

    def _scheduled_path(self) -> Path:
        return self.data_dir / "scheduled.json"

    def _resolve_lock_dir(self) -> Path | None:
        if self._custom_lock_dir is not None:
            return self._custom_lock_dir
        try:
            return _lock_dir()
        except Exception:
            return None

    # ------------------------------------------------------------------ checks

    def _check_json_integrity(self) -> dict[str, Any]:
        broken: list[str] = []
        for label, path, _default in self._json_targets():
            if not path.is_file():
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                broken.append(label)
        if not broken:
            return _item("json_integrity", "Αρχεία ρυθμίσεων JSON", "ok", "Όλα τα αρχεία ρυθμίσεων (settings, mcp, mobile_bridge, agenda) είναι έγκυρα.")
        return _item(
            "json_integrity",
            "Αρχεία ρυθμίσεων JSON",
            "error",
            f"Βρέθηκαν χαλασμένα JSON: {', '.join(broken)}.",
            fixable=True,
            fix_label="Επαναφορά JSON",
            fix_preview=f"Θα φυλάξει τα χαλασμένα ({', '.join(broken)}) ως .broken.json και θα τα επαναφέρει από το τελευταίο έγκυρο backup (ή καθαρή προεπιλογή).",
            meta={"broken": broken},
        )

    def _check_mobile_sessions(self) -> dict[str, Any]:
        path = self.data_dir / "mobile_bridge.json"
        if not path.is_file():
            return _item("mobile_sessions", "Συνεδρίες κινητού (Mobile Bridge)", "ok", "Δεν υπάρχουν αποθηκευμένες συνεδρίες κινητού.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sessions = data.get("sessions", {}) if isinstance(data, dict) else {}
        except Exception:
            return _item("mobile_sessions", "Συνεδρίες κινητού (Mobile Bridge)", "warn", "Το mobile_bridge.json δεν διαβάζεται (δες τον έλεγχο JSON).")
        if not isinstance(sessions, dict):
            return _item(
                "mobile_sessions",
                "Συνεδρίες κινητού (Mobile Bridge)",
                "error",
                "Η λίστα sessions στο mobile_bridge.json έχει λάθος μορφή.",
                fixable=True,
                fix_label="Καθάρισμα",
                fix_preview="Θα κρατήσει backup του mobile_bridge.json και θα καθαρίσει τις άκυρες συνεδρίες.",
            )
        now = time.time()
        bad = []
        for k, created in sessions.items():
            key_s = str(k or "").strip().lower()
            try:
                ts = float(created)
            except (TypeError, ValueError):
                bad.append(key_s)
                continue
            if not _SHA256_RE.match(key_s) or (now - ts) >= SESSION_TTL_SECONDS or ts > now + 86400:
                bad.append(key_s)
        if not bad:
            return _item("mobile_sessions", "Συνεδρίες κινητού (Mobile Bridge)", "ok", f"Όλες οι συνεδρίες κινητού ({len(sessions)}) είναι έγκυρες.")
        return _item(
            "mobile_sessions",
            "Συνεδρίες κινητού (Mobile Bridge)",
            "warn",
            f"Βρέθηκαν {len(bad)} παλιές ή άκυρες συνεδρίες στο mobile_bridge.json.",
            fixable=True,
            fix_label="Καθάρισμα",
            fix_preview=f"Θα κρατήσει backup του mobile_bridge.json και θα αφαιρέσει {len(bad)} ληγμένες/άκυρες συνεδρίες.",
            meta={"bad_count": len(bad)},
        )

    def _check_mobile_port(self) -> dict[str, Any]:
        own_port = 0
        if self.state is not None and getattr(self.state, "mobile_bridge", None):
            mb = self.state.mobile_bridge
            if getattr(mb, "active", False):
                own_port = int(getattr(mb, "port", 0) or 0)
        if own_port == DEFAULT_MOBILE_PORT:
            return _item("mobile_port", f"Θύρα Mobile Bridge ({DEFAULT_MOBILE_PORT})", "ok", f"Η θύρα {DEFAULT_MOBILE_PORT} χρησιμοποιείται κανονικά από το ίδιο το Akatikito.")
        if _is_port_in_use(DEFAULT_MOBILE_PORT):
            if _is_akatikito_bridge_on_port(DEFAULT_MOBILE_PORT):
                return _item("mobile_port", f"Θύρα Mobile Bridge ({DEFAULT_MOBILE_PORT})", "ok", f"Η θύρα {DEFAULT_MOBILE_PORT} χρησιμοποιείται ήδη από ανοιχτό Akatikito.")
            alt = _suggest_free_port(DEFAULT_MOBILE_PORT + 1)
            msg = f"Η θύρα {DEFAULT_MOBILE_PORT} είναι πιασμένη από άλλο πρόγραμμα."
            if alt:
                msg += f" Προτεινόμενη ελεύθερη θύρα: {alt}."
            return _item("mobile_port", f"Θύρα Mobile Bridge ({DEFAULT_MOBILE_PORT})", "warn", msg, meta={"suggested_port": alt})
        return _item("mobile_port", f"Θύρα Mobile Bridge ({DEFAULT_MOBILE_PORT})", "ok", f"Η θύρα {DEFAULT_MOBILE_PORT} είναι ελεύθερη.")

    def _find_orphan_locks(self) -> list[Path]:
        orphans: list[Path] = []
        now = time.time()
        # 1. PID-based lock files in data/ and .agent/
        for r in (self.data_dir, self.project_root / ".agent"):
            if not r.is_dir():
                continue
            for lock_file in r.glob("*.lock"):
                if not lock_file.is_file():
                    continue
                try:
                    raw = lock_file.read_text(encoding="utf-8", errors="ignore").strip()
                except OSError:
                    continue
                pid = 0
                if raw.isdigit():
                    pid = int(raw)
                else:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict) and str(parsed.get("pid", "")).isdigit():
                            pid = int(parsed["pid"])
                    except Exception:
                        pass
                if pid > 0 and not _pid_alive(pid):
                    orphans.append(lock_file)
        # 2. Real FileLock files in _lock_dir() — test with OS non-blocking lock probe + 24h age
        ldir = self._resolve_lock_dir()
        if ldir and ldir.is_dir():
            for lock_file in ldir.glob("*.lock"):
                if not lock_file.is_file():
                    continue
                try:
                    age = now - lock_file.stat().st_mtime
                except OSError:
                    continue
                if age > 86400 and not _file_lock_is_held(lock_file):
                    orphans.append(lock_file)
        return orphans

    def _check_orphan_locks(self) -> dict[str, Any]:
        orphans = self._find_orphan_locks()
        if not orphans:
            return _item("orphan_locks", "Ορφανά αρχεία .lock", "ok", "Δεν βρέθηκαν παλιά αρχεία κλειδώματος.")
        ldir = self._resolve_lock_dir()
        ldir_res = ldir.resolve() if ldir else None
        pid_orphans = [p for p in orphans if ldir_res is None or p.parent.resolve() != ldir_res]
        if pid_orphans:
            return _item(
                "orphan_locks",
                "Ορφανά αρχεία .lock",
                "warn",
                f"Βρέθηκαν {len(orphans)} παλιά αρχεία κλειδώματος χωρίς ενεργή διεργασία.",
                fixable=True,
                fix_label="Καθάρισμα",
                fix_preview=f"Θα διαγραφούν {len(orphans)} παλιά αρχεία κλειδώματος που δεν κρατιούνται από καμία διεργασία.",
                meta={"count": len(orphans)},
            )
        return _item(
            "orphan_locks",
            "Αρχεία κλειδώματος (.lock)",
            "ok",
            f"Υπάρχουν {len(orphans)} παλιά αρχεία κλειδώματος (φυσιολογικό — καθάρισμα προαιρετικό).",
            fixable=True,
            fix_label="Προαιρετικό καθάρισμα",
            fix_preview=f"Θα καθαριστούν {len(orphans)} παλιά ανενεργά αρχεία κλειδώματος από τον φάκελο temp.",
            meta={"count": len(orphans)},
        )

    def _find_temp_partials(self) -> list[Path]:
        """Find ONLY Akatikito's own stale temp/partial files (>1h old). NEVER user *.tmp files."""
        found: list[Path] = []
        now = time.time()
        # 1. App-owned dirs (data/ and .agent/): *.tmp / *.part older than 1 hour
        for app_dir in (self.data_dir, self.project_root / ".agent"):
            if not app_dir.is_dir():
                continue
            for p in app_dir.rglob("*"):
                if not p.is_file():
                    continue
                if not (p.name.endswith(".tmp") or p.name.endswith(".part")):
                    continue
                try:
                    if (now - p.stat().st_mtime) > _STALE_TEMP_SECONDS:
                        found.append(p)
                except OSError:
                    continue
                if len(found) >= 200:
                    return found
        # 2. Project workspace: ONLY exact MobileBridge partial upload pattern `.{name}.{8hex}.part` >1h old
        if self.project_root.is_dir():
            for root_s, dirs, files in os.walk(self.project_root):
                dirs[:] = [d for d in dirs if d not in _SKIP_SCAN_DIRS and d != ".agent"]
                for fn in files:
                    if not _MOBILE_PART_RE.match(fn):
                        continue
                    p = Path(root_s) / fn
                    try:
                        if (now - p.stat().st_mtime) > _STALE_TEMP_SECONDS:
                            found.append(p)
                    except OSError:
                        continue
                    if len(found) >= 200:
                        return found
        return found

    def _check_temp_partials(self) -> dict[str, Any]:
        found = self._find_temp_partials()
        if not found:
            return _item("temp_partials", "Μισά αρχεία .part / .tmp", "ok", "Δεν υπάρχουν ξεχασμένα προσωρινά αρχεία του Akatikito.")
        total_kb = round(sum(p.stat().st_size for p in found if p.is_file()) / 1024, 1)
        return _item(
            "temp_partials",
            "Μισά αρχεία .part / .tmp",
            "warn",
            f"Βρέθηκαν {len(found)} ξεχασμένα προσωρινά αρχεία του Akatikito (>1 ώρας, {total_kb} KB).",
            fixable=True,
            fix_label="Διαγραφή",
            fix_preview=f"Θα διαγραφούν {len(found)} προσωρινά αρχεία του Akatikito (>1 ώρας, {total_kb} KB). Δεν αγγίζει αρχεία χρήστη.",
            meta={"count": len(found), "total_kb": total_kb},
        )

    def _find_old_backups(self, days: int = 30) -> tuple[list[Path], int, int]:
        """Find >30-day edit backups in .agent_backups, strictly EXCLUDING organizer/ (Safe Trash + Undo) and index.jsonl."""
        bdir = self.project_root / ".agent_backups"
        if not bdir.is_dir():
            return [], 0, 0
        cutoff = time.time() - days * 86400
        old_files: list[Path] = []
        old_bytes = 0
        total_bytes = 0
        for p in bdir.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel_parts = p.relative_to(bdir).parts
            except ValueError:
                continue
            # CRITICAL: NEVER touch Safe Trash / Organizer Undo journals or index.jsonl!
            if rel_parts and rel_parts[0].lower() == "organizer":
                continue
            if p.name == "index.jsonl" and len(rel_parts) == 1:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            total_bytes += st.st_size
            if st.st_mtime < cutoff:
                old_files.append(p)
                old_bytes += st.st_size
        return old_files, old_bytes, total_bytes

    def _check_agent_backups(self) -> dict[str, Any]:
        old_files, old_bytes, total_bytes = self._find_old_backups(30)
        total_mb = round(total_bytes / (1024 * 1024), 2)
        old_mb = round(old_bytes / (1024 * 1024), 2)
        if not old_files and total_mb < 200:
            return _item("agent_backups", "Μέγεθος .agent_backups", "ok", f"Ο φάκελος .agent_backups είναι καθαρός ({total_mb} MB, εξαιρείται ο Κάδος).")
        if old_files:
            return _item(
                "agent_backups",
                "Μέγεθος .agent_backups",
                "warn",
                f"Το .agent_backups έχει {total_mb} MB, εκ των οποίων {len(old_files)} παλιά backups ({old_mb} MB) είναι πάνω από 30 ημερών (ο Κάδος/organizer εξαιρείται).",
                fixable=True,
                fix_label="Καθάρισμα >30 ημερών",
                fix_preview=f"Θα διαγραφούν {len(old_files)} παλιά edit-backups άνω των 30 ημερών ({old_mb} MB) και θα ενημερωθεί το index.jsonl. Ο Κάδος (organizer/) δεν αγγίζεται.",
                meta={"old_count": len(old_files), "old_mb": old_mb, "total_mb": total_mb},
            )
        return _item("agent_backups", "Μέγεθος .agent_backups", "warn", f"Το .agent_backups έχει φτάσει τα {total_mb} MB (όλα κάτω των 30 ημερών).")

    def is_busy(self) -> bool:
        """True if agent, self-update, mobile worker, or detached gateway is actively running."""
        if self.state is not None:
            if getattr(self.state, "busy", False) or getattr(self.state, "self_update", None):
                return True
            mb = getattr(self.state, "mobile_bridge", None)
            if mb is not None:
                workers = getattr(mb, "workers", None) or {}
                if any(getattr(getattr(w, "app", None), "busy", False) for w in workers.values()):
                    return True
        gw_ctrl = self.data_dir / "gateway" / "control.json"
        if gw_ctrl.is_file():
            try:
                desc = json.loads(gw_ctrl.read_text(encoding="utf-8"))
                pid = desc.get("pid") if isinstance(desc, dict) else 0
                if isinstance(pid, int) and pid > 0 and _pid_alive(pid):
                    return True
            except Exception:
                return True
        return False

    def snapshot_configs(self) -> dict[str, Any]:
        """Save timestamped .bak snapshots ONLY for valid JSON config files (never decrypting secrets)."""
        if self.is_busy():
            return {"ok": False, "error": "Περίμενε να ολοκληρωθούν πρώτα οι ενεργές εργασίες (agent / mobile / gateway)."}
        saved: list[str] = []
        skipped: list[str] = []
        touched: list[str] = []
        for label, path, _default in self._json_targets():
            if not path.is_file():
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                skipped.append(label)
                continue
            bak = _backup_file(path, "bak")
            if bak is not None:
                saved.append(label)
                touched.append(str(bak))
        summary = f"Αποθηκεύτηκε αντίγραφο για {len(saved)} έγκυρα αρχεία ρυθμίσεων" + (f" (παραλείφθηκαν {len(skipped)} άκυρα)." if skipped else ".")
        audit_logged = True
        try:
            AuditLog(self.project_root).record("doctor_snapshot", tool="snapshot_configs", status="ok", paths=touched, summary=summary, actor="human")
        except Exception:
            audit_logged = False
        return {"ok": True, "saved": saved, "skipped": skipped, "paths": touched, "summary": summary, "audit_logged": audit_logged}

    # ------------------------------------------------------------------ public API

    def diagnose(self, *, check_api: bool = False) -> dict[str, Any]:
        """Run all 18 read-only diagnostic checks."""
        checks = [
            self._check_json_integrity(), self._check_mobile_sessions(), self._check_mobile_port(),
            self._check_orphan_locks(), self._check_temp_partials(), self._check_agent_backups(),
            *_ext_checks.run_extended_checks(self, _item, check_api=check_api),
        ]
        counts = {
            "ok": sum(1 for c in checks if c["status"] == "ok"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "error": sum(1 for c in checks if c["status"] == "error"),
        }
        overall = "error" if counts["error"] else ("warn" if counts["warn"] else "ok")
        return {
            "ok": overall == "ok",
            "overall": overall,
            "badge": BADGES[overall],
            "busy": self.is_busy(),
            "counts": counts,
            "checks": checks,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def apply_fix(self, check_id: str, *, action: str = "") -> dict[str, Any]:
        """Apply a single explicit human-approved safe fix and record it in the AuditLog."""
        cid = str(check_id or "").strip()
        if cid == "audit_chain":
            return {"ok": False, "error": "Το audit log δεν επισκευάζεται αυτόματα για λόγους ακεραιότητας."}
        if self.is_busy():
            return {"ok": False, "error": "Περίμενε να ολοκληρωθούν πρώτα οι ενεργές εργασίες (agent / mobile / gateway) πριν την επισκευή."}

        touched: list[str] = []
        summary = ""

        if cid == "json_integrity":
            fixed = []
            for label, path, default in self._json_targets():
                if not path.is_file():
                    continue
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                    continue
                except Exception:
                    pass
                ts = time.strftime("%Y%m%d-%H%M%S")
                broken_copy = path.with_name(f"{path.stem}.broken.{ts}{path.suffix}")
                shutil.copy2(path, broken_copy)
                touched.append(str(broken_copy))
                backup = _find_latest_valid_json_backup(path)
                if backup is not None:
                    shutil.copy2(backup, path)
                    fixed.append(f"{label} (από backup {backup.name})")
                else:
                    path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
                    fixed.append(f"{label} (καθαρή προεπιλογή)")
                touched.append(str(path))
            summary = f"Επαναφέρθηκαν JSON: {', '.join(fixed)}" if fixed else "Δεν βρέθηκαν χαλασμένα JSON."

        elif cid == "mobile_sessions":
            path = self.data_dir / "mobile_bridge.json"
            if path.is_file():
                _backup_file(path, "bak")
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions = data.get("sessions", {}) if isinstance(data, dict) else {}
                now = time.time()
                clean = {}
                if isinstance(sessions, dict):
                    for k, created in sessions.items():
                        ks = str(k or "").strip().lower()
                        try:
                            ts = float(created)
                        except (TypeError, ValueError):
                            continue
                        if _SHA256_RE.match(ks) and (now - ts) < SESSION_TTL_SECONDS and ts <= now + 86400:
                            clean[ks] = ts
                data = data if isinstance(data, dict) else {}
                removed = (len(sessions) - len(clean)) if isinstance(sessions, dict) else 0
                data["sessions"] = clean
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                touched.append(str(path))
                summary = f"Αφαιρέθηκαν {removed} παλιές/άκυρες συνεδρίες κινητού."
            else:
                summary = "Δεν υπάρχει mobile_bridge.json."

        elif cid == "orphan_locks":
            orphans = self._find_orphan_locks()
            for p in orphans:
                try:
                    p.unlink(missing_ok=True)
                    touched.append(str(p))
                except OSError:
                    pass
            summary = f"Διαγράφηκαν {len(touched)} ορφανά αρχεία .lock."

        elif cid == "temp_partials":
            found = self._find_temp_partials()
            for p in found:
                try:
                    p.unlink(missing_ok=True)
                    touched.append(str(p))
                except OSError:
                    pass
            summary = f"Διαγράφηκαν {len(touched)} προσωρινά αρχεία του Akatikito."

        elif cid == "agent_backups":
            old_files, old_bytes, _ = self._find_old_backups(30)
            deleted_names: set[str] = set()
            for p in old_files:
                try:
                    p.unlink(missing_ok=True)
                    touched.append(str(p))
                    deleted_names.add(p.name)
                except OSError:
                    pass
            # Prune stale rows from .agent_backups/index.jsonl for deleted files
            idx_path = self.project_root / ".agent_backups" / "index.jsonl"
            if deleted_names and idx_path.is_file():
                try:
                    _backup_file(idx_path, "bak")
                    kept_lines = []
                    for line in idx_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                            bname = Path(str(row.get("backup", ""))).name
                            if bname and bname in deleted_names:
                                continue
                        except Exception:
                            pass
                        kept_lines.append(line)
                    idx_path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
                    touched.append(str(idx_path))
                except OSError:
                    pass
            summary = f"Καθαρίστηκαν {len(deleted_names)} παλιά backups >30 ημερών ({round(old_bytes / (1024 * 1024), 2)} MB)."

        else:
            ext = _ext_checks.apply_extended_fix(self, cid, action, _backup_file)
            if ext is None:
                return {"ok": False, "error": f"Μη υποστηριζόμενη επισκευή: {cid}"}
            if isinstance(ext, dict):
                return ext
            touched, summary = ext

        audit_logged = True
        try:
            AuditLog(self.project_root).record(
                "doctor_fix",
                tool=cid,
                status="ok",
                paths=touched,
                summary=summary,
                actor="human",
            )
        except Exception:
            audit_logged = False
            summary += " (προσοχή: η επισκευή έγινε, αλλά δεν καταγράφηκε στο audit log)"

        return {"ok": True, "check_id": cid, "summary": summary, "paths": touched, "audit_logged": audit_logged}


def _cli(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="🩺 Γιατρός Akatikito — Διάγνωση & ασφαλής επισκευή")
    parser.add_argument("--project", default="", help="Φάκελος project (προεπιλογή: τρέχων φάκελος)")
    parser.add_argument("--check-api", action="store_true", help="Ζωντανός έλεγχος API key (/models)")
    parser.add_argument("--fix", default="", help="Εκτέλεση συγκεκριμένης επισκευής (ID ελέγχου)")
    parser.add_argument("--action", default="", help="Ενέργεια για uncertain_jobs (retry | cancel)")
    parser.add_argument("--snapshot", action="store_true", help="Αποθήκευση αντιγράφου έγκυρων ρυθμίσεων (.bak)")
    parser.add_argument("--json", action="store_true", help="Έξοδος σε JSON")
    args = parser.parse_args(argv)

    doc = Doctor(project_root=args.project or None)
    if args.snapshot:
        res = doc.snapshot_configs()
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(("✅ " if res.get("ok") else "❌ ") + str(res.get("summary") or res.get("error")))
        return 0 if res.get("ok") else 1
    if args.fix:
        res = doc.apply_fix(args.fix, action=args.action)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(("✅ " if res.get("ok") else "❌ ") + str(res.get("summary") or res.get("error")))
        return 0 if res.get("ok") else 1

    report = doc.diagnose(check_api=args.check_api)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["overall"] != "error" else 1

    print(f"\n🩺 Γιατρός Akatikito — {report['badge']} ({report['counts']['ok']} OK, {report['counts']['warn']} Προσοχή, {report['counts']['error']} Σφάλματα)\n")
    for c in report["checks"]:
        print(f"  {c['badge']}  {c['title']} [{c['id']}]")
        print(f"      {c['details']}")
        if c["fixable"]:
            print(f"      → Επισκευή: python -m akatikito.doctor --fix {c['id']}")
    return 0 if report["overall"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
