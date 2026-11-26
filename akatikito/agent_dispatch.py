"""Single tool-call dispatch, guards, policy checks, and post-call accounting."""
from __future__ import annotations

from . import modes
from .audit_log import AuditLog
from .editing import EditResult
from .execution import arguments as call_arguments, mutation_paths
from .injection_guard import guard_untrusted_text
from .profile import topic_key
from .project_profiles import ProjectProfiles
from .repair import is_test_command
from .tools import validate_arguments

LEARNING_TOOLS = ("propose_memory", "propose_skill", "patch_skill")
UNTRUSTED_SOURCES = {
    "web_search", "web_fetch", "browser_fetch", "web_news", "youtube_transcript", "read_document", "email_summary",
}
ASSISTANT_TEXT_EXTS = (".md", ".txt", ".csv", ".json")
ASSISTANT_DOC_TOOLS = ("create_word_document", "create_excel_sheet", "create_pdf_document")
ERROR_PREFIXES = ("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")


def _safe_audit(root, event_type: str, **kwargs) -> bool:
    try:
        AuditLog(root).record(event_type, **kwargs)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _handle_set_mode(agent, args: dict, turn_tools: set[str]) -> str:
    target = args.get("mode") or args.get("name") or ""
    req_tools = agent._filter_disabled_tools(set(modes.allowed_tools(target))) if target in modes.MODES else set()
    ok = target in modes.MODES and req_tools <= turn_tools
    if agent.repair.pending and target not in ("code", "debug"):
        ok = False
    if ok:
        agent._set_mode(target, reason="πρόταση μοντέλου")
        return f"Λειτουργία: {modes.label(agent.mode)}"
    return "ΑΠΟΡΡΙΨΗ: η λειτουργία υπερβαίνει την εργασία ή αφήνει εκκρεμή διόρθωση tests."


def _handle_recall_context(agent, args: dict, _turn_tools: set[str]) -> str:
    page = agent.context.request_recall(**args)
    if page.get("blocked"):
        return agent.activity.block(page["text"])
    return agent.context.recall_text(page)


def _handle_record_diagnosis(agent, args: dict, _turn_tools: set[str]) -> str:
    result = agent.repair.diagnose(**args)
    if result.startswith("ΔΙΑΓΝΩΣΗ"):
        agent.events.emit("repair", phase="diagnosis", text=args["cause"], paths=args["paths"])
    return result


SPECIAL_HANDLERS = {
    "run_subtask": lambda agent, args, _t: agent._run_subtask(**args),
    "run_subtasks": lambda agent, args, _t: agent._run_subtasks(**args),
    "complete_task": lambda agent, args, _t: agent._accept_completion(args),
    "set_mode": _handle_set_mode,
    "recall_context": _handle_recall_context,
    "record_diagnosis": _handle_record_diagnosis,
}


def _check_learning_quota(agent, name: str, args: dict) -> tuple[tuple[str, str] | None, str | None]:
    if name not in LEARNING_TOOLS:
        return None, None
    proposals = getattr(agent, "_learning_proposals", set())
    kind_group = "skill" if name in ("propose_skill", "patch_skill") else "memory"
    key = (kind_group, topic_key(args.get("topic") or args.get("name") or ""))
    limit = 1 if kind_group == "skill" else 3
    if key in proposals or sum(k[0] == kind_group for k in proposals) >= limit:
        return key, agent.activity.block("Η πρόταση καταγράφηκε ήδη ή έφτασε το όριο αυτού του αιτήματος.")
    return key, None


def _target_paths(args: dict) -> list[str]:
    if isinstance(args.get("files"), list):
        return [item.get("path", "") if isinstance(item, dict) else str(item) for item in args.get("files", [])]
    return [args.get("path", "")]


def _has_hidden_part(agent, path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(agent.ws.root).parts)


def _check_mode_write_policy(agent, name: str, args: dict) -> str | None:
    targets = _target_paths(args)
    if agent.mode == "architect" and name in ("write_file", "edit_file"):
        if any(agent.ws.resolve(path) != agent.ws.root / "PLAN.md" for path in targets):
            return "ΑΠΟΡΡΙΨΗ: ο Αρχιτέκτονας γράφει μόνο το PLAN.md."
        return None
    if agent.mode != "assistant":
        return None
    if name in ("write_file", "edit_file", "convert_data_file"):
        check_targets = [args.get("output_path", "")] if name == "convert_data_file" else targets
        for target in check_targets:
            if not target:
                continue
            path = agent.ws.resolve(target)
            if path.suffix.lower() not in ASSISTANT_TEXT_EXTS or _has_hidden_part(agent, path):
                return "ΑΠΟΡΡΙΨΗ: ο Βοηθός γράφει μόνο απλά έγγραφα στον φάκελο project."
    elif name in ASSISTANT_DOC_TOOLS:
        for target in targets:
            if target and _has_hidden_part(agent, agent.ws.resolve(target)):
                return "ΑΠΟΡΡΙΨΗ: ο Βοηθός δεν γράφει σε κρυφούς φακέλους/αρχεία."
    return None


