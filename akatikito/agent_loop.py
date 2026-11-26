"""Phased turn-loop execution for Akatikito._chat."""
from __future__ import annotations

import json
import re

from . import modes
from .agent_routing import apply_model_routing
from .execution import mutation_paths
from .intent import implementation_requested, normal, read_only
from .skills import Skills
from .tool_spill import spill_large_output
from .tools import TOOL_SPECS

ERROR_PREFIXES = ("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")
ACTION_TOOLS = (
    "add_reminder", "done_reminder", "propose_skill", "patch_skill",
    "create_word_document", "create_excel_sheet", "create_pdf_document", "convert_data_file",
)


def _parse_turn_intent(agent, text: str, internal: bool):
    is_slash_learn = not internal and bool(re.match(r"^\s*/learn\b", text or "", re.I))
    is_learn_intent = is_slash_learn or (
        not internal
        and bool(re.search(r"\b(?:φτιαξε|δημιουργησε|αποθηκευσε|κρατα|μαθε|κανε|προτεινε)\b.*\b(?:skill|συνταγη)\b", normal(text or "")))
    )
    agent.ws.learn_requested = is_learn_intent
    is_direct_skill = False
    direct_skill_info = None
    if is_slash_learn:
        learn_topic = re.sub(r"^\s*/learn\b", "", text or "", flags=re.I).strip()
        scope_text = (" για: " + learn_topic) if learn_topic else " από την τρέχουσα συνομιλία"
        text = (
            f"[ΕΝΤΟΛΗ ΕΚΜΑΘΗΣΗΣ /learn] Αποστάξε (distill) τη διαδικασία/λύση{scope_text} σε Skill.\n"
            "1) Έλεγξε πρώτα αν υπάρχει ήδη παρόμοιο skill (list_skills).\n"
            "2) Αν υπάρχει παρόμοιο skill, διάβασέ το με use_skill και βελτίωσέ το με patch_skill ή propose_skill.\n"
            "3) Αλλιώς, κάλεσε propose_skill με σαφή steps (3+ βήματα), category, triggers και notes (παγίδες/σημειώσεις)."
        )
    elif not internal:
        m_skill = re.match(r"^\s*/([a-z][a-z0-9-]{1,63})\b(.*)$", text or "", re.S)
        if m_skill:
            s_name, s_rest = m_skill.group(1), m_skill.group(2).strip()
            s_store = Skills(agent.ws.root)
            s_entry = s_store._entries().get(s_name)
            if s_entry is not None:
                is_direct_skill = True
                agent._used_skills_in_turn.add(s_name)
                direct_skill_info = (s_store, s_name, s_rest, s_entry.get("description", ""))
                text = s_rest or f"Εκτέλεσε τα βήματα της συνταγής /{s_name}: {s_entry.get('description', '')}"
    return text, is_slash_learn, is_learn_intent, is_direct_skill, direct_skill_info


def _reset_turn_state(agent, effective: str) -> None:
    agent._pending_owner_goal = ""
    agent._current_task = effective
    agent._pending_completion = None
    agent._trusted_blocker = ""
    agent._owner_denied = False
    agent._filesystem_failure = ""
    agent._task_writes, agent._task_reads = set(), set()
    agent._task_actions = set()
    agent._task_checks = agent._mutation_generation = 0
    agent._verified_generation = -1
    agent.subtasks = []
    agent._subtask_tokens_total = 0
    agent._turn_event_start = len(agent.events.log)
    agent.context.begin_turn()
    agent.activity.begin_turn()
    agent._stop = False
    agent._verify_failures = 0
    agent.repair.attempts = 0


def _append_turn_messages(agent, text: str, internal: bool, fresh: bool, can_repair: bool) -> None:
    if fresh:
        agent.messages.append({"role": "system", "content": agent.policy_prompt()})
    elif agent.messages[0].get("role") == "system":
        agent.messages[0] = {"role": "system", "content": agent.policy_prompt()}
    content = [{"type": "text", "text": text}] + agent._attachment_blocks if getattr(agent, "_attachment_blocks", None) else text
    agent.messages.append({"role": "user", "content": content, "_origin": "internal" if internal else "owner"})
    if agent.repair.pending and can_repair:
        agent._set_mode("debug", reason="συνέχεια εκκρεμούς διόρθωσης")
        agent.messages.append({
            "role": "user",
            "content": "[ΕΚΚΡΕΜΗΣ ΑΠΟΤΥΧΙΑ] " + agent.repair.terminal() + "\nΔιάβασε ξανά τα σχετικά αρχεία και συνέχισε τη διάγνωση.",
            "_origin": "internal",
        })


