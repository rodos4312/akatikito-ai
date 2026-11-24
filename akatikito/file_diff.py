"""Visual Diff helper: computes safe, bounded unified diffs against .agent_backups."""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .workspace import Workspace

MAX_DIFF_BYTES = 1024 * 1024  # 1 MiB
MAX_DIFF_LINES = 600


def _read_text_safe(path: Path) -> list[str]:
    if not path.is_file():
        return []
    size = path.stat().st_size
    if size > MAX_DIFF_BYTES:
        raise ValueError(f"Το αρχείο είναι πολύ μεγάλο για diff ({round(size / 1024)} KB, όριο 1 MB).")
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError("Δεν υποστηρίζεται οπτικό diff για δυαδικά (binary) αρχεία.")
    return raw.decode("utf-8", errors="replace").splitlines()


def compute_file_diff(root: str | Path, rel_path: str, backup_id: str = "") -> dict[str, Any]:
    """Compute a structured unified diff between the latest backup and the current file."""
    ws = Workspace(root)
    target = ws.resolve(rel_path)
    rel = ws.relative(target).replace("\\", "/")
    if not target.is_file():
        raise FileNotFoundError(f"Δεν βρέθηκε το αρχείο '{rel}'.")

    after_lines = _read_text_safe(target)
    backups = ws.list_backups(limit=500)
    chosen = None
    if backup_id:
        chosen = next((b for b in backups if b.get("backup") == backup_id), None)
    else:
        rel_cf = rel.casefold()
        for row in backups:
            if str(row.get("original", "")).replace("\\", "/").casefold() == rel_cf:
                cand = ws._backup_dir() / str(row.get("backup", ""))
                if cand.is_file():
                    chosen = row
                    break

    if chosen is not None:
        bak_path = ws._backup_dir() / str(chosen["backup"])
        before_lines = _read_text_safe(bak_path) if bak_path.is_file() else []
        from_label = f"πριν ({chosen.get('at', 'backup')})"
        is_new = False
        chosen_id = str(chosen["backup"])
        chosen_at = str(chosen.get("at", ""))
    else:
        before_lines = []
        from_label = "νέο αρχείο"
        is_new = True
        chosen_id = ""
        chosen_at = ""

    raw_diff = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel} ({from_label})",
            tofile=f"b/{rel} (τώρα)",
            lineterm="",
            n=3,
        )
    )

    added = 0
    removed = 0
    rows: list[dict[str, str]] = []
    truncated = len(raw_diff) > MAX_DIFF_LINES
    for line in raw_diff[:MAX_DIFF_LINES]:
        if line.startswith("+++") or line.startswith("---"):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
            added += 1
        elif line.startswith("-"):
            kind = "del"
            removed += 1
        else:
            kind = "ctx"
        rows.append({"kind": kind, "text": line})

    return {
        "ok": True,
        "path": rel,
        "backup_id": chosen_id,
        "backup_at": chosen_at,
        "is_new": is_new,
        "has_changes": bool(rows),
        "added": added,
        "removed": removed,
        "truncated": truncated,
        "lines": rows,
    }
