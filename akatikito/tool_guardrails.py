"""Per-turn tool loop guardrails: multi-step cycle detection & identical-result deduplication.

Inspired by MEU's ``tool_guardrails.py``:
- Replaces byte-identical large tool results (>= 512 chars, non-error) with a 1-line
  reference stub so the LLM never pays for the same large output repeatedly.
- Detects repeating multi-call cycles (A,B,A,B,A,B up to period 4) and excessive
  ``run_command`` bursts within one turn, appending a concise strategy-shift notice.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass

IDENTICAL_RESULT_STUB_MIN_CHARS = 512
_ARGS_PREVIEW_CHARS = 100
_MAX_CYCLE_PERIOD = 4
_CYCLE_LAPS = 3
_RUN_CMD_BURST_WARN = 8
_ERROR_PREFIXES = ("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ERROR", "returncode=1", "returncode=2")


@dataclass(frozen=True)
class GuardrailOutcome:
    content: str
    deduplicated: bool = False
    notice: str = ""
    kind: str = ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _args_sig(args: dict) -> tuple[str, str]:
    try:
        raw = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(args or "")
    preview = raw if len(raw) <= _ARGS_PREVIEW_CHARS else raw[:_ARGS_PREVIEW_CHARS] + "…"
    return _digest(raw), preview


def _is_error_result(text: str) -> bool:
    stripped = (text or "").lstrip()
    if any(stripped.startswith(p) for p in _ERROR_PREFIXES):
        return True
    if stripped.startswith("returncode=") and not stripped.startswith("returncode=0"):
        return True
    return False


class ToolLoopGuardrails:
    """Side-effect-free per-turn tracker for tool result dedup & cycle warnings."""

    def __init__(self, run_cmd_warn_at: int = _RUN_CMD_BURST_WARN) -> None:
        self._seen_results: dict[tuple[str, str], str] = {}
        self._history: deque[tuple[str, str, str]] = deque(maxlen=48)
        self._cycle_warned: set[tuple[str, ...]] = set()
        self._run_cmd_count: int = 0
        self._run_cmd_warn_at: int = max(2, int(run_cmd_warn_at))
        self._run_cmd_warned: bool = False

    def _detect_cycle(self) -> tuple[int, tuple[str, ...]] | None:
        hist = list(self._history)
        total = len(hist)
        for period in range(1, _MAX_CYCLE_PERIOD + 1):
            needed = period * _CYCLE_LAPS
            if total < needed:
                continue
            tail = hist[-needed:]
            unit = tail[:period]
            if all(tail[i] == unit[i % period] for i in range(period, needed)):
                sig = tuple(item[0] for item in unit)
                return period, sig
        return None

    def observe(self, name: str, args: dict, result: str) -> GuardrailOutcome:
        text = str(result or "")
        args_hash, args_preview = _args_sig(args)
        res_hash = _digest(text)
        err = _is_error_result(text)

        deduplicated = False
        out_text = text
        if not err and len(text) >= IDENTICAL_RESULT_STUB_MIN_CHARS:
            key = (name, res_hash)
            if key in self._seen_results:
                prev_args = self._seen_results[key]
                out_text = (
                    f"[Ίδιο αποτέλεσμα με προηγούμενο {name} ({prev_args}) — "
                    f"παραλείφθηκαν {len(text)} χαρακτήρες για εξοικονόμηση tokens.]"
                )
                deduplicated = True
            else:
                self._seen_results[key] = args_preview

        self._history.append((name, args_hash, res_hash))

        notices: list[str] = []
        kind = ""
        cycle = self._detect_cycle()
        if cycle is not None:
            period, sig = cycle
            if sig not in self._cycle_warned:
                self._cycle_warned.add(sig)
                kind = "cycle_warning"
                chain = " → ".join(sig)
                notices.append(
                    f"[ΠΡΟΕΙΔΟΠΟΙΗΣΗ ΚΥΚΛΟΥ] Επαναλαμβάνεις τον ίδιο κύκλο {period} βημάτων "
                    f"({chain}) με πανομοιότυπα αποτελέσματα. Άλλαξε προσέγγιση ή ολοκλήρωσε την απάντηση."
                )

        if name == "run_command":
            self._run_cmd_count += 1
            if self._run_cmd_count >= self._run_cmd_warn_at and not self._run_cmd_warned:
                self._run_cmd_warned = True
                kind = kind or "run_command_burst"
                notices.append(
                    f"[ΣΥΜΒΟΥΛΗ ΑΠΟΔΟΣΗΣ] Έχεις ήδη τρέξει {self._run_cmd_count} εντολές run_command "
                    "σε αυτό το βήμα. Ομαδοποίησε τις υπόλοιπες αλλαγές και κάνε έναν τελικό έλεγχο στο τέλος."
                )

        notice_text = "\n".join(notices)
        if notice_text:
            out_text = out_text.rstrip() + "\n\n" + notice_text
        return GuardrailOutcome(content=out_text, deduplicated=deduplicated, notice=notice_text, kind=kind)
