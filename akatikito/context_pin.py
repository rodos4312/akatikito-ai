"""Per-session Context Pinning: keep up to 3 critical workspace files live across compactions.

Pinned files are re-read fresh from disk on every turn via observe_request_state so:
1. Auto/manual compaction NEVER loses critical specs, plans, or schemas.
2. Edits to a pinned file are reflected immediately on the next step.
3. Zero overhead (0 bytes) when no files are pinned.
"""
from __future__ import annotations

from contextlib import suppress
import re
import threading
from pathlib import Path

from . import redact
from .workspace import Workspace

MAX_PINNED_FILES = 3
MAX_PER_FILE_CHARS = 8_000
MAX_TOTAL_PIN_CHARS = 16_000
_SENSITIVE_NAMES = frozenset({".env", "settings.json", "id_rsa", "id_ed25519"})
_PIN_CMD_RE = re.compile(r"^\s*/(pins|pin|unpin)\b(.*)$", re.I | re.S)


def _is_sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return True
    return name.endswith((".pem", ".key", ".p12", ".pfx"))


class ContextPinStore:
    """Bounded per-session list of pinned workspace-relative file paths."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: list[str] = []

    def list_paths(self) -> list[str]:
        with self._lock:
            return list(self._paths)

    def clear(self) -> int:
        with self._lock:
            n = len(self._paths)
            self._paths.clear()
            return n

    def pin(self, ws: Workspace, raw_path: str) -> str:
        text = str(raw_path or "").strip().strip("\"'")
        if not text:
            return "ΣΦΑΛΜΑ: δώσε διαδρομή αρχείου (π.χ. /pin PLAN.md)."
        target = ws.resolve(text)
        if _is_sensitive_path(target):
            return "ΑΠΟΡΡΙΨΗ: δεν επιτρέπεται καρφίτσωμα αρχείων μυστικών/κλειδιών."
        if not target.is_file():
            return f"ΣΦΑΛΜΑ: το αρχείο '{text}' δεν βρέθηκε στο project."
        try:
            raw = target.read_bytes()
        except OSError as exc:
            return f"ΣΦΑΛΜΑ: αδυναμία ανάγνωσης '{text}' ({exc})."
        if b"\x00" in raw[:4096]:
            return "ΣΦΑΛΜΑ: δεν υποστηρίζεται καρφίτσωμα δυαδικού αρχείου."
        rel = ws.relative(target).replace("\\", "/")
        with self._lock:
            if rel in self._paths:
                return f"📌 Το «{rel}» είναι ήδη καρφιτσωμένο ({len(self._paths)}/{MAX_PINNED_FILES})."
            if len(self._paths) >= MAX_PINNED_FILES:
                return f"ΣΦΑΛΜΑ: όριο {MAX_PINNED_FILES} καρφιτσωμένων αρχείων ({', '.join(self._paths)}). Κάνε πρώτα /unpin."
            self._paths.append(rel)
            count = len(self._paths)
        return f"📌 Καρφιτσώθηκε το «{rel}» ({count}/{MAX_PINNED_FILES}) — θα παραμένει ζωντανό και μετά από compaction."

    def unpin(self, ws: Workspace, raw_path: str) -> str:
        text = str(raw_path or "").strip().strip("\"'")
        if not text or text.lower() in ("all", "clear", "*"):
            removed = self.clear()
            return f"📌 Ξεκαρφιτσώθηκαν όλα τα αρχεία ({removed})." if removed else "📌 Δεν υπήρχαν καρφιτσωμένα αρχεία."
        candidates = {text.replace("\\", "/")}
        with suppress(PermissionError, ValueError, OSError):
            candidates.add(ws.relative(ws.resolve(text)).replace("\\", "/"))
        with self._lock:
            match = next((p for p in self._paths if p in candidates or p.casefold() in {c.casefold() for c in candidates}), None)
            if match is None:
                return f"📌 Το «{text}» δεν είναι καρφιτσωμένο."
            self._paths.remove(match)
        return f"📌 Ξεκαρφιτσώθηκε το «{match}»."

    def render(self, ws: Workspace) -> str:
        """Render current on-disk contents of pinned files within strict char budget."""
        paths = self.list_paths()
        if not paths:
            return ""
        sections: list[str] = []
        used = 0
        for rel in paths:
            remaining = MAX_TOTAL_PIN_CHARS - used
            if remaining <= 200:
                break
            try:
                target = ws.resolve(rel)
                if not target.is_file():
                    sections.append(f"### 📌 {rel}\n(Το αρχείο δεν υπάρχει πλέον στον δίσκο.)")
                    continue
                content = target.read_text(encoding="utf-8-sig", errors="replace")
            except (PermissionError, OSError, ValueError):
                continue
            safe = redact.redact_all(content)
            limit = min(MAX_PER_FILE_CHARS, remaining)
            clipped = safe[:limit] + ("\n…[περικοπή στα όρια pin]" if len(safe) > limit else "")
            block = f"### 📌 {rel}\n{clipped}"
            sections.append(block)
            used += len(block)
        return "\n\n".join(sections)


def handle_pin_slash_command(agent, text: str) -> str | None:
    """Handle /pin, /unpin, /pins locally with zero LLM tokens; returns reply or None."""
    m = _PIN_CMD_RE.match(str(text or ""))
    if not m:
        return None
    cmd = m.group(1).lower()
    arg = (m.group(2) or "").strip()
    pins: ContextPinStore = agent.pins
    if cmd == "pins" or (cmd == "pin" and not arg):
        current = pins.list_paths()
        if not current:
            return "📌 Δεν υπάρχουν καρφιτσωμένα αρχεία. Χρήση: `/pin όνομα_αρχείου` (έως 3 αρχεία)."
        return "📌 Καρφιτσωμένα αρχεία:\n" + "\n".join(f"- `{p}`" for p in current)
    if cmd == "unpin" or (cmd == "pin" and arg.lower() in ("clear", "reset")):
        return pins.unpin(agent.ws, "" if arg.lower() in ("clear", "reset") else arg)
    return pins.pin(agent.ws, arg)


def pin_context_file(ws: Workspace, path: str = "", action: str = "pin") -> str:
    """Tool handler allowing the agent to pin, unpin, or list pinned files in the current session."""
    pins = getattr(ws, "context_pins", None)
    if pins is None:
        pins = ContextPinStore()
        ws.context_pins = pins
    act = str(action or "pin").strip().lower()
    if act == "list":
        items = pins.list_paths()
        return "Καρφιτσωμένα αρχεία: " + (", ".join(items) if items else "(κανένα)")
    if act == "unpin":
        return pins.unpin(ws, path)
    return pins.pin(ws, path)


CONTEXT_PIN_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "pin_context_file",
            "description": "Pin up to 3 key workspace files (e.g. specs, plans, schemas) so they stay live and fresh in context across auto/manual compactions. Actions: pin, unpin, list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path (required for pin; omit or 'clear' for unpin all)"},
                    "action": {"type": "string", "enum": ["pin", "unpin", "list"]},
                },
                "required": [],
            },
        },
    },
]

CONTEXT_PIN_HANDLERS = {
    "pin_context_file": pin_context_file,
}