def _begin_turn(agent, text: str, internal: bool) -> tuple[str, bool]:
    with agent._sub_lock:
        agent._turn_id += 1
    agent.turn_usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0, "recall_calls": 0, "remember_calls": 0}
    agent._used_skills_in_turn = set()
    agent._skill_healing_hints = {}
    text, is_slash_learn, is_learn_intent, is_direct_skill, direct_skill_info = _parse_turn_intent(agent, text, internal)
    if internal:
        effective = agent.task.goal or agent.task.begin("Διόρθωσε την εκκρεμή αποτυχία του ελέγχου", agent.messages)
    else:
        effective = agent.task.begin(agent._pending_owner_goal or text, agent.messages)
    _reset_turn_state(agent, effective)
    fresh = not agent.messages
    if not fresh and not internal:
        from .compaction import maybe_auto_compact
        maybe_auto_compact(agent)
    if agent.config.auto_mode and not agent._manual_mode:
        agent._set_mode("assistant" if is_slash_learn else modes.classify(effective), reason="αυτόματα από την εργασία")
    apply_model_routing(agent, effective, fresh=fresh, is_direct_skill=is_direct_skill)
    agent._turn_tools = set(agent.allowed_tools())
    if read_only(effective) and not is_learn_intent and not is_direct_skill:
        agent._turn_tools &= set(modes.allowed_tools("ask"))
    agent.ws.turn_allowed_tools = agent._turn_tools
    if direct_skill_info is not None:
        s_store, s_name, s_rest, _ = direct_skill_info
        preloaded = s_store.read(s_name, available_tools=agent._turn_tools)
        text = (
            f"[ΑΜΕΣΗ ΕΚΤΕΛΕΣΗ SKILL /{s_name}]\n{preloaded}\n\n"
            f"Οδηγία χρήστη: {s_rest or ('Εκτέλεσε τα βήματα της συνταγής /' + s_name)}"
        )
    can_repair = {"edit_file", "run_command", "record_diagnosis"} <= agent._turn_tools
    agent._turn_requires_completion = bool(
        can_repair and implementation_requested(effective) and getattr(agent.model, "requires_completion_evidence", False)
    )
    _append_turn_messages(agent, text, internal, fresh, can_repair)
    agent.events.emit("user", text=text)
    agent.events.emit("mode", mode=agent.mode, label=modes.label(agent.mode), reason=agent.mode_reason)
    return effective, can_repair


def _track_cache_frame(agent, request: list[dict], tools: list[dict]) -> None:
    from .session_frame import SessionFrame

    guard = getattr(agent, "cache_guard", None)
    if guard is None:
        return
    sys_text = request[0].get("content", "") if request and request[0].get("role") == "system" else ""
    model_name = str(getattr(agent.config, "model", "") or "")
    if guard.frame is None:
        frame = guard.freeze(sys_text, tools, model_name)
        agent.cache_frame_fingerprint = frame.fingerprint()
        return
    current_fp = SessionFrame.build(sys_text, tools, model_name).fingerprint()
    if current_fp != agent.cache_frame_fingerprint:
        res = guard.on_setting_change(
            "policy_or_tools",
            apply_now=True,
            new_system=sys_text,
            new_tools=tools,
            new_model=model_name,
        )
        agent.cache_frame_fingerprint = res["fingerprint"]
        agent.events.emit("cache_notice", label="policy_or_tools", **res)


