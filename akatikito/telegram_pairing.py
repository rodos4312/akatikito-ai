"""One-time owner pairing for Telegram (6-digit code, TTL, brute-force cap)."""
from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from typing import Callable

PAIR_TTL_SECONDS = 600.0
PAIR_MAX_WRONG = 5
CODE_RE = re.compile(r"^(?:/start\s+)?(\d{6})$")


class TelegramPairing:
    """Issues a short-lived 6-digit pairing code to bind one owner user safely."""

    def __init__(
        self,
        *,
        ttl: float = PAIR_TTL_SECONDS,
        max_wrong: int = PAIR_MAX_WRONG,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = float(ttl)
        self.max_wrong = int(max_wrong)
        self.clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at: float = 0.0
        self._wrong: int = 0

    def issue(self) -> dict[str, object]:
        """Generate and return a fresh one-time 6-digit pairing code."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._code = code
            self._expires_at = self.clock() + self.ttl
            self._wrong = 0
        return {"code": code, "expires_in": int(self.ttl)}

    def cancel(self) -> None:
        with self._lock:
            self._code = None
            self._expires_at = 0.0
            self._wrong = 0

    def active_code(self) -> str | None:
        with self._lock:
            if self._code is None or self.clock() > self._expires_at:
                self._code = None
                return None
            return self._code

    def verify(self, text: str) -> bool:
        """Verify an incoming 6-digit code in constant time; burns on match, expiry, or max wrong guesses."""
        match = CODE_RE.match(str(text or "").strip())
        candidate = match.group(1) if match else ""
        with self._lock:
            if self._code is None:
                return False
            if self.clock() > self._expires_at:
                self._code = None
                return False
            if candidate and hmac.compare_digest(candidate, self._code):
                self._code = None
                self._wrong = 0
                return True
            self._wrong += 1
            if self._wrong >= self.max_wrong:
                self._code = None
            return False
