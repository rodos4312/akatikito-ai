"""Centralized secret redaction before anything is written to disk or exported (PRO-PLAN B1).

Applies to: saved sessions (.agent/sessions/*.json), audit log, diagnostics bundles,
and exported chat Markdown. Never mutates the live in-memory message sent to the model.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    ("openai", re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{12,}")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("github", re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}")),
    ("notion", re.compile(r"\bntn_[A-Za-z0-9]{16,}")),
    ("secret", re.compile(r"\bsecret_[A-Za-z0-9]{16,}")),
    ("bearer", re.compile(r"(?i)(\b(?:authorization\s*:\s*)?bearer\s+)[A-Za-z0-9._~+/-]{12,}")),
    (
        "assign",
        re.compile(
            r"\b([A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD)|PASSWORD|API_KEY|SECRET_KEY|ACCESS_TOKEN|BOT_TOKEN)"
            r"(\s*[=:]\s*)[\"']?[^\s\"',;]{8,}[\"']?"
        ),
    ),
)


def _mask_token(value: str) -> str:
    return value[:4] + "…" + value[-2:] if len(value) > 10 else "***"


def redact(text: str) -> str:
    """Scrub API keys, bearer tokens, env credential assignments, and PEM private keys."""
    if not text:
        return text
    out = str(text)
    for kind, pattern in _PATTERNS:
        if kind == "assign":
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}***", out)
        elif kind == "bearer":
            out = pattern.sub(lambda m: f"{m.group(1)}***", out)
        elif kind == "pem":
            out = pattern.sub("[redacted:private-key]", out)
        else:
            out = pattern.sub(lambda m, k=kind: f"[{k}:{_mask_token(m.group(0))}]", out)
    return out


def redact_known(text: str, secrets: Iterable[str]) -> str:
    """Also mask explicit user secrets (e.g. saved provider keys), even if they match no pattern."""
    if not text:
        return text
    out = str(text)
    valid = sorted({str(s) for s in secrets if s and len(str(s)) >= 6}, key=len, reverse=True)
    for sec in valid:
        if sec in out:
            out = out.replace(sec, "***")
    return out


def redact_all(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Apply both explicit known-secret masking and pattern-based redaction."""
    if not text:
        return text
    return redact(redact_known(str(text), extra_secrets))


def collect_user_secrets(settings_obj=None) -> tuple[str, ...]:
    """Safely collect configured provider API keys (plaintext) for redaction without leaking them."""
    secrets: list[str] = []
    try:
        from .settings import Settings, unmask_secret

        st = settings_obj if settings_obj is not None else Settings.load()
        for val in (getattr(st, "keys", None) or {}).values():
            raw = unmask_secret(val)
            if raw and len(raw) >= 6:
                secrets.append(raw)
        for attr in ("fast_api_key", "smart_api_key", "fallback_api_key"):
            raw = unmask_secret(getattr(st, attr, "") or "")
            if raw and len(raw) >= 6:
                secrets.append(raw)
    except Exception:
        pass
    return tuple(dict.fromkeys(secrets))


def redact_obj(obj: Any, extra_secrets: Iterable[str] = ()) -> tuple[Any, bool]:
    """Recursively redact strings in JSON-like dicts/lists; returns (redacted_obj, changed)."""
    secrets = tuple(extra_secrets) if not isinstance(extra_secrets, tuple) else extra_secrets
    changed = False

    def _walk(val: Any, key: str = "") -> Any:
        nonlocal changed
        if isinstance(val, str):
            # Never touch base64 image data URIs
            if key == "url" and val.startswith("data:image/"):
                return val
            red = redact_all(val, secrets)
            if red != val:
                changed = True
            return red
        if isinstance(val, list):
            return [_walk(x) for x in val]
        if isinstance(val, dict):
            return {k: _walk(v, k) for k, v in val.items()}
        return val

    return _walk(obj), changed
