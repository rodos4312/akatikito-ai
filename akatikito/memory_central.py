"""The agent's door to the central memory (MEMORY-PLAN M5).

Per task the agent gets two observations, both sent as application state next to the
conversation, never inside the cached system prompt:

- the owner's profile core (``kind=core``), ordered by importance and freshness, cut to a budget;
- the other memories of this project and the global ones: all of them while they fit the budget
  (exactly today's behaviour for a small memory), otherwise only those the task needs (recall).

The legacy files stay where notes are written and approved; ``refresh`` mirrors them into the
store first (cheap when nothing changed). Any storage failure returns ``None`` so the caller keeps
the legacy excerpts: a broken index must never cost the owner their memory.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import memory
from .memory_import import project_scope, sync_global, sync_project
from .memory_recall import HEADER, KIND_LABELS, recall
from .memory_store import MemoryStore

log = logging.getLogger(__name__)
FAILURES = (sqlite3.Error, OSError, ValueError)


@contextmanager
def opened():
    """The store of the current memory folder, open only while it is used.

    No handle outlives a call: on Windows an open ``memory.db`` would block checkpoint restore,
    backups and uninstall. ``memory.DIR`` is read each time, so tests and profiles can move it."""
    s = MemoryStore(Path(memory.DIR) / "memory.db")
    try:
        yield s
    finally:
        s.close()


def refresh(s: MemoryStore, project_root: Path | str | None) -> MemoryStore:
    sync_global(s, Path(memory.DIR))
    if project_root is not None:
        sync_project(s, Path(project_root))
    return s


def _line(row: dict) -> str:
    label = KIND_LABELS.get(row["kind"], row["kind"])
    topic = f"{row['topic']}: " if row["topic"] else ""
    return f"- ({label}) {topic}{row['text']}"


def _fit(rows: list[dict], max_chars: int) -> tuple[list[str], int]:
    lines, size = [], 0
    for row in rows:
        line = _line(row)
        if size + len(line) + 1 > max_chars:
            continue
        lines.append(line)
        size += len(line) + 1
    return lines, len(rows) - len(lines)


def _rows(s: MemoryStore, scope: str, kinds: tuple[str, ...], cap: int) -> list[dict]:
    rows, offset = [], 0
    while len(rows) <= cap and (batch := s.active(scope=scope, limit=200, offset=offset)):
        rows.extend(r for r in batch if r["kind"] in kinds)
        offset += len(batch)
    return rows


def core_profile(s: MemoryStore, max_chars: int = 4000, question: str = "") -> tuple[str, str]:
    """``(shown, full)``: the profile core, and the whole of it when it had to be cut (else "").

    When everything fits it is shown whole. Otherwise the items the question needs come first, then
    the most important and freshest; ``full`` lets the caller keep an exact, retrievable copy."""
    rows = sorted(_rows(s, "global", ("core",), 500), key=lambda r: (-r["importance"], -r["updated_at"], r["id"]))
    full = "\n".join(_line(r) for r in rows)
    if len(full) <= max_chars:
        return full, ""
    relevant = [i.id for i in recall(s, question, limit=20, max_chars=10**6, touch=False).items
                if i.row["kind"] == "core"] if question.strip() else []
    order = {memory_id: rank for rank, memory_id in enumerate(relevant)}
    rows.sort(key=lambda r: order.get(r["id"], len(order)))
    lines, left = _fit(rows, max_chars)
    lines.append(f"(+{left} ακόμη στοιχεία προφίλ· ανακαλούνται όταν χρειαστούν)")
    return "\n".join(lines), full


@dataclass
class Observation:
    """What the agent is shown for one task, and which memories that was (for the chat's 🧠 line)."""

    profile: str
    memories: str
    full_profile: str = ""
    used: list = field(default_factory=list)
    mode: str = "all"  # "all": every memory fitted · "recall": only those the task needed
    fresh: bool = True  # False when the previous step's answer was reused


def memory_block(s: MemoryStore, question: str, project_root: Path | str | None,
                 max_chars: int = 3000) -> tuple[str, list[dict], str]:
    """``(block, used, mode)``: all project + global memories while they fit, else what ``question`` needs."""
    scope = project_scope(project_root) if project_root is not None else None
    kinds = ("fact", "belief", "episode")
    rows = (_rows(s, scope, kinds, 300) if scope else []) + _rows(s, "global", kinds, 300)
    if len(rows) <= 300:
        lines = [_line(r) for r in rows]
        if len(HEADER) + sum(len(line) + 1 for line in lines) <= max_chars:
            used = [{"id": r["id"], "text": r["text"], "why": []} for r in rows]
            return ("\n".join([HEADER, *lines]) if lines else ""), used, "all"
    result = recall(s, question, project_scope=scope, max_chars=max_chars)
    return result.text, [{"id": i.id, "text": i.row["text"], "why": i.why} for i in result.items], "recall"


_MEMO: dict[str, tuple[tuple, Observation]] = {}


def observations(question: str, project_root: Path | str | None, *, core_chars: int = 4000,
                 memory_chars: int = 3000) -> Observation | None:
    """What the agent sees for ``question``, or ``None`` when the store is unavailable.

    The agent asks before every model call of a task; the answer is reused until the task or a
    memory file changes, so a memory is counted as used once per task, not once per step."""
    try:
        with opened() as s:
            return _observations(refresh(s, project_root), question, project_root, core_chars, memory_chars)
    except FAILURES as exc:
        log.warning("Central memory unavailable, using the legacy memory files: %s", exc)
        return None


def _observations(s: MemoryStore, question: str, project_root, core_chars: int, memory_chars: int) -> Observation:
    scope = project_scope(project_root) if project_root is not None else ""
    key = (question, scope, s.meta("sync:global"), s.meta(f"sync:{scope}"), s.count(), core_chars, memory_chars)
    cached = _MEMO.get(str(s.path))
    if cached and cached[0] == key:
        return replace(cached[1], fresh=False)
    profile, full = core_profile(s, core_chars, question)
    block, used, mode = memory_block(s, question, project_root, memory_chars)
    result = Observation(profile, block, full, used, mode)
    _MEMO[str(s.path)] = (key, result)
    return result


def search(query: str, project_root: Path | str | None, limit: int = 8) -> list[dict]:
    """Owner/model search over every memory kind, with why each one matched."""
    scope = project_scope(project_root) if project_root is not None else None
    with opened() as s:
        result = recall(refresh(s, project_root), query, project_scope=scope, limit=max(1, min(20, int(limit))),
                        max_chars=20_000)
    return [{"id": i.id, "kind": i.row["kind"], "status": i.row["status"], "topic": i.row["topic"],
             "text": i.row["text"], "scope": "project" if i.row["scope"] != "global" else "global",
             "matched": i.why} for i in result.items]
