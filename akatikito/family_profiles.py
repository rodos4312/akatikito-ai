"""Family / multi-member isolated profiles with PBKDF2 PIN escalation guard (PRO-PLAN B10).

Each profile is an isolated island under `data/profiles/<id>/` with its own:
- `settings.json` (keys, provider, daily budget)
- `memory/memory.db` (central SQLite memory)
- `usage.json` (independent cost ledger)
- `sessions/` (isolated conversation history)
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .local_store import load_json, save_json

_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_VALID_ROLES = ("admin", "standard", "restricted")
_ROLE_RANK = {"restricted": 0, "standard": 1, "admin": 2}
_PBKDF2_ITERS = 120_000
PIN_MAX_WRONG = 5
PIN_LOCKOUT_SECONDS = 30.0


class PinLockoutError(PermissionError):
    """Raised when too many invalid PIN attempts trigger temporary lockout."""

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after))


def _hash_pin(pin: str) -> str:
    clean = str(pin or "").strip()
    if not clean:
        return ""
    if len(clean) < 4 or len(clean) > 32:
        raise ValueError("Το PIN πρέπει να έχει από 4 έως 32 χαρακτήρες.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", clean.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERS).hex()
    return f"pbkdf2:sha256:{_PBKDF2_ITERS}:{salt}:{digest}"


def _verify_pin(pin: str, stored: str) -> bool:
    if not stored:
        return True
    parts = str(stored).split(":")
    if len(parts) != 5 or parts[0] != "pbkdf2" or parts[1] != "sha256":
        return False
    try:
        iters = int(parts[2])
        salt = bytes.fromhex(parts[3])
        expected = parts[4]
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", str(pin or "").strip().encode("utf-8"), salt, iters).hex()
    return hmac.compare_digest(computed, expected)


@dataclass(frozen=True)
class ProfilePaths:
    profile_id: str
    root: Path
    settings_file: Path
    memory_dir: Path
    memory_db: Path
    usage_file: Path
    sessions_dir: Path
    agenda_file: Path


class FamilyProfiles:
    """Manages isolated family profiles stored under `<data_dir>/profiles/`."""

    def __init__(self, data_dir: str | Path, *, clock: Callable[[], float] | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.profiles_dir = self.data_dir / "profiles"
        self.registry_path = self.profiles_dir / "registry.json"
        self._clock = clock or time.monotonic
        self._pin_failures: dict[str, int] = {}
        self._pin_locked_until: dict[str, float] = {}

    def _load(self) -> dict:
        data = load_json(self.registry_path, lambda: {"active": "", "profiles": {}})
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
            return {"active": "", "profiles": {}}
        return data

    def _save(self, data: dict) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        save_json(self.registry_path, data)

    def _paths_from_root(self, pid: str, root: Path) -> ProfilePaths:
        mem_dir = root / "memory"
        return ProfilePaths(
            profile_id=pid,
            root=root,
            settings_file=root / "settings.json",
            memory_dir=mem_dir,
            memory_db=mem_dir / "memory.db",
            usage_file=root / "usage.json",
            sessions_dir=root / "sessions",
            agenda_file=root / "agenda.json",
        )

    def paths_for(self, profile_id: str) -> ProfilePaths:
        pid = str(profile_id or "").strip().lower()
        if not _PROFILE_ID_RE.fullmatch(pid):
            raise ValueError("Μη έγκυρο αναγνωριστικό προφίλ (μόνο πεζά a-z, 0-9, -, _).")
        row = self._load()["profiles"].get(pid)
        if isinstance(row, dict) and row.get("in_place") is True:
            return self._paths_from_root(pid, self.data_dir)
        root = (self.profiles_dir / pid).resolve()
        if not root.is_relative_to(self.profiles_dir.resolve()):
            raise ValueError("Μη επιτρεπτή διαδρομή προφίλ.")
        return self._paths_from_root(pid, root)

    def init_primary_profile(self, display_name: str = "Κύριο", *, pin: str = "") -> dict:
        """Register existing data_dir in-place as the primary admin profile (zero copy)."""
        data = self._load()
        if "primary" in data["profiles"]:
            return self._public_view(data["profiles"]["primary"])
        if not str(pin or "").strip():
            raise ValueError("Το κύριο προφίλ (admin) χρειάζεται PIN προστασίας.")
        record = {
            "id": "primary",
            "display_name": str(display_name or "Κύριο").strip()[:60],
            "role": "admin",
            "pin_hash": _hash_pin(pin),
            "daily_budget": 0.0,
            "allow_commands": True,
            "allow_mobile_bridge": True,
            "allowed_projects": [],
            "in_place": True,
        }
        data["profiles"]["primary"] = record
        if not data.get("active"):
            data["active"] = "primary"
        self._save(data)
        return self._public_view(record)

    def create_profile(
        self,
        profile_id: str,
        display_name: str,
        *,
        role: str = "standard",
        pin: str = "",
        daily_budget: float = 0.0,
        allow_commands: bool = True,
        allow_mobile_bridge: bool = True,
        allowed_projects: tuple[str, ...] | list[str] = (),
    ) -> dict:
        if role not in _VALID_ROLES:
            raise ValueError(f"Μη έγκυρος ρόλος προφίλ: {role}")
        paths = self.paths_for(profile_id)
        data = self._load()
        if paths.profile_id in data["profiles"]:
            raise ValueError(f"Το προφίλ '{paths.profile_id}' υπάρχει ήδη.")
        if role == "admin" and not str(pin or "").strip():
            raise ValueError("Τα προφίλ διαχειριστή (admin) χρειάζονται PIN προστασίας.")
        paths.memory_dir.mkdir(parents=True, exist_ok=True)
        paths.sessions_dir.mkdir(parents=True, exist_ok=True)
        norm_projects = [str(Path(p).expanduser().resolve()) for p in allowed_projects if str(p).strip()]
        record = {
            "id": paths.profile_id,
            "display_name": str(display_name or paths.profile_id).strip()[:60],
            "role": role,
            "pin_hash": _hash_pin(pin),
            "daily_budget": max(0.0, float(daily_budget or 0.0)),
            "allow_commands": bool(allow_commands) if role != "restricted" else False,
            "allow_mobile_bridge": bool(allow_mobile_bridge) if role != "restricted" else False,
            "allowed_projects": norm_projects,
        }
        data["profiles"][paths.profile_id] = record
        if not data.get("active"):
            data["active"] = paths.profile_id
        self._save(data)
        return self._public_view(record)

    @staticmethod
    def _public_view(record: dict) -> dict:
        return {k: v for k, v in record.items() if k != "pin_hash"} | {"has_pin": bool(record.get("pin_hash"))}

    def list_profiles(self) -> list[dict]:
        data = self._load()
        return [self._public_view(row) for row in data["profiles"].values()]

    def active_profile(self) -> dict | None:
        data = self._load()
        active_id = data.get("active")
        row = data["profiles"].get(active_id)
        return self._public_view(row) if row else None

    def _verify_pin_with_lockout(self, profile_id: str, pin_hash: str, pin: str) -> None:
        now = float(self._clock())
        locked_until = self._pin_locked_until.get(profile_id, 0.0)
        if now < locked_until:
            remaining = locked_until - now
            raise PinLockoutError(
                f"Πολλές λανθασμένες προσπάθειες PIN. Δοκίμασε ξανά σε {int(remaining) + 1} δευτ.",
                retry_after=remaining,
            )
        if locked_until:
            self._pin_locked_until.pop(profile_id, None)
            self._pin_failures.pop(profile_id, None)
        if not pin_hash or not _verify_pin(pin, pin_hash):
            fails = self._pin_failures.get(profile_id, 0) + 1
            self._pin_failures[profile_id] = fails
            if fails >= PIN_MAX_WRONG:
                self._pin_locked_until[profile_id] = now + PIN_LOCKOUT_SECONDS
                self._pin_failures[profile_id] = 0
            raise PermissionError("Απαιτείται έγκυρος κωδικός PIN για αλλαγή σε αυτό το προφίλ.")
        self._pin_failures.pop(profile_id, None)

    def switch_profile(self, target_id: str, *, pin: str = "") -> ProfilePaths:
        """Switch active profile; requires valid PIN when target has PIN or higher privilege."""
        data = self._load()
        target = data["profiles"].get(str(target_id or "").strip().lower())
        if target is None:
            raise ValueError(f"Δεν βρέθηκε προφίλ: {target_id}")
        current = data["profiles"].get(data.get("active") or "")
        elevating = current is not None and _ROLE_RANK[target["role"]] > _ROLE_RANK[current["role"]]
        if target.get("pin_hash") or elevating:
            self._verify_pin_with_lockout(target["id"], str(target.get("pin_hash") or ""), pin)
        data["active"] = target["id"]
        self._save(data)
        return self.paths_for(target["id"])

    def check_access(
        self,
        profile_id: str,
        *,
        tool_name: str = "",
        project_path: str | Path | None = None,
        mobile_bridge: bool = False,
        advanced: bool = False,
    ) -> tuple[bool, str]:
        """Enforce restricted profile boundaries (commands, mobile bridge, advanced, allowed folders)."""
        data = self._load()
        row = data["profiles"].get(str(profile_id or "").strip().lower())
        if row is None:
            return False, "Άγνωστο προφίλ."
        if advanced and row.get("role") == "restricted":
            return False, "Το τρέχον προφίλ δεν επιτρέπει αλλαγές στο Advanced."
        if tool_name == "run_command" and not row.get("allow_commands", True):
            return False, "Η εκτέλεση εντολών (run_command) είναι απενεργοποιημένη σε αυτό το προφίλ."
        if mobile_bridge and not row.get("allow_mobile_bridge", True):
            return False, "Η γέφυρα κινητού είναι απενεργοποιημένη σε αυτό το προφίλ."
        allowed = row.get("allowed_projects") or []
        if project_path is not None and allowed:
            target = Path(project_path).expanduser().resolve()
            if not any(target.is_relative_to(Path(root)) for root in allowed):
                return False, "Αυτός ο φάκελος δεν επιτρέπεται στο τρέχον προφίλ."
        return True, ""
