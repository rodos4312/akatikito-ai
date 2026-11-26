"""Ρυθμίσεις που μένουν: πάροχος, μοντέλο και κλειδιά API ανά πάροχο.

Αποθηκεύονται τοπικά (default: data/settings.json) ώστε να μη ζητάει κλειδί σε κάθε
επανεκκίνηση. Το κλειδί δεν γράφεται ποτέ σε καθαρή μορφή στο αρχείο — μπαίνει με
απλή αντιστρεπτή συγκάλυψη (base64 + XOR). Δεν είναι κρυπτογράφηση: είναι προστασία
από «ματιές», όχι από κάποιον που έχει πρόσβαση στο μηχάνημα.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = Path(os.getenv("AKATIKITO_SETTINGS_FILE", str(ROOT / "data" / "settings.json")))

_MASK = b"Akatikito::topikes::rythmiseis"
_PREFIX = "v1:"


def mask_secret(text: str) -> str:
    """Κρυπτογραφεί το κλειδί με Windows DPAPI (CryptProtectData) πριν γραφτεί στο αρχείο, με fallback XOR εκτός Windows."""
    if not text:
        return ""
    from .secret_store import DPAPI_PREFIX, protect_text

    if str(text).startswith(DPAPI_PREFIX):
        return str(text)
    protected = protect_text(str(text))
    if protected is not None:
        return protected
    raw = str(text).encode("utf-8")
    mixed = bytes(byte ^ _MASK[i % len(_MASK)] for i, byte in enumerate(raw))
    return _PREFIX + base64.urlsafe_b64encode(mixed).decode("ascii")


def unmask_secret(text: str) -> str:
    """Αποκρυπτογραφεί DPAPI ('dpapi:v1:') ή παλαιότερο 'v1:' κλειδί. Σε σφάλμα επιστρέφει κενό."""
    if not text:
        return ""
    s = str(text)
    from .secret_store import DPAPI_PREFIX, unprotect_text

    if s.startswith(DPAPI_PREFIX):
        return unprotect_text(s)
    if not s.startswith(_PREFIX):
        return s  # παλιά/χειροκίνητη καταχώρηση
    try:
        mixed = base64.urlsafe_b64decode(s[len(_PREFIX) :].encode("ascii"))
        return bytes(byte ^ _MASK[i % len(_MASK)] for i, byte in enumerate(mixed)).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _load_routing_fields(settings: Settings, data: dict) -> None:
    for field_name in (
        "fast_provider",
        "fast_model",
        "smart_provider",
        "smart_model",
        "fallback_provider",
        "fallback_model",
    ):
        setattr(settings, field_name, str(data.get(field_name) or "").strip())
    settings.sidebar_collapsed = data.get("sidebar_collapsed") is True


def short_key(key: str) -> str:
    """Δείχνει μόνο την αρχή και το τέλος του κλειδιού (για επιβεβαίωση)."""
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


@dataclass
class Settings:
    provider: str = "DeepSeek"
    base_url: str = ""
    model: str = ""
    fast_provider: str = ""
    fast_model: str = ""
    smart_provider: str = ""
    smart_model: str = ""
    fallback_provider: str = ""
    fallback_model: str = ""
    max_steps: int = 50
    verify_command: str = ""
    project: str = ""
    keys: dict[str, str] = field(default_factory=dict)  # πάροχος -> συγκαλυμμένο κλειδί
    models: dict[str, str] = field(default_factory=dict)
    key_origins: dict[str, str] = field(default_factory=dict)
    autonomous: bool = False
    cautious_edits: bool = False
    advanced_view: bool = False
    advanced_features: list[str] = field(default_factory=list)
    daily_budget: float = 0.0
    model_prices: dict = field(default_factory=dict)
    vision_models: list[str] = field(default_factory=list)
    checkpoints: bool = True
    allowed_commands: list[str] = field(default_factory=list)
    voice_model: str = 'base'
    voice_language: str = 'el'
    ui_language: str = 'el'
    ui_theme: str = 'dark'
    ui_font_size: int = 14
    sidebar_collapsed: bool = False
    recent_projects: list[str] = field(default_factory=list)
    global_hotkey: str = "Alt+Space"
    background_mode: bool = False
    background_autostart: bool = False
    background_service: bool = False
    notes_folder: str = ""
    onboarding_complete: bool = False
    warning: str = field(default="", compare=False)

    # --- αρχείο ---------------------------------------------------------

    @staticmethod
    def _load_ui_fields(settings: "Settings", data: dict) -> None:
        from .i18n import SUPPORTED_LANGUAGES, normalize_lang

        settings.voice_model = data.get("voice_model") if data.get("voice_model") in ("base", "small") else "base"
        settings.voice_language = (
            data.get("voice_language") if data.get("voice_language") in ("el", "en", "ru", "de", "es", "fr", "auto") else "el"
        )
        settings.ui_language = normalize_lang(data.get("ui_language")) if data.get("ui_language") in SUPPORTED_LANGUAGES else "el"
        settings.ui_theme = data.get("ui_theme") if data.get("ui_theme") in ("dark", "light") else "dark"
        try:
            settings.ui_font_size = max(12, min(20, int(data.get("ui_font_size", 14))))
        except (TypeError, ValueError):
            settings.ui_font_size = 14
        rp = data.get("recent_projects")
        if isinstance(rp, list):
            seen: set[str] = set()
            clean_rp: list[str] = []
            for item in rp:
                if isinstance(item, str) and item.strip():
                    k = item.strip().casefold()
                    if k not in seen:
                        seen.add(k)
                        clean_rp.append(item.strip())
            settings.recent_projects = clean_rp[:10]

    @staticmethod
    def _load_provider_fields(settings: "Settings", data: dict) -> None:
        settings.provider = str(data.get("provider") or settings.provider)
        settings.base_url = str(data.get("base_url") or "")
        settings.model = str(data.get("model") or "")
        try:
            settings.max_steps = int(data.get("max_steps") or settings.max_steps)
        except (TypeError, ValueError):
            settings.max_steps = 50
        settings.verify_command = str(data.get("verify_command") or "")
        settings.project = str(data.get("project") or "")
        models = data.get("models") or {}
        if isinstance(models, dict):
            settings.models = {str(k): str(v) for k, v in models.items() if v}
        if settings.model:
            settings.models[settings.provider] = settings.model
        settings.onboarding_complete = (
            data.get("onboarding_complete", bool(settings.project and (settings.model or settings.models.get(settings.provider)))) is True
        )
        raw_vis = data.get("vision_models")
        settings.vision_models = [str(x) for x in raw_vis if isinstance(x, str)] if isinstance(raw_vis, list) else []

    @staticmethod
    def _load_security_fields(settings: "Settings", data: dict) -> None:
        from .advanced import sanitize_feature_list

        settings.advanced_view = data.get("advanced_view") is True
        settings.advanced_features = sanitize_feature_list(data.get("advanced_features", []))
        try:
            settings.daily_budget = max(0, float(data.get("daily_budget", 0)))
        except (TypeError, ValueError):
            settings.daily_budget = 0
        settings.model_prices = data.get("model_prices", {}) if isinstance(data.get("model_prices"), dict) else {}
        settings.autonomous = data.get("autonomous") is True
        _load_routing_fields(settings, data)
        settings.cautious_edits = data.get("cautious_edits") is True
        settings.checkpoints = data.get("checkpoints", True) is True
        settings.global_hotkey = str(data.get("global_hotkey", "Alt+Space") or "").strip()
        settings.background_mode = data.get("background_mode") is True
        settings.background_autostart = data.get("background_autostart") is True
        settings.background_service = data.get("background_service") is True
        settings.notes_folder = str(data.get("notes_folder") or "").strip()
        raw_cmds = data.get("allowed_commands")
        settings.allowed_commands = [str(x) for x in raw_cmds if isinstance(x, str)] if isinstance(raw_cmds, list) else []
        origins = data.get("key_origins") or {}
        if isinstance(origins, dict):
            settings.key_origins = {str(k): str(v) for k, v in origins.items() if v}
        keys = data.get("keys") or {}
        if isinstance(keys, dict):
            settings.keys = {str(k): str(v) for k, v in keys.items() if v}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        target = Path(path) if path else DEFAULT_PATH
        settings = cls()
        if not target.exists():
            return settings
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig") or "{}")
        except Exception:  # noqa: BLE001 — χαλασμένο αρχείο δεν σταματάει τη δουλειά
            try:
                target.replace(target.with_suffix(".broken.json"))
                settings.warning = "Το αρχείο ρυθμίσεων ήταν χαλασμένο — κρατήθηκε αντίγραφο."
            except OSError:
                settings.warning = "Δεν διάβασα το αρχείο ρυθμίσεων."
            return settings
        if not isinstance(data, dict):
            settings.warning = "Το αρχείο ρυθμίσεων δεν είχε τη σωστή μορφή."
            return settings
        cls._load_ui_fields(settings, data)
        cls._load_provider_fields(settings, data)
        cls._load_security_fields(settings, data)
        return settings

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else DEFAULT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'onboarding_complete':self.onboarding_complete,
            'voice_model':self.voice_model,'voice_language':self.voice_language,'ui_language':self.ui_language,
            'ui_theme':self.ui_theme,'ui_font_size':int(self.ui_font_size),'sidebar_collapsed':bool(self.sidebar_collapsed),'recent_projects':list(self.recent_projects[:10]),
            'global_hotkey':self.global_hotkey,
            'background_mode':bool(self.background_mode),
            'background_autostart':bool(self.background_autostart),
            'background_service':bool(self.background_service),
            'notes_folder':self.notes_folder,
            "version": 1,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "fast_provider": self.fast_provider,
            "fast_model": self.fast_model,
            "smart_provider": self.smart_provider,
            "smart_model": self.smart_model,
            "fallback_provider": self.fallback_provider,
            "fallback_model": self.fallback_model,
            "max_steps": int(self.max_steps),
            "verify_command": self.verify_command,
            "project": self.project,
            "models": self.models,
            "key_origins": self.key_origins,
            'autonomous':self.autonomous, 'cautious_edits':self.cautious_edits, 'checkpoints':self.checkpoints,
            'advanced_view':self.advanced_view,'advanced_features':list(self.advanced_features),'daily_budget':self.daily_budget,'model_prices':self.model_prices,
            'allowed_commands':self.allowed_commands, 'vision_models':self.vision_models,
            "keys": {k: v for k, v in self.keys.items() if v},
        }
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, target)
        try:  # να μη το διαβάζει όποιος περάσει (Windows: μόνο ο χρήστης)
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target

    # --- κλειδιά --------------------------------------------------------

    def set_key(self, provider: str, key: str) -> None:
        key = (key or "").strip()
        if not key:
            self.keys.pop(provider, None)
            return
        self.keys[provider] = mask_secret(key)

    def key_for(self, provider: str) -> str:
        return unmask_secret(self.keys.get(provider, ""))

    def has_key(self, provider: str) -> bool:
        return bool(self.key_for(provider))

    def remember_project(self, path: str) -> None:
        clean = str(path or "").strip()
        if not clean:
            return
        key = clean.casefold()
        self.recent_projects = [clean] + [p for p in self.recent_projects if p.casefold() != key]
        self.recent_projects = self.recent_projects[:10]

    def forget_key(self, provider: str) -> None:
        if provider in self.keys:
            self.keys.pop(provider)
            self.save()

    def known_providers(self) -> list[str]:
        return sorted(self.keys)

    def summary(self) -> list[dict]:
        """Τι ξέρουμε για κάθε πάροχο — για εμφάνιση στο UI."""
        rows: list[dict] = []
        for name in sorted(self.keys):
            key = self.key_for(name)
            if key:
                rows.append({"provider": name, "short": short_key(key)})
        return rows
