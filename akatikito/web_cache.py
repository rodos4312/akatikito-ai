"""Thread-safe single-flight TTL cache for public web_search and web_fetch results.

Only successful responses are cached. Concurrent identical calls (e.g. from parallel subtasks)
share a single per-key lock so only one HTTP request executes and waiters reuse the result.
"""
from __future__ import annotations

import copy
import re
import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

DEFAULT_TTL_SECONDS = 900.0  # 15 minutes
DEFAULT_MAX_ENTRIES = 128


def normalize_cache_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


class SingleFlightTTLCache:
    """In-memory bounded TTL cache with per-key single-flight locking."""

    def __init__(self, *, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._ttl = max(1.0, float(ttl_seconds))
        self._max = max(8, int(max_entries))
        self._lock = threading.Lock()
        self._entries: dict[tuple, tuple[float, Any]] = {}
        self._key_locks: dict[tuple, threading.Lock] = {}
        self.hits = 0
        self.misses = 0

    def _sweep_locked(self, now: float) -> None:
        expired = [k for k, (exp, _) in self._entries.items() if now >= exp]
        for k in expired:
            self._entries.pop(k, None)
        while len(self._entries) > self._max:
            oldest_key = min(self._entries.items(), key=lambda kv: kv[1][0])[0]
            self._entries.pop(oldest_key, None)

    def _flight_lock(self, key: tuple) -> threading.Lock:
        with self._lock:
            kl = self._key_locks.get(key)
            if kl is None:
                if len(self._key_locks) > self._max * 2:
                    self._key_locks = {k: v for k, v in self._key_locks.items() if v.locked()}
                kl = self._key_locks[key] = threading.Lock()
            return kl

    def get(self, key: tuple, *, now: float | None = None) -> tuple[bool, Any]:
        clock = time.monotonic() if now is None else float(now)
        with self._lock:
            row = self._entries.get(key)
            if row is None or clock >= row[0]:
                self._entries.pop(key, None)
                return False, None
            self.hits += 1
            return True, copy.deepcopy(row[1])

    def put(self, key: tuple, value: Any, *, now: float | None = None) -> None:
        clock = time.monotonic() if now is None else float(now)
        with self._lock:
            self._entries[key] = (clock + self._ttl, copy.deepcopy(value))
            self._sweep_locked(clock)

    def get_or_fetch(
        self,
        key: tuple,
        fetcher: Callable[[], T],
        *,
        should_cache: Callable[[T], bool] | None = None,
    ) -> tuple[T, bool]:
        """Return (value, from_cache). Only caches when fetcher succeeds and should_cache(val) is True."""
        found, cached = self.get(key)
        if found:
            return cached, True
        with self._flight_lock(key):
            found, cached = self.get(key)
            if found:
                return cached, True
            with self._lock:
                self.misses += 1
            value = fetcher()
            if should_cache is None or should_cache(value):
                self.put(key, value)
            return value, False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._key_locks.clear()
            self.hits = 0
            self.misses = 0


WEB_FETCH_CACHE = SingleFlightTTLCache(ttl_seconds=DEFAULT_TTL_SECONDS, max_entries=DEFAULT_MAX_ENTRIES)
WEB_SEARCH_CACHE = SingleFlightTTLCache(ttl_seconds=DEFAULT_TTL_SECONDS, max_entries=DEFAULT_MAX_ENTRIES)


def clear_web_cache() -> None:
    WEB_FETCH_CACHE.clear()
    WEB_SEARCH_CACHE.clear()
