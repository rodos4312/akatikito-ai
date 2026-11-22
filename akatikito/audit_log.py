"""Tamper-evident hash-chained local audit log (.agent/audit.jsonl) per project."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .local_store import checked
from .secret_store import redact_secrets

GENESIS_HASH = "0" * 64
MAX_AUDIT_BYTES = 16 * 1024 * 1024
_AUDIT_LOCK = threading.RLock()


def _entry_hash(prev_hash: str, payload: dict) -> str:
    body = json.dumps({"prev": prev_hash, **payload}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only SHA-256 hash-chained audit trail for tool calls, edits, and approvals."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / ".agent" / "audit.jsonl"

    def _last_hash(self) -> str:
        if not self.path.is_file():
            return GENESIS_HASH
        last = GENESIS_HASH
        try:
            with self.path.open("rb") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line.decode("utf-8"))
                    if isinstance(row, dict) and isinstance(row.get("hash"), str):
                        last = row["hash"]
        except Exception:
            pass
        return last

    def record(
        self,
        action: str,
        *,
        tool: str = "",
        mode: str = "",
        status: str = "ok",
        paths: list[str] | tuple[str, ...] = (),
        summary: str = "",
        actor: str = "agent",
    ) -> dict:
        with _AUDIT_LOCK:
            checked(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_file() and self.path.stat().st_size > MAX_AUDIT_BYTES:
                rotated = self.path.with_name(f"audit-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
                self.path.replace(rotated)
            prev = self._last_hash()
            payload = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "actor": str(actor)[:40],
                "action": str(action)[:60],
                "tool": str(tool)[:80],
                "mode": str(mode)[:30],
                "status": str(status)[:30],
                "paths": [str(p)[:300] for p in list(paths)[:20]],
                "summary": redact_secrets(str(summary or "")[:400]),
            }
            digest = _entry_hash(prev, payload)
            row = {"prev": prev, **payload, "hash": digest}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return row

    def entries(self, limit: int = 100, query: str = "") -> list[dict]:
        if not self.path.is_file():
            return []
        q = str(query or "").strip().lower()
        rows: list[dict] = []
        with _AUDIT_LOCK:
            try:
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if q and q not in json.dumps(row, ensure_ascii=False).lower():
                        continue
                    rows.append(row)
            except (OSError, ValueError):
                return []
        return rows[-max(1, min(500, int(limit))) :]

    def verify_chain(self) -> dict:
        """Verify cryptographic integrity of the entire audit chain."""
        if not self.path.is_file():
            return {"valid": True, "count": 0, "error": ""}
        prev = GENESIS_HASH
        count = 0
        with _AUDIT_LOCK:
            try:
                for idx, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get("prev") != prev:
                        return {"valid": False, "count": count, "error": f"Σπασμένη αλυσίδα στη γραμμή {idx} (prev mismatch)."}
                    payload = {k: row[k] for k in ("ts", "actor", "action", "tool", "mode", "status", "paths", "summary") if k in row}
                    expected = _entry_hash(prev, payload)
                    if row.get("hash") != expected:
                        return {"valid": False, "count": count, "error": f"Αλλοιωμένη εγγραφή στη γραμμή {idx} (hash mismatch)."}
                    prev = expected
                    count += 1
            except Exception as exc:
                return {"valid": False, "count": count, "error": f"Μη έγκυρο αρχείο audit: {exc}"}
        return {"valid": True, "count": count, "error": ""}
