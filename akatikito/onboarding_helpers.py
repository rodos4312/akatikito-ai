"""First-run onboarding helpers: Windows OS locale detection, local AI probing, plain errors (F3)."""
from __future__ import annotations

import json
import locale
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .i18n import SUPPORTED_LANGUAGES, normalize_lang, translations

DEFAULT_LOCAL_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("Ollama", "http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1/models", "http://127.0.0.1:1234/v1"),
)
LOCAL_INSTALL_LINKS: dict[str, str] = {
    "ollama": "https://ollama.com",
    "lm_studio": "https://lmstudio.ai",
}


def detect_windows_ui_language() -> str:
    """Detect OS/Windows UI language and map to one of the 6 supported languages."""
    if os.name == "nt":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lcid & 0x3FF
            win_map = {0x08: "el", 0x09: "en", 0x19: "ru", 0x07: "de", 0x0A: "es", 0x0C: "fr"}
            if primary in win_map:
                return win_map[primary]
        except (AttributeError, OSError):
            primary = 0
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    prefix = loc.split("_", 1)[0].lower()
    return prefix if prefix in SUPPORTED_LANGUAGES else "el"


def friendly_connection_error(exc_or_msg: str | BaseException, lang: str = "el") -> str:
    """Convert raw HTTP/network/auth errors into plain, human-readable messages in the user's language."""
    bundle = translations(normalize_lang(lang))
    raw = str(exc_or_msg or "").lower()
    if any(tok in raw for tok in ("401", "403", "invalid_api_key", "invalid api key", "unauthorized", "authentication", "incorrect api key")):
        return bundle.get("err_bad_key", "Το κλειδί API δεν έγινε δεκτό. Έλεγξε ότι το αντέγραψες σωστά.")
    if any(tok in raw for tok in ("402", "insufficient_quota", "insufficient balance", "billing", "credit", "quota", "payment required")):
        return bundle.get("err_no_balance", "Δεν υπάρχει διαθέσιμο υπόλοιπο στον λογαριασμό AI.")
    if any(tok in raw for tok in ("getaddrinfo", "name or service", "connection refused", "timed out", "timeout", "urlerror", "network", "unreachable", "nodename")):
        return bundle.get("err_no_internet", "Δεν υπάρχει σύνδεση στο internet ή η υπηρεσία δεν απαντά.")
    return bundle.get("err_no_internet", "Δεν ήταν δυνατή η σύνδεση. Έλεγξε το κλειδί και τη σύνδεσή σου.")


def _extract_local_models(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("models"), list):
        return [str(m.get("name") or m.get("model") or "") for m in payload["models"] if isinstance(m, dict) and (m.get("name") or m.get("model"))]
    if isinstance(payload.get("data"), list):
        return [str(m.get("id") or "") for m in payload["data"] if isinstance(m, dict) and m.get("id")]
    return []


def detect_local_ai(
    endpoints: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...] | None = None,
    *,
    timeout: float = 1.2,
) -> dict:
    """Probe loopback-only local AI servers (Ollama / LM Studio). Never downloads anything."""
    found: list[dict] = []
    for name, probe_url, base_url in (endpoints if endpoints is not None else DEFAULT_LOCAL_ENDPOINTS):
        host = (urlsplit(probe_url).hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            continue
        try:
            req = urllib.request.Request(probe_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback-only URL validated above
                if resp.status == 200:
                    data = json.loads(resp.read(262_144).decode("utf-8", errors="replace"))
                    found.append({"name": name, "base_url": base_url, "models": _extract_local_models(data)})
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return {"found": found, "install_links": dict(LOCAL_INSTALL_LINKS), "auto_download": False}
