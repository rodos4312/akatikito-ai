"""Primary rate-limit cooldown & deterministic empty-response guard for OpenAIModel.

Prevents two costly failure loops:
1. 429 ping-pong: when the primary provider rate-limits (or hits hard quota) and we fall back,
   arm a session cooldown timer (honoring Retry-After or exponential 60s -> 120s -> ... -> 900s)
   so subsequent steps stay smoothly on fallback instead of hitting 429 on every single step.
2. Deterministic empty completions: two consecutive 0-output-token empty replies (no text, no
   tool calls, no reasoning) from the same (base_url, model) immediately route to fallback
   instead of repeatedly re-billing the full prompt.
"""
from __future__ import annotations

import math
import time
from typing import Any

_DEFAULT_COOLDOWN_BASE_S = 60.0
_MAX_COOLDOWN_S = 900.0
_MAX_RETRY_AFTER_S = 300.0
_EMPTY_STREAK_TRIGGER = 2


class RateLimitCooldown:
    """Monotonic cooldown tracker for a rate-limited primary model."""

    def __init__(self, *, base_seconds: float = _DEFAULT_COOLDOWN_BASE_S, max_seconds: float = _MAX_COOLDOWN_S) -> None:
        self._base = max(1.0, float(base_seconds))
        self._max = max(self._base, float(max_seconds))
        self._until: float = 0.0
        self._level: int = 0

    def arm(self, retry_after: float | None = None, *, now: float | None = None) -> float:
        """Arm cooldown using Retry-After (if positive) or exponential backoff; return seconds."""
        clock = time.monotonic() if now is None else float(now)
        if retry_after is not None and math.isfinite(retry_after) and retry_after > 0:
            delay = min(_MAX_RETRY_AFTER_S, math.ceil(float(retry_after)))
        else:
            delay = min(self._max, self._base * (2 ** self._level))
        self._level = min(self._level + 1, 8)
        self._until = max(self._until, clock + delay)
        return float(delay)

    def remaining(self, *, now: float | None = None) -> float:
        clock = time.monotonic() if now is None else float(now)
        return max(0.0, self._until - clock)

    def active(self, *, now: float | None = None) -> bool:
        return self.remaining(now=now) > 0.0

    def reset(self) -> None:
        self._until = 0.0
        self._level = 0


def is_zero_output_empty(reply: dict[str, Any] | None, usage: dict[str, Any] | None) -> bool:
    """True when a completion returned zero content, zero tool calls, zero reasoning, and 0 completion tokens."""
    if not isinstance(reply, dict):
        return False
    if str(reply.get("content") or "").strip():
        return False
    if reply.get("tool_calls"):
        return False
    if str(reply.get("reasoning") or "").strip() or str(reply.get("reasoning_content") or "").strip():
        return False
    comp_tokens = int((usage or {}).get("completion_tokens", 0) or 0)
    return comp_tokens <= 0


class EmptyResponseTracker:
    """Detects consecutive deterministic 0-output-token empty replies per (base_url, model)."""

    def __init__(self, *, trigger_streak: int = _EMPTY_STREAK_TRIGGER) -> None:
        self._trigger = max(2, int(trigger_streak))
        self._identity: tuple[str, str] | None = None
        self._streak: int = 0

    @property
    def streak(self) -> int:
        return self._streak

    def record(self, identity: tuple[str, str], reply: dict[str, Any] | None, usage: dict[str, Any] | None) -> bool:
        """Record a primary response; return True once deterministic-empty streak is reached."""
        if identity != self._identity:
            self._identity = identity
            self._streak = 0
        if not is_zero_output_empty(reply, usage):
            self._streak = 0
            return False
        self._streak += 1
        return self._streak >= self._trigger

    def reset(self) -> None:
        self._streak = 0
