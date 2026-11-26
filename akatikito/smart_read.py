"""Original Akatikito smart file-reading helpers: wide-range complete-line reading & typo suggestions.

Gives ``read_file``:
- ``suggest_similar_files``: fast fuzzy path suggestions when a requested file doesn't exist
  (avoids wasting an extra ``list_files`` round-trip).
- ``read_wide_lines``: reads up to 1,500 lines / 80,000 chars in one call when the caller
  explicitly requests ``full=True`` or a wide ``start..end`` span, always trimming cleanly
  at the last COMPLETE line with an exact ``start=<next_line>`` continuation hint.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from .workspace import Workspace

MAX_WIDE_READ_LINES = 1500
MAX_WIDE_READ_CHARS = 80_000
_IGNORED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".agent", ".agent_backups",
})
_MAX_SCAN_FILES = 1500


def _iter_workspace_files(root: Path) -> list[str]:
    found: list[str] = []
    stack = [root]
    while stack and len(found) < _MAX_SCAN_FILES:
        cur = stack.pop()
        try:
            entries = sorted(cur.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _IGNORED_DIRS:
                    stack.append(entry)
                continue
            try:
                found.append(entry.relative_to(root).as_posix())
            except ValueError:
                continue
            if len(found) >= _MAX_SCAN_FILES:
                break
    return found


def suggest_similar_files(ws: Workspace, missing_path: str, limit: int = 5) -> list[str]:
    """Return up to ``limit`` closest workspace paths for a mistyped file path."""
    query = str(missing_path or "").strip().replace("\\", "/").lstrip("./")
    if not query:
        return []
    q_low = query.lower()
    q_base = Path(q_low).name
    scored: list[tuple[float, str]] = []
    for rel in _iter_workspace_files(ws.root):
        r_low = rel.lower()
        r_base = Path(r_low).name
        base_score = difflib.SequenceMatcher(None, q_base, r_base).ratio()
        full_score = difflib.SequenceMatcher(None, q_low, r_low).ratio()
        score = max(base_score * 1.15, full_score)
        if q_base and q_base in r_base:
            score += 0.25
        if score >= 0.48:
            scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [rel for _, rel in scored[: max(1, int(limit))]]


def missing_file_message(ws: Workspace, path: str) -> str:
    """Build a Greek missing-file error with closest workspace path suggestions."""
    similar = suggest_similar_files(ws, path)
    hint = f" Μήπως εννοούσες: {', '.join(similar)};" if similar else " Έλεγξε τη διαδρομή."
    return f"ΣΦΑΛΜΑ: δεν βρήκα αρχείο '{path}'.{hint}"


def is_wide_read_request(start: int, end: int | None, anchor_line: int | None, full: bool, default_lines: int) -> bool:
    if full:
        return True
    if end is None or anchor_line is not None:
        return False
    return (int(end) - max(1, int(start)) + 1) > default_lines


def render_wide_read(
    rel_path: str,
    lines: list[str],
    start: int,
    end: int | None,
    max_lines: int = MAX_WIDE_READ_LINES,
    max_chars: int = MAX_WIDE_READ_CHARS,
) -> str:
    """Render a wide 1-based range trimmed strictly on complete line boundaries."""
    total = len(lines)
    first = max(1, int(start))
    if first > total:
        return f"ΣΦΑΛΜΑ: το αρχείο έχει μόνο {total} γραμμές."
    req_end = total if end is None else min(total, int(end))
    if req_end < first:
        return "ΣΦΑΛΜΑ: το 'end' είναι πριν το 'start'."
    cap_end = min(req_end, first + max_lines - 1)

    kept: list[str] = []
    running = 0
    truncated_by_chars = False
    for n in range(first, cap_end + 1):
        row = f"{n:>5} | {lines[n - 1]}"
        add = len(row) + (1 if kept else 0)
        if kept and running + add > max_chars:
            truncated_by_chars = True
            break
        if not kept and len(row) > max_chars:
            kept.append(row[:max_chars])
            truncated_by_chars = True
            break
        kept.append(row)
        running += add

    last_shown = first + len(kept) - 1
    head = f"{rel_path} — γραμμές {first}-{last_shown} από {total}:\n"
    body = "\n".join(kept)
    if last_shown < req_end or truncated_by_chars:
        reason = "όριο χαρακτήρων" if truncated_by_chars else "όριο γραμμών"
        note = (
            f"\n[Περικόπηκε καθαρά σε πλήρη γραμμή ({reason}, γραμμή {last_shown}/{total}). "
            f"Συνέχεια με read_file(path='{rel_path}', start={last_shown + 1}, full=true).]"
        )
        return head + body + note
    return head + body