def _prepare_step_request(agent, step: int):
    agent.events.emit("step", step=step, of=agent.config.max_steps, phase="think")
    agent.events.emit("plan_step", step=step)
    allowed = set(agent.allowed_tools()) & agent._turn_tools
    agent.context.reasoning_identity = (agent.config.model, agent.config.base_url)
    continuous = bool(
        getattr(agent.model, "keeps_active_history", False) or getattr(agent.model, "preserves_tool_reasoning", False)
    )
    native = max(0, int(getattr(agent.model, "context_window_tokens", 0) or 0))
    max_out = max(0, int(getattr(agent.model, "max_output_tokens", 0) or 0))
    configured = max(0, int(agent.config.context_active_max_bytes))
    if continuous or native:
        # UTF-8 bytes conservatively bound text token counts; reserve room for tools/output.
        automatic = max(1024, int(native * 0.6)) if native else 160000
        agent.context.active_max_bytes = min(configured, automatic) if configured else automatic
        agent.context.native_context_tokens = native
    agent.context.max_output_tokens = max_out
    # Never reuse old permissions for the sake of a cache hit.
    policy = agent.policy_prompt()
    if agent.mode in ("code", "debug") and (native or max_out):
        from .model_limits import format_window_hint

        whint = format_window_hint(native, max_out)
        if whint:
            policy = policy + "\n\n" + whint
    agent.messages[0] = {"role": "system", "content": policy}
    from .turn_recovery import close_interrupted_tool_sequence
    if close_interrupted_tool_sequence(agent.messages):
        agent.context.reset_transport()
    agent._drain_steer()
    agent._observe_request_state()
    request = agent.context.prepare(agent.messages, agent.repair, agent.activity, continuous=continuous)
    agent.activity.update_visible(request)
    advertised = modes.select_ask_tools(agent._current_task, allowed, agent._ask_unlocked_tools) if agent.mode == "ask" else allowed
    mcp_specs = agent.mcp.specs() if getattr(agent, "mcp", None) else []
    tools = [t for t in TOOL_SPECS + mcp_specs if t["function"]["name"] in advertised]
    agent.events.emit("context", **agent.context.metrics)
    agent.events.emit("checkpoint")
    prev_ri = agent.runtime_instructions
    reason = getattr(agent, "before_model", lambda: "")()
    if reason:
        return None, None, agent._progress_report(reason)
    if agent.runtime_instructions != prev_ri:
        agent.messages[0] = {"role": "system", "content": agent.policy_prompt()}
        if request and request[0].get("role") == "system":
            request[0] = dict(agent.messages[0])
    _track_cache_frame(agent, request, tools)
    return request, tools, None


def _record_model_reply(agent, reply: dict, usage: dict, step: int) -> tuple[list[dict], str, bool]:
    for key in agent.tokens:
        agent.tokens[key] += usage.get(key, 0)
    agent.turn_usage["model_calls"] += 1
    agent.turn_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
    agent.turn_usage["completion_tokens"] += usage.get("completion_tokens", 0)
    getattr(agent, "usage_hook", lambda _u: None)(usage)
    if agent._stop:
        agent.events.emit("stopped", step=step)
        return [], "", True
    agent.events.emit("usage", step=step, **{k: usage.get(k, 0) for k in agent.tokens})
    reasoning = (reply.get("reasoning") or "").strip()
    if reasoning:
        agent.events.emit("reasoning", text=reasoning, step=step)
    calls = reply.get("tool_calls") or []
    content = (reply.get("content") or "").strip()
    assistant = {"role": "assistant", "content": content}
    for field in ("reasoning_content", "reasoning_details"):
        if field in reply:
            assistant[field] = reply[field]
    if any(k in assistant for k in ("reasoning_content", "reasoning_details")):
        assistant["_reasoning_identity"] = [agent.config.model, agent.config.base_url]
    if calls:
        assistant["tool_calls"] = [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls
        ]
    agent.messages.append(assistant)
    if calls:
        agent.events.emit("act_step", step=step, count=len(calls))
    return calls, content, False


