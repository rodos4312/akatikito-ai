"""Automatic & manual context compaction with lossless archival (Category 1).

Two-stage token reduction inspired by MEU's context compressor while preserving
Akatikito's lossless ``recall_context`` archive and valid tool-call pairs:
1. ``demote_stale_tool_outputs``: replaces bulky superseded/old tool outputs
   outside the recent tail with 1-line stubs pointing to ``recall_context``.
2. ``compact_conversation``: archives middle exchanges losslessly and replaces
   them with a structured summary (Fast/Active LLM with deterministic fallback),
   keeping head (system prompt) and tail (recent exchanges) intact.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace

from .context import ContextWindow, clean, encoded, owner

_DEMOTABLE_TOOLS = frozenset({
    "read_file", "run_command", "search_project", "search_in_file",
    "list_files", "web_fetch", "web_search", "browser_fetch",
    "read_document", "git_status_diff",
})

_COMPACT_PROMPT = (
    "Σύνοψισε τη μέχρι τώρα συνομιλία σε συμπαγή, ακριβή μορφή εργασίας στα ελληνικά "
    "(έως 25 γραμμές) με 4 ενότητες:\n"
    "1. Στόχος χρήστη\n"
    "2. Αλλαγμένα/δημιουργημένα αρχεία (ακριβείς διαδρομές)\n"
    "3. Κύρια ευρήματα, αποφάσεις & αποτελέσματα ελέγχων\n"
    "4. Τι απομένει\n"
    "Μην εφεύρεις γεγονότα. Κράτα μόνο επαληθευμένα στοιχεία."
)


def _call_target(call: dict) -> tuple[str, str]:
    fn = call.get("function") or {}
    name = str(fn.get("name") or "")
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    target = str(args.get("path") or args.get("command") or args.get("query") or args.get("url") or "")
    return name, target[:120]


def demote_stale_tool_outputs(
    messages: list[dict],
    context_window: ContextWindow,
    *,
    keep_recent_groups: int = 3,
    min_chars: int = 1200,
) -> tuple[list[dict], int]:
    """Replace bulky tool results older than the recent tail with lossless recall stubs."""
    groups = ContextWindow.groups(messages)
    cutoff = max(0, len(groups) - max(1, int(keep_recent_groups)))
    updated: list[dict] = []
    demoted = 0
    for idx, (_pos, group) in enumerate(groups):
        if idx >= cutoff or not group[0].get("tool_calls"):
            updated.extend(copy.deepcopy(group))
            continue
        call_info = {c["id"]: _call_target(c) for c in group[0]["tool_calls"]}
        new_group = [copy.deepcopy(group[0])]
        for msg in group[1:]:
            cloned = copy.deepcopy(msg)
            text = str(cloned.get("content") or "")
            name, target = call_info.get(cloned.get("tool_call_id"), ("", ""))
            if (
                name in _DEMOTABLE_TOOLS
                and len(text) >= min_chars
                and not text.startswith("[Συμπιεσμένη έξοδος")
            ):
                key = context_window.store(text)
                lines = text.count("\n") + 1
                label = f"{name} ({target})" if target else name
                cloned["content"] = (
                    f"[Συμπιεσμένη έξοδος εργαλείου {label} · {lines} γραμμές / {len(text)} chars "
                    f"· ακριβής ανάκτηση: recall_context id={key}]"
                )
                demoted += 1
            new_group.append(cloned)
        updated.extend(new_group)
    return updated, demoted


def _collect_middle_facts(middle_groups: list[tuple[int, list[dict]]], cw: ContextWindow) -> dict:
    goals, files, actions, answers = [], [], [], []
    seen_files: set[str] = set()
    for _pos, group in middle_groups:
        first = group[0]
        if owner(first):
            txt = str(first.get("content") or "").strip()
            if txt:
                goals.append(txt[:220])
        elif first.get("tool_calls"):
            results = {m.get("tool_call_id"): str(m.get("content") or "") for m in group[1:]}
            for m in group[1:]:
                for p in m.get("_written_paths") or ():
                    if p and p not in seen_files:
                        seen_files.add(p)
                        files.append(p)
            for call in first["tool_calls"]:
                name, target = _call_target(call)
                if name in ("recall_context", "remember", "set_mode"):
                    continue
                out = cw.outcome(results.get(call["id"], ""), 160).replace("\n", " ")
                actions.append(f"{name} {target} → {out}".strip())
        elif first.get("role") == "assistant" and not first.get("tool_calls"):
            txt = str(first.get("content") or "").strip()
            if txt:
                answers.append(txt[:280])
    return {"goals": goals[-6:], "files": files[:15], "actions": actions[-10:], "answers": answers[-2:]}


def build_deterministic_summary(
    middle_groups: list[tuple[int, list[dict]]],
    context_window: ContextWindow,
) -> tuple[str, str]:
    """Build a structured zero-API-cost summary and archive raw middle messages losslessly."""
    raw_middle = [clean(m) for _pos, group in middle_groups for m in group]
    archive_id = context_window.store(raw_middle)
    facts = _collect_middle_facts(middle_groups, context_window)
    sections = [f"[ΣΥΝΟΨΗ ΣΥΝΟΜΙΛΙΑΣ — αρχειοθετημένο ιστορικό recall_context id={archive_id}]"]
    if facts["goals"]:
        sections.append("Στόχοι χρήστη:\n" + "\n".join(f"- {g}" for g in facts["goals"]))
    if facts["files"]:
        sections.append("Αλλαγμένα αρχεία: " + ", ".join(facts["files"]))
    if facts["actions"]:
        sections.append("Ενέργειες & αποτελέσματα:\n" + "\n".join(f"- {a}" for a in facts["actions"]))
    if facts["answers"]:
        sections.append("Τελευταία συμπεράσματα:\n" + "\n".join(f"- {a}" for a in facts["answers"]))
    sections.append(f"Για πλήρη λεπτομέρεια παλαιότερων βημάτων χρησιμοποίησε recall_context id={archive_id}.")
    return "\n\n".join(sections), archive_id


def _summarize_with_model(agent, digest_input: str) -> str:
    """Attempt concise LLM compaction via Fast Model (if configured) or active model."""
    cfg = agent.config
    target_model = agent.model
    if getattr(cfg, "fast_model", "") and getattr(cfg, "fast_base_url", ""):
        from .model import OpenAIModel

        fast_cfg = replace(
            cfg,
            model=cfg.fast_model,
            base_url=cfg.fast_base_url,
            api_key=cfg.fast_api_key,
            temperature=0.0,
        )
        target_model = OpenAIModel(fast_cfg)
    if target_model is None or not callable(getattr(target_model, "chat", None)):
        return ""
    prompt_msgs = [
        {"role": "system", "content": _COMPACT_PROMPT},
        {"role": "user", "content": digest_input[:24000]},
    ]
    try:
        reply, _usage = target_model.chat(prompt_msgs, tools=None, cache_key=None)
        return str((reply or {}).get("content") or "").strip()
    except Exception:  # quality: allow BLE001 — compaction must never break a user turn; deterministic fallback handles model failures.
        return ""


def _record_compaction_metrics(agent, before_chars: int, after_chars: int, archive_id: str, method: str, reason: str) -> dict:
    saved_pct = round(max(0.0, (1.0 - after_chars / max(1, before_chars)) * 100.0), 1)
    active_bytes = len(encoded([clean(m) for m in agent.messages]).encode("utf-8"))
    budget = max(8000, int(getattr(agent.context, "active_max_bytes", 0) or agent.config.context_active_max_bytes or 160000))
    agent.context.metrics.update({
        "original_chars": before_chars,
        "sent_chars": after_chars,
        "active_bytes": active_bytes,
        "active_budget_bytes": budget,
        "reduction_percent": saved_pct,
        "over_budget": active_bytes > budget,
        "compactions": int(agent.context.metrics.get("compactions", 0)) + 1,
    })
    payload = {
        "ok": True,
        "compacted": True,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "saved_pct": saved_pct,
        "archive_id": archive_id,
        "method": method,
        "reason": reason,
    }
    # Clear authoritative pre-compaction prompt token watermark so the next step doesn't re-trigger.
    agent._last_prompt_tokens = 0
    agent.events.emit("compacted", **payload)
    return payload


def compact_conversation(
    agent,
    *,
    use_llm: bool = True,
    keep_tail_groups: int = 3,
    reason: str = "manual",
) -> dict:
    """Compact middle conversation groups into a lossless-archived structured summary."""
    messages = list(agent.messages or [])
    before_chars = len(encoded([clean(m) for m in messages]))
    if len(messages) < 3:
        return {"ok": True, "compacted": False, "before_chars": before_chars, "after_chars": before_chars, "saved_pct": 0.0, "reason": "too_short"}

    groups = ContextWindow.groups(messages)
    head_groups = groups[:1] if groups and groups[0][1][0].get("role") == "system" else []
    rest = groups[len(head_groups):]
    tail_n = max(1, int(keep_tail_groups))

    if len(rest) <= tail_n + 1:
        demoted_msgs, count = demote_stale_tool_outputs(messages, agent.context, keep_recent_groups=2)
        after_chars = len(encoded([clean(m) for m in demoted_msgs]))
        if count > 0 and after_chars < before_chars:
            agent.messages = demoted_msgs
            agent.context.reset_transport()
            return _record_compaction_metrics(agent, before_chars, after_chars, "", "demote", reason)
        return {"ok": True, "compacted": False, "before_chars": before_chars, "after_chars": before_chars, "saved_pct": 0.0, "reason": "already_compact"}

    middle_groups = rest[:-tail_n]
    tail_groups = rest[-tail_n:]
    det_summary, archive_id = build_deterministic_summary(middle_groups, agent.context)
    method = "deterministic"
    summary_text = det_summary
    if use_llm:
        llm_text = _summarize_with_model(agent, det_summary)
        if llm_text and len(llm_text) >= 40:
            summary_text = (
                f"[ΣΥΝΟΨΗ ΣΥΝΟΜΙΛΙΑΣ — αρχειοθετημένο ιστορικό recall_context id={archive_id}]\n"
                f"{llm_text}\n\n"
                f"Για πλήρη λεπτομέρεια παλαιότερων βημάτων χρησιμοποίησε recall_context id={archive_id}."
            )
            method = "llm"

    head_msgs = [copy.deepcopy(m) for _p, g in head_groups for m in g]
    tail_msgs = [copy.deepcopy(m) for _p, g in tail_groups for m in g]
    summary_msg = {"role": "user", "content": summary_text, "_origin": "internal", "_compaction_id": archive_id}
    rebuilt = head_msgs + [summary_msg] + tail_msgs
    ContextWindow.groups(rebuilt)  # Verify invariant before mutating live history.
    after_chars = len(encoded([clean(m) for m in rebuilt]))
    if after_chars >= before_chars and reason == "auto":
        return {"ok": True, "compacted": False, "before_chars": before_chars, "after_chars": before_chars, "saved_pct": 0.0, "reason": "no_gain"}

    agent.messages = rebuilt
    agent.context.reset_transport()
    return _record_compaction_metrics(agent, before_chars, after_chars, archive_id, method, reason)


def _auto_compact_trigger(agent) -> tuple[int, bool] | None:
    """Return (byte_trigger, should_compact) or None when no active budget/window is set."""
    cfg = agent.config
    explicit_budget = int(getattr(agent.context, "active_max_bytes", 0) or cfg.context_active_max_bytes or 0)
    native_ctx = int(
        getattr(agent.context, "native_context_tokens", 0)
        or getattr(getattr(agent, "model", None), "context_window_tokens", 0)
        or 0
    )
    if explicit_budget <= 0 and native_ctx <= 0:
        return None
    budget = max(8000, explicit_budget or (native_ctx * 3))
    threshold = max(0.3, min(0.95, float(getattr(cfg, "auto_compact_threshold", 0.80))))
    trigger = int(budget * threshold)
    current_bytes = len(encoded([clean(m) for m in agent.messages]).encode("utf-8"))
    real_prompt = int(getattr(agent, "_last_prompt_tokens", 0) or 0)
    token_triggered = bool(real_prompt > 0 and native_ctx > 0 and real_prompt >= int(native_ctx * threshold))
    return trigger, (current_bytes >= trigger or token_triggered)


def maybe_auto_compact(agent) -> dict | None:
    """Automatically demote stale tool outputs or compact history when crossing threshold."""
    cfg = agent.config
    if not getattr(cfg, "context_economy", True) or not getattr(cfg, "auto_compact", True):
        return None
    if not agent.messages or len(agent.messages) < 4:
        return None
    check = _auto_compact_trigger(agent)
    if check is None or not check[1]:
        return None
    trigger = check[0]
    before_chars = len(encoded([clean(m) for m in agent.messages]))

    # Stage 1: lossless stale tool-output demotion (0 LLM calls).
    demoted_msgs, count = demote_stale_tool_outputs(agent.messages, agent.context, keep_recent_groups=3)
    if count > 0:
        new_bytes = len(encoded([clean(m) for m in demoted_msgs]).encode("utf-8"))
        if new_bytes < int(trigger * 0.85):
            agent.messages = demoted_msgs
            agent.context.reset_transport()
            after_chars = len(encoded([clean(m) for m in demoted_msgs]))
            return _record_compaction_metrics(agent, before_chars, after_chars, "", "demote", "auto")
        agent.messages = demoted_msgs

    # Stage 2: full structured compaction (Fast/Active model with deterministic fallback).
    return compact_conversation(agent, use_llm=True, keep_tail_groups=3, reason="auto")
