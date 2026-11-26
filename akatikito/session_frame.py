"""Session-level prompt-cache discipline: frozen SessionFrame & fingerprint guard (PRO-PLAN B2)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def _canonical_tools(tools: list[dict] | tuple[dict, ...]) -> tuple[dict, ...]:
    items = [t for t in (tools or ()) if isinstance(t, dict)]
    items.sort(key=lambda t: str((t.get("function") or {}).get("name") or t.get("name") or ""))
    return tuple(items)


@dataclass(frozen=True)
class SessionFrame:
    """Immutable snapshot of system prompt, sorted tool specs, and model pinned for a session."""

    system: str
    tools: tuple[dict, ...]
    model: str

    @classmethod
    def build(cls, system: str, tools: list[dict] | tuple[dict, ...], model: str) -> "SessionFrame":
        return cls(system=str(system or ""), tools=_canonical_tools(tools), model=str(model or ""))

    def fingerprint(self) -> str:
        blob = json.dumps(
            [self.system, list(self.tools), self.model],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class CacheGuard:
    """Freezes a SessionFrame per conversation; defers mid-session setting changes until next session."""

    def __init__(self) -> None:
        self._frame: SessionFrame | None = None
        self._pending_changes: list[str] = []
        self._breaks: int = 0

    @property
    def frame(self) -> SessionFrame | None:
        return self._frame

    def freeze(self, system: str, tools: list[dict] | tuple[dict, ...], model: str) -> SessionFrame:
        candidate = SessionFrame.build(system, tools, model)
        if self._frame is None:
            self._frame = candidate
        return self._frame

    def on_setting_change(
        self,
        label: str,
        *,
        apply_now: bool = False,
        new_system: str | None = None,
        new_tools: list[dict] | tuple[dict, ...] | None = None,
        new_model: str | None = None,
    ) -> dict:
        """Defer config/tool/skill changes to next session unless `apply_now=True` is explicitly requested."""
        if not apply_now or self._frame is None:
            if label not in self._pending_changes:
                self._pending_changes.append(label)
            return {
                "applied_now": False,
                "cache_broken": False,
                "fingerprint": self._frame.fingerprint() if self._frame else "",
                "notice": "Ισχύει από την επόμενη συζήτηση · [Εφαρμογή τώρα (νέα cache)]",
            }
        updated = SessionFrame.build(
            self._frame.system if new_system is None else new_system,
            self._frame.tools if new_tools is None else new_tools,
            self._frame.model if new_model is None else new_model,
        )
        changed = updated.fingerprint() != self._frame.fingerprint()
        self._frame = updated
        self._pending_changes.clear()
        if changed:
            self._breaks += 1
        return {
            "applied_now": True,
            "cache_broken": changed,
            "fingerprint": self._frame.fingerprint(),
            "notice": "Εφαρμόστηκε τώρα (νέα cache)." if changed else "Καμία αλλαγή στην cache.",
        }

    def pending_changes(self) -> list[str]:
        return list(self._pending_changes)

    def reset_for_new_session(self) -> None:
        self._frame = None
        self._pending_changes.clear()


def cache_hit_summary(prompt_tokens: int, cache_hit_tokens: int) -> dict:
    """Format provider cache-hit ratio for advanced UI display."""
    prompt = max(0, int(prompt_tokens or 0))
    hit = min(prompt, max(0, int(cache_hit_tokens or 0)))
    pct = round((hit / prompt) * 100.0, 1) if prompt > 0 else 0.0
    return {
        "prompt_tokens": prompt,
        "cache_hit_tokens": hit,
        "percent": pct,
        "label": f"cache: {int(round(pct))}% των tokens εισόδου",
    }