def _handle_no_tool_reply(agent, content: str, step: int, dirty: bool, can_repair: bool, effective: str) -> tuple[str | None, bool]:
    if dirty and can_repair and agent.config.verify_command:
        agent.messages.append({"role": "user", "content": agent._verification_cycle(step), "_origin": "internal"})
        dirty = False
    if agent.repair.pending and can_repair:
        if agent.repair.attempts >= max(1, agent.config.max_retries):
            return agent._repair_report(), dirty
        msg = (
            "[Η ΕΡΓΑΣΙΑ ΣΥΝΕΧΙΖΕΤΑΙ] Ο έλεγχος αποτυγχάνει: "
            + agent.repair.failure.get("error", "")
            + ". Μην δηλώσεις ολοκλήρωση και μην ζητήσεις νέα έγκριση. Διάβασε, κάλεσε record_diagnosis, διόρθωσε και επανέλεγξε."
        )
        agent.messages.append({"role": "user", "content": msg, "_origin": "internal"})
        return None, dirty
    verified = not agent.config.verify_command or agent._verified_generation == agent._mutation_generation
    if agent._turn_requires_completion:
        if not agent._has_implementation_changes() or not verified:
            return agent._progress_report("Ο agent τερμάτισε χωρίς τεκμηριωμένη υλοποίηση.\nΤελευταία ανεπιβεβαίωτη απάντηση: " + content), dirty
        agent.task.status = "complete"
    if (agent._has_implementation_changes() and verified) or (agent._task_checks and not implementation_requested(effective)):
        agent.task.status = "complete"
    if agent.mode == "assistant" and agent._task_actions:
        agent.task.status = "complete"
    if agent.mode == "architect" and "PLAN.md" in agent._task_writes:
        agent.task.status = "complete"
    agent.events.emit("report_step", step=step)
    agent.events.emit("answer", text=content, steps=step, tokens=dict(agent.tokens))
    return content, dirty


def _update_turn_tracking(agent, name: str, args: dict, result: str) -> None:
    if name == "recall_context":
        agent.turn_usage["recall_calls"] += 1
    elif name == "remember":
        agent.turn_usage["remember_calls"] += 1
    is_err = result.startswith(ERROR_PREFIXES)
    if name == "use_skill" and args.get("name") and not is_err:
        agent._used_skills_in_turn.add(str(args["name"]).strip())
    if name in ("patch_skill", "propose_skill") and args.get("name") and result.startswith("ΠΡΟΤΑΣΗ SKILL"):
        agent._skill_healing_hints.pop(str(args["name"]).strip(), None)
    if is_err:
        agent._batch_failed = True
        first_line = result.splitlines()[0][:100] if result.splitlines() else ""
        for sname in getattr(agent, "_used_skills_in_turn", ()):
            agent._skill_healing_hints.setdefault(sname, f"{name}: {first_line}")
    if name == "read_file" and not args.get("outline") and not is_err:
        agent._task_reads.add(args.get("path", ""))
    if name in ACTION_TOOLS and (result.startswith("ΕΓΙΝΕ") or result.startswith("ΠΡΟΤΑΣΗ SKILL")):
        agent._task_actions.add(name)


def _process_tool_result(agent, call: dict, result, checking: bool, step: int, dirty: bool, feedback: list[str]) -> bool:
    try:
        raw_args = call["arguments"]
        args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else dict(raw_args or {})
    except (ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    name = call["name"]
    paths = mutation_paths(name, args, result)
    result = spill_large_output(agent.ws.root, name, result)
    agent.events.emit(
        "tool_result",
        name=name,
        path=paths[0] if len(paths) == 1 else (args.get("path") or args.get("output_path") or ""),
        paths=list(paths),
        recovery_required=getattr(result, "recovery_required", False),
        diff=getattr(result, "diff", ""),
        organization_changes=getattr(result, "organization_changes", []),
        transaction_id=getattr(result, "transaction_id", ""),
        result=result,
        step=step,
    )
    agent.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result, "_written_paths": list(paths)})
    _update_turn_tracking(agent, name, args, result)
    if agent.activity.record(name, args, result):
        agent.context.note_progress()
    if paths:
        dirty = True
        agent._task_writes.update(paths)
        agent._mutation_generation += 1
    if checking:
        agent.events.emit("verify_step", step=step, command=agent._last_verify_command)
        command = agent._last_verify_command
        ok = result.startswith("returncode=0")
        if not ok:
            for sname in getattr(agent, "_used_skills_in_turn", ()):
                agent._skill_healing_hints.setdefault(sname, "Αποτυχία ελέγχου verify")
        feedback.append(agent._check_feedback(ok, result, step, command))
        if ok and command != agent.config.verify_command and agent.config.verify_command:
            feedback.append(agent._verification_cycle(step))
        dirty = False
    return dirty


