"""Evidence for non-coding procedures, without retaining source bodies or secrets."""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path

READS = {
    "list_files", "read_file", "search_in_file", "search_project", "read_document",
    "web_search", "web_fetch", "browser_fetch", "web_news", "youtube_transcript",
    "github_search", "github_read", "github_issues", "list_reminders", "list_memory",
    "list_skills", "search_past_sessions", "git_status_diff",
}
ACTIONS = {
    "add_reminder", "done_reminder",
    "create_word_document", "create_excel_sheet", "create_pdf_document",
}
DOCUMENT_ACTIONS = {
    "create_word_document", "create_excel_sheet", "create_pdf_document", "convert_data_file",
}
PAGING_KEYS = {
    "start", "end", "start_line", "max_lines", "start_char",
    "max_chars", "length", "limit", "max_entries", "max_files",
}


def _parse_call_args(raw) -> dict:
    try:
        args = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
    except (ValueError, TypeError):
        return {}
    return args if isinstance(args, dict) else {}


def _tool_role(name: str, args: dict, trait: dict) -> str:
    if name == "convert_data_file":
        return "action" if str(args.get("output_path") or "").strip() else "inspect"
    if name in READS or trait.get("readonly") is True:
        return "inspect"
    if name in ACTIONS or trait.get("readonly") is False:
        return "action"
    if name == "update_todos":
        return "plan"
    if name == "run_command":
        return "verify"
    return ""


def _tool_failed(role: str, name: str, result: str, traits: dict) -> bool:
    if result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
        return True
    if role == "verify" and not result.startswith("returncode=0"):
        return True
    if role in ("action", "plan") and not (result.startswith("ΕΓΙΝΕ") or name in traits):
        return True
    return False


def _row_identity_and_source(name: str, args: dict, root) -> tuple[str, str]:
    # Paging the same source repeatedly is not a procedure with three steps.
    identity = {k: v for k, v in args.items() if k not in PAGING_KEYS}
    if root is not None and identity.get("path") and name in READS and not name.startswith("github_"):
        identity["path"] = str((Path(root) / str(identity["path"])).resolve()).casefold()
    fingerprint = hashlib.sha256(json.dumps([name, identity], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    source = (
        [args.get("owner"), args.get("repo"), args.get("path", "")]
        if name.startswith("github_")
        else identity.get("path") or args.get("url")
    )
    source = source or (name + ":" + fingerprint)
    return fingerprint, hashlib.sha256(str(source).encode()).hexdigest()


def _collect_rows(events, traits: dict, root) -> tuple[list[dict], bool]:
    pending: dict[str, deque] = defaultdict(deque)
    rows: list[dict] = []
    seen: set[str] = set()
    for event in events:
        data = event.data
        name = data.get("name", "")
        if event.kind == "tool_call":
            pending[name].append(_parse_call_args(data.get("arguments")))
            continue
        if event.kind != "tool_result":
            continue
        args = pending[name].popleft() if pending[name] else {"path": data.get("path", "")}
        result = str(data.get("result") or "")
        trait = traits.get(name, {})
        role = _tool_role(name, args, trait)
        if not role:
            continue
        if _tool_failed(role, name, result, traits):
            return rows, True
        fingerprint, source_hash = _row_identity_and_source(name, args, root)
        if fingerprint in seen and not (name == "list_reminders" and any(r["role"] == "action" for r in rows)):
            continue
        seen.add(fingerprint)
        rows.append({
            "name": name,
            "role": role,
            "source": source_hash,
            "server": trait.get("server"),
            "result": result[:5000] if name in ACTIONS or name == "list_reminders" else "",
        })
    return rows, False


def _reminders_match(read_result: str, actions: list[dict]) -> bool:
    try:
        returned = {str(r["id"]): r for r in json.loads(read_result)}
        wanted = {}
        for action in actions:
            item = json.loads(action["result"].split(": ", 1)[1])
            wanted[str(item["id"])] = item
        return all(returned.get(key) == value for key, value in wanted.items())
    except (ValueError, TypeError, KeyError, IndexError):
        return False


def _has_verified_readback(rows: list[dict], actions: list[dict]) -> bool:
    last_action = max(i for i, r in enumerate(rows) if r["role"] == "action")
    all_builtin = all(a["name"] in ACTIONS for a in actions)
    for read in rows[last_action + 1:]:
        if read["role"] != "inspect":
            continue
        if all_builtin and read["name"] == "list_reminders" and _reminders_match(read["result"], actions):
            return True
        if read["server"] and all(a["server"] == read["server"] for a in actions):
            return True
    return False


def _classify_workflow(rows: list[dict], reads: list[dict], actions: list[dict]) -> tuple[str, list[str], str] | None:
    if actions:
        if any(a["name"] in DOCUMENT_ACTIONS for a in actions) and reads:
            return "documents", ["inspect", "generate", "verify"], "document-created"
        if reads and _has_verified_readback(rows, actions):
            return "organization", ["inspect", "act", "readback"], "readback"
        return None
    if any(r["role"] == "plan" for r in rows) and len(reads) >= 2:
        return "planning", ["inspect", "structure", "report"], "checklist-and-report"
    if rows[-1]["role"] == "verify" and reads:
        return "analysis", ["inspect", "evaluate", "verify"], "successful-command"
    if len({r["source"] for r in reads}) >= 2 and len(reads) >= 3:
        return "research", ["inspect", "compare", "report"], "sources-and-report"
    return None


def observed(events, traits=None, root=None):
    rows, failed = _collect_rows(events, traits or {}, root)
    if failed or len(rows) < 3:
        return None
    reads = [r for r in rows if r["role"] == "inspect"]
    actions = [r for r in rows if r["role"] == "action"]
    classified = _classify_workflow(rows, reads, actions)
    if classified is None:
        return None
    category, phases, evidence = classified
    names = list(dict.fromkeys(r["name"] for r in rows))[:30]
    signature = json.dumps([category, phases, names], ensure_ascii=False)
    return {
        "id": hashlib.sha256(signature.encode()).hexdigest()[:16],
        "category": category,
        "tools": names,
        "phases": phases,
        "evidence": evidence,
        "source_count": len({r["source"] for r in reads}),
        "extensions": [],
        "examples": [category + ": " + " → ".join(names[:8])],
    }
