"""One-shot turn auto-recovery: orphan tool-call healing, context-overflow & image recovery (Category 2).

Inspired by MEU's ``turn_recovery.py``: instead of failing a user turn on recoverable
400/413/422 provider errors or interrupted tool batches, repair the conversation state
once per error class and retry the step cleanly.
"""
from __future__ import annotations

import re

from .compaction import compact_conversation

_CONTEXT_OVERFLOW_RE = re.compile(
    r"context[_\s-]*length[_\s-]*exceeded|maximum context length|prompt is too long|"
    r"too many tokens|exceeds (?:the )?(?:maximum )?(?:context|token) (?:window|limit)|"
    r"input is too long|request too large",
    re.I,
)
_IMAGE_REJECTED_RE = re.compile(
    r"invalid[_\s-]*image|corrupt(?:ed)?\s+image|does not support image|"
    r"image.*(?:too large|max allowed size|unsupported|cannot be processed|dimension)|"
    r"patches after processing|vision.*not supported|multimodal.*not supported",
    re.I,
)
_ORPHAN_TOOL_RE = re.compile(
    r"tool_call_id|must be followed by tool messages|preceeding message with .?tool_calls.?|"
    r"preceding message with .?tool_calls.?|missing tool(?:_call)? result|orphan.*tool",
    re.I,
)
_OUTPUT_TRUNCATED_RE = re.compile(
    r"δεν ολοκληρώθηκε κανονικά\s*\(length\)|finish_reason['\":=\s]+length|"
    r"ελλιπή JSON ορίσματα εργαλείου|max_tokens",
    re.I,
)


def close_interrupted_tool_sequence(messages: list[dict]) -> int:
    """Repair interrupted tool sequences in place so provider tool-call protocols stay valid.

    - Inserts synthetic tool responses for any assistant ``tool_calls`` missing their ``tool`` message.
    - Drops stray ``tool`` messages that have no matching preceding assistant ``tool_call``.
    Returns the number of repairs performed.
    """
    if not messages:
        return 0
    repaired: list[dict] = []
    fixes = 0
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        role = msg.get("role")
        calls = msg.get("tool_calls") or []
        if role == "assistant" and calls:
            repaired.append(msg)
            expected_ids = [c.get("id") for c in calls if isinstance(c, dict) and c.get("id")]
            expected_set = set(expected_ids)
            seen_ids: set[str] = set()
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                tmsg = messages[j]
                tid = tmsg.get("tool_call_id")
                if tid in expected_set and tid not in seen_ids:
                    repaired.append(tmsg)
                    seen_ids.add(tid)
                else:
                    fixes += 1  # Drop duplicate or mismatched stray tool result.
                j += 1
            for missing_id in expected_ids:
                if missing_id not in seen_ids:
                    repaired.append({
                        "role": "tool",
                        "tool_call_id": missing_id,
                        "content": "[Η εκτέλεση του εργαλείου διακόπηκε πριν ολοκληρωθεί.]",
                    })
                    fixes += 1
            i = j
            continue
        if role == "tool":
            # Orphan tool message with no preceding assistant tool_calls.
            fixes += 1
            i += 1
            continue
        repaired.append(msg)
        i += 1
    if fixes:
        messages[:] = repaired
    return fixes


def strip_images_from_messages(messages: list[dict]) -> int:
    """Replace multimodal image blocks with a safe text placeholder in place."""
    removed = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_blocks = []
        had_image = False
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("image_url", "image", "input_image"):
                removed += 1
                had_image = True
            else:
                new_blocks.append(block)
        if had_image:
            new_blocks.append({
                "type": "text",
                "text": "[Η εικόνα αφαιρέθηκε αυτόματα επειδή απορρίφθηκε από τον πάροχο/μοντέλο.]",
            })
            msg["content"] = new_blocks
    return removed


def classify_recoverable_error(exc: BaseException) -> str | None:
    """Classify a model error into a one-shot recovery kind, or None if unrecoverable."""
    text = f"{type(exc).__name__}: {exc}"
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        text += f" | {type(cause).__name__}: {cause}"
    if _ORPHAN_TOOL_RE.search(text):
        return "orphan_tool_sequence"
    if _OUTPUT_TRUNCATED_RE.search(text):
        return "output_truncated"
    if _CONTEXT_OVERFLOW_RE.search(text):
        return "context_overflow"
    if _IMAGE_REJECTED_RE.search(text):
        return "image_rejected"
    return None


def _recover_output_truncated(agent) -> tuple[bool, str]:
    close_interrupted_tool_sequence(agent.messages)
    agent.context.reset_transport()
    steer = (
        "[ΑΥΤΟΜΑΤΗ ΑΝΑΚΤΗΣΗ — ΟΡΙΟ ΕΞΟΔΟΥ (length)] Η προηγούμενη απάντηση ή κλήση εργαλείου "
        "κόπηκε επειδή ξεπέρασε το μέγιστο μέγεθος εξόδου σε ένα βήμα. "
        "ΜΗΝ προσπαθήσεις να γράψεις ολόκληρο μεγάλο αρχείο σε ένα μόνο write_file. "
        "Γράψε πρώτα έναν καθαρό, λειτουργικό σκελετό (<= 250 γραμμές) και πρόσθεσε "
        "τα υπόλοιπα τμήματα σταδιακά με edit_file ή replace_symbol."
    )
    agent.messages.append({"role": "user", "content": steer, "_origin": "internal"})
    detail = "Η απάντηση κόπηκε στο όριο εξόδου (length) — αυτόματη συνέχιση σε μικρότερα βήματα."
    agent.events.emit("recovery", recovery_kind="output_truncated", text=detail)
    return True, detail


def attempt_turn_recovery(agent, exc: BaseException, attempted: set[str]) -> tuple[bool, str]:
    """Try a one-shot in-place state repair for a recoverable model error.

    Returns ``(recovered, detail)``. Each error class runs at most once per turn.
    """
    kind = classify_recoverable_error(exc)
    if kind is None or kind in attempted:
        return False, ""
    attempted.add(kind)

    if kind == "output_truncated":
        return _recover_output_truncated(agent)

    if kind == "orphan_tool_sequence":
        fixes = close_interrupted_tool_sequence(agent.messages)
        if fixes > 0:
            agent.context.reset_transport()
            detail = f"Επιδιορθώθηκαν {fixes} διακομμένες κλήσεις εργαλείων — επανάληψη βήματος."
            agent.events.emit("recovery", recovery_kind=kind, fixes=fixes, text=detail)
            return True, detail
        return False, ""

    if kind == "image_rejected":
        removed = strip_images_from_messages(agent.messages)
        if removed > 0:
            agent.context.reset_transport()
            detail = f"Αφαιρέθηκαν {removed} εικόνες που απέρριψε το μοντέλο — επανάληψη μόνο με κείμενο."
            agent.events.emit("recovery", recovery_kind=kind, removed=removed, text=detail)
            return True, detail
        return False, ""

    if kind == "context_overflow":
        res = compact_conversation(agent, use_llm=False, keep_tail_groups=2, reason="recovery")
        if res.get("compacted"):
            detail = (
                f"Επείγουσα συμπίεση context ({res['before_chars']} → {res['after_chars']} chars, "
                f"-{res['saved_pct']}%) — επανάληψη βήματος."
            )
            agent.events.emit("recovery", recovery_kind=kind, **res, text=detail)
            return True, detail
        return False, ""

    return False, ""