def _post_batch_decision(agent, step: int, dirty: bool, can_repair: bool, feedback: list[str]) -> tuple[str | None, bool]:
    if agent._filesystem_failure:
        agent.events.emit("checkpoint")
        return agent._progress_report(agent._filesystem_failure), dirty
    if agent._stop:
        agent.events.emit("stopped", step=step)
        return agent._stopped_report(), dirty
    # Evidence follows all tool responses, preserving the tool-call protocol.
    for message in feedback:
        agent.messages.append({"role": "user", "content": message, "_origin": "internal"})
    if dirty and can_repair and agent.config.verify_command:
        agent.messages.append({"role": "user", "content": agent._verification_cycle(step), "_origin": "internal"})
        dirty = False
    agent.events.emit("checkpoint")
    if agent._owner_denied:
        return agent._progress_report("Ο χρήστης δεν ενέκρινε την ενέργεια. Δεν εκτελώ άλλες ενέργειες σε αυτό το αίτημα."), dirty
    if agent._trusted_blocker:
        return agent._progress_report("Ο έλεγχος εμποδίζεται από το περιβάλλον ή την κατάσταση του υποψηφίου: " + agent._trusted_blocker), dirty
    if agent._pending_completion and not agent.repair.pending:
        return agent._finish_completion(), dirty
    if agent.activity.stalled:
        agent.events.emit("loop_guard", text=agent.activity.last_reason)
        return agent._progress_report("Σταμάτησα επαναλαμβανόμενες κλήσεις χωρίς νέα στοιχεία."), dirty
    if agent.repair.pending and agent.repair.attempts >= max(1, agent.config.max_retries):
        agent.events.emit("give_up", attempts=agent.repair.attempts, error=agent.repair.failure.get("error"))
        return agent._repair_report(), dirty
    return None, dirty


def _call_model_with_recovery(agent, request, tools, step: int, attempted: set[str]):
    from .model import ModelError
    from .turn_recovery import attempt_turn_recovery

    while True:
        try:
            return agent._model_reply(request, tools, step)
        except ModelError as exc:
            recovered, _detail = attempt_turn_recovery(agent, exc, attempted)
            if not recovered:
                raise
            request, tools, early = _prepare_step_request(agent, step)
            if early is not None:
                return {"content": early, "tool_calls": []}, {}


def run_chat_turn(agent, text: str, internal: bool = False) -> str:
    effective, can_repair = _begin_turn(agent, text, internal)
    dirty = False
    recovery_attempted: set[str] = set()
    for step in range(1, agent.config.max_steps + 1):
        if agent._filesystem_failure:
            agent.events.emit("checkpoint")
            return agent._progress_report(agent._filesystem_failure)
        if agent._stop:
            agent.events.emit("stopped", step=step)
            return agent._stopped_report()
        request, tools, early = _prepare_step_request(agent, step)
        if early is not None:
            return early
        reply, usage = _call_model_with_recovery(agent, request, tools, step, recovery_attempted)
        calls, content, stopped = _record_model_reply(agent, reply, usage, step)
        if stopped:
            return agent._stopped_report()
        if not calls:
            done, dirty = _handle_no_tool_reply(agent, content, step, dirty, can_repair, effective)
            if done is not None:
                return done
            continue
        if content and not any(call["name"] == "complete_task" for call in calls):
            agent.events.emit("thinking", text=content)
        feedback: list[str] = []
        agent._batch_failed = False
        for call, result, checking in agent._execute_calls(calls, agent._turn_tools, step):
            dirty = _process_tool_result(agent, call, result, checking, step, dirty, feedback)
        done, dirty = _post_batch_decision(agent, step, dirty, can_repair, feedback)
        if done is not None:
            return done
    agent.events.emit("limit", steps=agent.config.max_steps)
    if agent.repair.pending:
        return agent._repair_report()
    return agent._progress_report(f"Έφτασα το όριο των {agent.config.max_steps} βημάτων.")
