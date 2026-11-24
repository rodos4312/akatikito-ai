"""Parallel subtask coordination primitives: FileClaims & declared-files write guard.

Known boundary: declared-file guards cover workspace write/edit tools and native
output-writing plugins (PDF/image/ZIP). Shell commands via ``run_command`` are
serialized with a shared lock across concurrent subtasks, but shell I/O is not
sandboxed to the declared ``files`` list.
"""
from __future__ import annotations

import threading
from pathlib import Path


CHILD_FORBIDDEN_TOOLS = frozenset({
    "run_subtask",
    "run_subtasks",
    "propose_memory",
    "propose_skill",
    "patch_skill",
    "propose_global_memory",
})


def normalize_claim_path(ws, raw_path: str) -> str:
    """Normalize a declared workspace path to a canonical lowercase posix key."""
    resolved = ws.resolve(str(raw_path or "").strip())
    return ws.relative(resolved).replace("\\", "/").casefold()


class FileClaims:
    """Tracks exclusive file ownership across concurrent subtasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: dict[str, str] = {}  # normalized_rel_path -> child_id

    def try_claim(self, child_id: str, paths: list[str] | set[str]) -> bool:
        norm_paths = [str(p).replace("\\", "/").casefold() for p in paths if str(p).strip()]
        with self._lock:
            if any(self._owner.get(p) not in (None, child_id) for p in norm_paths):
                return False
            for p in norm_paths:
                self._owner[p] = child_id
            return True

    def release(self, child_id: str) -> None:
        with self._lock:
            self._owner = {p: o for p, o in self._owner.items() if o != child_id}


def make_declared_write_guard(ws, declared_files: set[str], parent_guard=None):
    """Wrap a workspace write_guard so a child subtask can only write to its declared files."""
    allowed = {normalize_claim_path(ws, f) for f in declared_files if str(f).strip()}

    def guard(path: Path, old_text: str = "", new_text: str = ""):
        rel = ws.relative(path).replace("\\", "/")
        if rel.casefold() not in allowed:
            raise PermissionError(
                f"ΑΠΟΡΡΙΨΗ: δεν δήλωσες το '{rel}' στα files της υποεργασίας."
            )
        if parent_guard is not None:
            return parent_guard(path, old_text, new_text)
        return None

    return guard