def _check_repair_command_policy(agent, name: str, command: str, checking: bool) -> str | None:
    if not (agent.repair.pending and name == "run_command"):
        return None
    required = agent.repair.failure.get("command") or agent.config.verify_command
    if not checking or command != required:
        return "ΑΠΟΡΡΙΨΗ: όσο εκκρεμεί αποτυχία, διάβασε με τα εργαλεία ανάγνωσης και τρέξε μόνο τον καθορισμένο έλεγχο."
    if not agent.repair.can_verify():
        return "ΑΠΟΡΡΙΨΗ: ο ίδιος έλεγχος ήδη απέτυχε. Τεκμηρίωσε τη διάγνωση και κάνε διόρθωση πριν τον επανέλεγχο."
    return None


def _dispatch_tool(agent, name: str, args: dict, proj_profile: str, dispatch_fn) -> str:
    agent.ws.organization_scope = "full" if (agent.mode in ("code", "debug") and proj_profile == "developer") else "documents"
    if name == "run_command" and agent._shared_command_lock is not None:
        with agent._shared_command_lock:
            result = dispatch_fn(agent.ws, name, args)
    elif getattr(agent, "mcp", None) and name in agent.mcp.entries:
        result = agent.mcp.call(name, args, agent)
    else:
        result = dispatch_fn(agent.ws, name, args)
    if name in UNTRUSTED_SOURCES or name.startswith("mcp_"):
        result = guard_untrusted_text(result, source=name)
    return result


def _record_call_effects(agent, name: str, args: dict, result, checking: bool, command: str, learning_key) -> tuple[str, bool]:
    if learning_key is not None and not result.startswith((*ERROR_PREFIXES, "ΕΝΤΟΠΙΣΤΗΚΕ ΠΑΡΟΜΟΙΟ SKILL")):
        proposals = getattr(agent, "_learning_proposals", set())
        proposals.add(learning_key)
        agent._learning_proposals = proposals
    if "[OWNER_DENIED]" in result:
        agent._owner_denied = True
    checking = checking and result.startswith("returncode=")
    if checking:
        agent._last_verify_command = command
    if name == "read_file" and not args.get("outline") and not result.startswith(ERROR_PREFIXES):
        agent.repair.note_read(args["path"])
    changed = mutation_paths(name, args, result)
    try:
        for path in changed:
            agent.repair.note_change(path)
    except Exception as exc:  # quality: allow BLE001 — preserve written-file error contract if repair tracking fails
        result = EditResult(
            "ΣΦΑΛΜΑ ΜΕΤΑ ΤΗΝ ΕΓΓΡΑΦΗ: τα αρχεία γράφτηκαν αλλά η καταγραφή απέτυχε: " + str(exc),
            changed,
            True,
            getattr(result, "recovery_paths", ()),
        )
    if getattr(result, "recovery_required", False):
        agent._filesystem_failure = str(result)
    status_tag = "denied" if str(result).startswith("ΑΠΟΡΡΙΨΗ") else ("error" if str(result).startswith("ΣΦΑΛΜΑ") else "ok")
    _safe_audit(agent.ws.root, "tool_call", tool=name, mode=agent.mode, status=status_tag, paths=changed, summary=str(result)[:220])
    return result, checking


def execute_call(agent, call: dict, turn_tools: set[str], dispatch_fn) -> tuple[str, bool]:
    name = call["name"]
    if agent._stop or getattr(agent, "_owner_denied", False):
        return "ΑΠΟΡΡΙΨΗ: ο χρήστης σταμάτησε ή δεν ενέκρινε την εκτέλεση.", False
    blocked = agent._tool_guard_error(name, turn_tools, allow_mode_change=True)
    if blocked:
        return blocked, False
    try:
        args = call_arguments(call)
        invalid = validate_arguments(name, args)
        if invalid:
            return f"ΣΦΑΛΜΑ: {invalid}", False
        learning_key, quota_err = _check_learning_quota(agent, name, args)
        if quota_err:
            return quota_err, False
        activity_err = agent.activity.before(name, args)
        if activity_err:
            return activity_err, False
        special = SPECIAL_HANDLERS.get(name)
        if special is not None:
            return special(agent, args, turn_tools), False
        mode_err = _check_mode_write_policy(agent, name, args)
        if mode_err:
            return mode_err, False
        command = (args.get("command") or "").strip()
        checking = name == "run_command" and bool(command) and (
            command == agent.config.verify_command.strip() or is_test_command(command)
        )
        repair_err = _check_repair_command_policy(agent, name, command, checking)
        if repair_err:
            return repair_err, False
        proj_profile = getattr(agent.ws, "project_profile", None) or ProjectProfiles().get(agent.ws.root)
        profile_denial = ProjectProfiles.check_tool(proj_profile, name)
        if profile_denial:
            _safe_audit(agent.ws.root, "tool_denied", tool=name, mode=agent.mode, status="denied", summary=profile_denial)
            return profile_denial, False
        result = _dispatch_tool(agent, name, args, proj_profile, dispatch_fn)
        return _record_call_effects(agent, name, args, result, checking, command, learning_key)
    except PermissionError as exc:
        return f"ΑΠΟΡΡΙΨΗ: {exc}", False
    except Exception as exc:  # quality: allow BLE001 — tool boundary converts unexpected errors into model-visible text
        return f"ΣΦΑΛΜΑ: {type(exc).__name__}: {exc}", False
