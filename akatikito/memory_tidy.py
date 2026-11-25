"""Tidy-up suggestions for the memory (MEMORY-PLAN M8). Suggests, never deletes.

- near duplicates (same kind, mostly the same words) → merge;
- memories nobody used for a long time → review;
- memories the owner marked unhelpful more often than helpful → review;
- memories naming a model provider other than the one in Settings → correct.
"""
from __future__ import annotations

import time
from pathlib import Path

from .memory_import import project_scope
from .memory_store import MemoryStore
from .memory_text import fold

STALE_DAYS = 180
PROVIDERS = ("deepseek", "openrouter", "openai", "anthropic", "claude", "gemini", "groq", "ollama", "mistral",
             "lmstudio", "lm studio", "together", "fireworks", "xai", "grok", "qwen", "kimi", "moonshot")
_PROVIDER_WORDS = ("παροχ", "provider", "μοντελ", "model")


def _words(text: str) -> set[str]:
    return {w for w in fold(text).split() if len(w) > 2}


def _all(s: MemoryStore, scopes: list[str], cap: int = 600) -> list[dict]:
    rows, offset = [], 0
    while len(rows) < cap and (page := s.active_in(scopes, limit=200, offset=offset)[0]):
        rows.extend(page)
        offset += len(page)
    return rows[:cap]


def _duplicates(rows: list[dict]) -> list[dict]:
    found, words = [], [(_words(r["text"]), r) for r in rows]
    for i, (a, ra) in enumerate(words):
        for b, rb in words[i + 1:]:
            if ra["kind"] != rb["kind"] or not a or not b:
                continue
            if len(a & b) / len(a | b) >= 0.75:
                found.append({"type": "duplicate", "ids": [ra["id"], rb["id"]], "texts": [ra["text"], rb["text"]],
                              "note": "Μοιάζουν πολύ· κράτησε μία ή γράψε μια ενιαία."})
                if len(found) >= 20:
                    return found
    return found


def _stale(rows: list[dict], now: float) -> list[dict]:
    limit = now - STALE_DAYS * 86400
    return [{"type": "stale", "ids": [r["id"]], "texts": [r["text"]],
             "note": f"Δεν χρησιμοποιήθηκε για πάνω από {STALE_DAYS} μέρες· ισχύει ακόμα;"}
            for r in rows if r["kind"] != "core" and max(r["last_used_at"] or 0, r["updated_at"]) < limit][:20]


def _unhelpful(rows: list[dict]) -> list[dict]:
    return [{"type": "unhelpful", "ids": [r["id"]], "texts": [r["text"]],
             "note": f"Σημειώθηκε άχρηστη {r['unhelpful']} φορές· να ξεχαστεί ή να διορθωθεί;"}
            for r in rows if r["unhelpful"] >= r["helpful"] + 2][:20]


def _conflicts(rows: list[dict], settings) -> list[dict]:
    current = fold(str(getattr(settings, "provider", "") or ""))
    if not current:
        return []
    found = []
    for r in rows:
        text = fold(r["text"])
        named = [p for p in PROVIDERS if p in text]
        if named and any(w in text for w in _PROVIDER_WORDS) and not any(p in current or current in p for p in named):
            found.append({"type": "conflict", "ids": [r["id"]], "texts": [r["text"]],
                          "note": f"Οι Ρυθμίσεις λένε πάροχος «{settings.provider}»· η μνήμη λέει άλλον."})
    return found[:20]


def suggestions(s: MemoryStore, root: Path | None, *, settings=None, now: float | None = None) -> list[dict]:
    scopes = ["global", project_scope(root)] if root is not None else ["global"]
    rows = _all(s, scopes)
    now = now or time.time()
    return _duplicates(rows) + _conflicts(rows, settings) + _unhelpful(rows) + _stale(rows, now)
