"""Spill oversized tool outputs to .agent/outputs/ and return a compact summary (PRO-PLAN B8)."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from . import redact

MAX_INLINE_CHARS = 16_000
MAX_INLINE_LINES = 300
HEAD_LINES = 40
TAIL_LINES = 20
MAX_KEPT_FILES = 50
_EXEMPT_TOOLS = frozenset({"read_file", "recall_context", "use_skill", "propose_skill", "patch_skill"})
_STATUS_PREFIXES = ("returncode=", "ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")


def _format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def _prune_outputs_dir(out_dir: Path, keep: int = MAX_KEPT_FILES) -> None:
    try:
        files = sorted(out_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def spill_large_output(
    workspace_root: Path | str,
    tool_name: str,
    result: str,
    *,
    max_chars: int = MAX_INLINE_CHARS,
    max_lines: int = MAX_INLINE_LINES,
) -> str:
    """Save large tool text outputs to .agent/outputs/ and return head/tail summary + path."""
    if type(result) is not str or tool_name in _EXEMPT_TOOLS:
        return result
    lines = result.splitlines()
    if len(result) <= max_chars and len(lines) <= max_lines:
        return result

    root = Path(workspace_root)
    out_dir = root / ".agent" / "outputs"
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(tool_name or "tool")).strip("_")[:32] or "tool"
    filename = f"{safe_name}-{int(time.time())}-{uuid.uuid4().hex[:4]}.txt"
    rel_path = f".agent/outputs/{filename}"
    redacted_full = redact.redact_all(result)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_bytes = redacted_full.encode("utf-8", errors="replace")
        (out_dir / filename).write_bytes(raw_bytes)
        _prune_outputs_dir(out_dir)
    except OSError:
        return result

    r_lines = redacted_full.splitlines()
    status_line = ""
    body_lines = r_lines
    if r_lines and r_lines[0].startswith(_STATUS_PREFIXES):
        status_line = r_lines[0]
        body_lines = r_lines[1:]

    head = "\n".join(body_lines[:HEAD_LINES])
    tail = "\n".join(body_lines[-TAIL_LINES:]) if len(body_lines) > HEAD_LINES else ""
    header = f"Αποτέλεσμα: {len(r_lines):,} γραμμές ({_format_size(len(raw_bytes))}) — αποθηκεύτηκε στο {rel_path}"
    parts = [status_line] if status_line else []
    parts.append(header)
    parts.append(f"Πρώτες {min(HEAD_LINES, len(body_lines))} γραμμές:\n{head}")
    if tail:
        parts.append(f"Τελευταίες {min(TAIL_LINES, len(body_lines))} γραμμές:\n{tail}")
    parts.append(f"Χρησιμοποίησε search_in_file ή read_file με start_line/end_line στο {rel_path} για τα υπόλοιπα.")
    return "\n\n".join(parts)
