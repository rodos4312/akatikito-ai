"""Smart provider error classification, Retry-After backoff, and explicit fallback model (PRO-PLAN B3).

Rules:
- 401/403 (auth): 0 retries, no fallback -> clear Greek guidance to check key/Doctor.
- 402 (no balance): 0 retries, eligible for explicit fallback provider.
- 400 (bad/oversized request): 0 retries, no fallback.
- 429 (rate limit): honors numeric Retry-After header (capped at 30s) or exponential backoff;
  eligible for explicit fallback after attempts exhaust.
- 5xx / 529 / timeout / connection: retries with backoff, then explicit fallback.
- Fallback provider MUST be explicitly configured by the owner and NEVER leaks primary API keys
  to a different non-local endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
import random


@dataclass(frozen=True)
class Verdict:
    retry: bool
    fallback: bool
    wait: float = 0.0
    message: str = ""


_TRANSIENT_STATUSES = frozenset({408, 409, 500, 502, 503, 504, 529})
_TRANSIENT_NAMES = frozenset({
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "TimeoutError",
    "ConnectionError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
})


_HARD_QUOTA_MARKERS = (
    "weekly included input token limit exceeded",
    "daily included input token limit exceeded",
    "monthly included input token limit exceeded",
    "insufficient_quota",
    "quota_exceeded",
    "insufficient balance",
)


def extract_status(exc: Exception | None) -> int | None:
    """Read HTTP status code from exc.status_code or nested exc.response.status_code."""
    if exc is None:
        return None
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    resp = getattr(exc, "response", None)
    nested = getattr(resp, "status_code", None) if resp is not None else None
    return nested if isinstance(nested, int) else None


def is_hard_quota(exc: Exception | None) -> bool:
    """Return True if a 429 error represents a hard billing/quota cap rather than transient rate-limit."""
    if exc is None:
        return False
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        parts.append(str(body))
    low = " ".join(parts).casefold()
    return any(marker in low for marker in _HARD_QUOTA_MARKERS)


def extract_retry_after(exc: Exception | None) -> float | None:
    """Parse numeric Retry-After header (seconds, capped at 30s) from an SDK/HTTP exception."""
    if exc is None:
        return None
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers or not hasattr(headers, "get"):
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        secs = float(str(raw).strip())
        if secs >= 0:
            return min(30.0, secs)
    except (TypeError, ValueError):
        pass
    return None


def _is_transient_exception(exc: Exception | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__
    if name in _TRANSIENT_NAMES:
        return True
    low = str(exc).lower()
    return any(tok in low for tok in ("timeout", "timed out", "temporarily unavailable", "connection reset", "rate limit", "too many requests"))


def _backoff_wait(attempt: int, base_delay: float, backoff: float, jitter_max: float) -> float:
    if base_delay <= 0:
        return 0.0
    raw = min(30.0, float(base_delay) * (float(backoff) ** max(0, attempt - 1)))
    return min(30.0, raw + random.uniform(0.0, jitter_max))


def classify(
    status: int | None,
    error: Exception | None,
    attempt: int,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    retry_after: float | None = None,
) -> Verdict:
    """Decide whether to retry, switch to explicit fallback provider, or fail immediately."""
    if status in (401, 403):
        return Verdict(False, False, message="Το κλειδί API απορρίφθηκε από τον πάροχο. Άνοιξε τον Γιατρό ή έλεγξε τις Ρυθμίσεις.")
    if status == 402:
        return Verdict(False, True, message="Δεν υπάρχει διαθέσιμο υπόλοιπο στον πάροχο.")
    if status == 400:
        return Verdict(False, False, message="Ο πάροχος απέρριψε το αίτημα (πιθανόν πολύ μεγάλο context ή μη έγκυρη παράμετρος).")
    if status == 429:
        if is_hard_quota(error):
            return Verdict(False, True, message="Εξαντλήθηκε το όριο (quota) του παρόχου.")
        can_retry = attempt < max_attempts
        wait = retry_after if (retry_after is not None and base_delay > 0) else _backoff_wait(attempt, base_delay, backoff, 0.5)
        return Verdict(can_retry, not can_retry, wait=wait if can_retry else 0.0, message="Πάρα πολλές κλήσεις (429) στον πάροχο.")
    if (status in _TRANSIENT_STATUSES) or (status is None and _is_transient_exception(error)):
        can_retry = attempt < max_attempts
        wait = _backoff_wait(attempt, base_delay, backoff, 0.3)
        return Verdict(can_retry, not can_retry, wait=wait if can_retry else 0.0, message="Ο πάροχος δεν απαντά προσωρινά.")
    return Verdict(False, False)


def build_fallback_config(config):
    """Build a safe fallback Config if explicitly configured; never leak primary API key cross-host."""
    fb_model = str(getattr(config, "fallback_model", "") or "").strip()
    if not fb_model:
        return None
    base_url = str(getattr(config, "base_url", "") or "").strip()
    fb_url = str(getattr(config, "fallback_base_url", "") or "").strip() or base_url
    fb_key = str(getattr(config, "fallback_api_key", "") or "").strip()
    diff_host = bool(fb_url and fb_url != base_url)
    is_local = ("127.0.0.1" in fb_url) or ("localhost" in fb_url)
    if diff_host and not fb_key and not is_local:
        # Strict key isolation: never send primary provider's API key to another endpoint.
        return None
    effective_key = fb_key if diff_host else (fb_key or getattr(config, "api_key", ""))
    return dc_replace(
        config,
        model=fb_model,
        base_url=fb_url,
        api_key=effective_key,
        fallback_provider="",
        fallback_model="",
        fallback_base_url="",
        fallback_api_key="",
    )
