"""Cheap content-sanity checks for degenerate model repetition loops & continuation stitching.

Inspired by MEU's ``repetition_guard.py`` & ``turn_truncation.py``:
- Detects when a model output is dominated (>= 50%) by repeated lines or repeated
  60-character fragments so we abort cleanly instead of continuing garbage.
- Cleanly stitches continuation fragments without duplicating seam overlap.
"""
from __future__ import annotations

import math
from collections import Counter

MIN_FRAGMENT_LENGTH = 400
_REPEAT_WINDOW = 60
_MIN_REPEAT_COUNT = 5
_DOMINANCE_RATIO = 0.5
RUNAWAY_STOP_MIN_CHARS = 16_000

REPETITION_ABORT_MESSAGE = (
    "⚠️ Η απάντηση σταμάτησε επειδή το μοντέλο μπήκε σε κύκλο επανάληψης.\n"
    "Ξαναστείλε το μήνυμα ή δοκίμασε άλλο μοντέλο — το ιστορικό σου παραμένει ανέπαφο."
)


def _line_repetition_dominated(text: str, total_len: int) -> bool:
    counts: Counter[str] = Counter()
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) >= 12:
            counts[line] += 1
    if not counts:
        return False
    top_line, top_count = counts.most_common(1)[0]
    if top_count < _MIN_REPEAT_COUNT:
        return False
    covered = len(top_line) * top_count
    return covered >= total_len * _DOMINANCE_RATIO


def _window_repetition_dominated(text: str, total_len: int) -> bool:
    window = _REPEAT_WINDOW
    if total_len < window * _MIN_REPEAT_COUNT:
        return False
    needed = max(_MIN_REPEAT_COUNT, math.ceil(total_len * _DOMINANCE_RATIO / window))
    counts: dict[str, int] = {}
    # Step by 4 chars for speed; exact repeated 60-char blocks still hit >= needed times.
    step = 4 if total_len > 4000 else 1
    scale = 1 / step
    target = max(_MIN_REPEAT_COUNT, math.ceil(needed * scale))
    for i in range(0, total_len - window + 1, step):
        key = text[i : i + window]
        seen = counts.get(key, 0) + 1
        if seen >= target:
            return True
        counts[key] = seen
    return False


def is_repetition_dominated(text: str) -> bool:
    """True when >= 50% of ``text`` consists of repeated lines or 60-char loops."""
    if not isinstance(text, str):
        return False
    n = len(text)
    if n < MIN_FRAGMENT_LENGTH:
        return False
    return _line_repetition_dominated(text, n) or _window_repetition_dominated(text, n)


def stitch_continuation(parts: list[str]) -> str:
    """Join continuation fragments while trimming small exact seam overlaps."""
    joined = ""
    for raw in parts:
        part = str(raw or "")
        if not part:
            continue
        if not joined:
            joined = part
            continue
        max_overlap = min(48, len(joined), len(part))
        overlap = 0
        for size in range(max_overlap, 3, -1):
            if joined.endswith(part[:size]):
                overlap = size
                break
        joined += part[overlap:]
    return joined
