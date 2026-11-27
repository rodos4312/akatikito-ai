"""Live session context-window breakdown (/context, 0 tokens).

Estimates token consumption across system policy, tool schemas, memory/profile,
pinned files, and conversation history, plus lists the heaviest entries.
"""
from __future__ import annotations

import json
from typing import Any

from .context import clean, encoded

_BAR_WIDTH = 16


def _est_tokens(text: str) -> int:
    raw = len(str(text or "").encode("utf-8", errors="ignore"))
    return (raw + 3) // 4 if raw > 0 else 0


def _bar(pct: float) -> str:
    filled = max(0, min(_BAR_WIDTH, int(round(pct / 100.0 * _BAR_WIDTH))))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _msg_label(msg: dict[str, Any], idx: int) -> str:
    role = str(msg.get("role") or "msg")
    ctx_key = str(msg.get("_context_key") or "")
    if ctx_key:
        return f"#{idx} [{ctx_key}]"
    if role == "tool":
        tid = str(msg.get("tool_call_id") or "")[:12]
        return f"#{idx} tool({tid})"
    calls = msg.get("tool_calls") or []
    if calls:
        names = ", ".join(str((c.get("function") or {}).get("name") or "") for c in calls[:2])
        return f"#{idx} assistant → {names}"
    return f"#{idx} {role}"


def _measure_history(messages) -> tuple[int, int, list[tuple[int, str]]]:
    mem_tok = 0
    conv_tok = 0
    heaviest: list[tuple[int, str]] = []
    for idx, msg in enumerate(messages or ()):
        if idx == 0 and msg.get("role") == "system":
            continue
        tok = _est_tokens(encoded(clean(msg)))
        ck = str(msg.get("_context_key") or "")
        if ck in ("προτιμήσεις χρήστη", "μνήμες"):
            mem_tok += tok
        elif ck != "καρφιτσωμένα αρχεία":
            conv_tok += tok
            heaviest.append((tok, _msg_label(msg, idx)))
    heaviest.sort(key=lambda item: item[0], reverse=True)
    return mem_tok, conv_tok, heaviest[:3]


def _resolve_system_text(agent) -> str:
    if agent.messages and agent.messages[0].get("role") == "system":
        return str(agent.messages[0].get("content") or "")
    try:
        return agent.policy_prompt()
    except (OSError, ValueError, RuntimeError):
        return ""


def compute_context_breakdown(agent) -> dict[str, Any]:
    """Build a structured token breakdown for the active agent session."""
    from .tools import TOOL_SPECS

    sys_tok = _est_tokens(_resolve_system_text(agent))
    allowed = set(agent.allowed_tools())
    active_specs = [t for t in TOOL_SPECS if (t.get("function") or {}).get("name") in allowed]
    tools_tok = _est_tokens(json.dumps(active_specs, ensure_ascii=False))
    pins = getattr(agent, "pins", None)
    pins_tok = _est_tokens(pins.render(agent.ws) if pins is not None else "")
    mem_tok, conv_tok, heaviest = _measure_history(agent.messages)

    est_total = sys_tok + tools_tok + mem_tok + pins_tok + conv_tok
    real_prompt = int(getattr(agent, "_last_prompt_tokens", 0) or 0)
    window = int(
        getattr(agent.context, "native_context_tokens", 0)
        or getattr(getattr(agent, "model", None), "context_window_tokens", 0)
        or 0
    )
    reported_total = real_prompt if real_prompt > 0 else est_total
    occ_pct = round(100.0 * reported_total / window, 1) if window > 0 else 0.0
    return {
        "system_tokens": sys_tok,
        "tools_tokens": tools_tok,
        "memory_tokens": mem_tok,
        "pins_tokens": pins_tok,
        "conversation_tokens": conv_tok,
        "estimated_total": est_total,
        "provider_prompt_tokens": real_prompt,
        "window_tokens": window,
        "occupancy_pct": occ_pct,
        "pinned_files": pins.list_paths() if pins is not None else [],
        "heaviest": heaviest,
    }


def format_context_breakdown(agent) -> str:
    """Format a concise Greek /context report (0 LLM tokens)."""
    b = compute_context_breakdown(agent)
    tot = max(1, b["estimated_total"])
    lines = ["📊 **Ανάλυση Context** *(0 tokens)*"]
    if b["window_tokens"] > 0:
        used = b["provider_prompt_tokens"] or b["estimated_total"]
        src = "πραγματικά από πάροχο" if b["provider_prompt_tokens"] > 0 else "εκτίμηση"
        lines.append(
            f"Κατάληψη: `{_bar(b['occupancy_pct'])}` **{b['occupancy_pct']}%** "
            f"({used:,} / {b['window_tokens']:,} tokens · {src})"
        )
    else:
        lines.append(f"Σύνολο (εκτίμηση): **~{b['estimated_total']:,} tokens**")
    rows = (
        ("System & Κανόνες", b["system_tokens"]),
        ("Ορισμοί Εργαλείων", b["tools_tokens"]),
        ("Μνήμες & Προφίλ", b["memory_tokens"]),
        (f"Καρφιτσωμένα ({len(b['pinned_files'])})", b["pins_tokens"]),
        ("Ιστορικό Συνομιλίας", b["conversation_tokens"]),
    )
    for label, tok in rows:
        if tok == 0 and label.startswith(("Μνήμες", "Καρφιτσωμένα")):
            continue
        pct = round(100.0 * tok / tot, 1)
        lines.append(f"- {label}: **{tok:,}** tok ({pct}%)")
    if b["heaviest"]:
        lines.append("\n**Πιο βαριά μηνύματα στο ιστορικό:**")
        for tok, lbl in b["heaviest"]:
            lines.append(f"- `{lbl}` — ~{tok:,} tok")
    if b["occupancy_pct"] >= 70.0:
        lines.append("\n💡 Συμβουλή: πάτησε `/compact` για να ελευθερώσεις χώρο.")
    return "\n".join(lines)
