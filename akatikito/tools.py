"""Εργαλεία project και δημόσιου web/GitHub, schemas και dispatch του Akatikito."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import memory as global_memory
from .workspace import Workspace
from .task_state import COMPLETION_SPEC
from .reading import SOURCE_INDEX, READ_LINES
from .editing import EditError, EditResult, parse_edits, apply_edits, check_syntax, commit, commit_batch
from .config import Config
from .network_tools import NETWORK_SPECS, NETWORK_HANDLERS, NetworkError, web_fetch, github_search, github_read, github_issues, github_clone

MAX_READ_LINES = READ_LINES
MAX_READ_CHARS = 24000
MAX_SEARCH_HITS = 50
MAX_OUTPUT = 10000
MAX_PROJECT_HITS = 20


# --------------------------------------------------------------------------- βοηθητικά


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp1253", "cp437"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [κόπηκε — {len(text) - limit} χαρακτήρες ακόμη]"


def _clip_with_archive(ws: Workspace, text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    store = getattr(ws, "context_store", None)
    if store is None:
        from .context import ContextWindow
        store = ContextWindow(ws).store
    key = store(text)
    half = max(1, (limit - 300) // 2)
    return text[:half] + f"\n... [συνοπτική προβολή· πλήρες ακριβές περιεχόμενο: recall_context id={key}] ...\n" + text[-half:]


def _protect_live_source(target: Path) -> None:
    live = Path(__file__).resolve().parents[1]
    if target == live or live in target.parents:
        raise PermissionError("Η εφαρμογή αλλάζει μόνο μέσω Αυτοαναβάθμιση, σε ελεγμένο αντίγραφο.")


from .searching import project_files as _project_files


# --------------------------------------------------------------------------- τα εργαλεία


def list_files(ws: Workspace, path: str = ".", recursive: bool = False, max_entries: int = 200) -> str:
    base=ws.resolve(path)
    if not base.is_dir():
        return f"ΣΦΑΛΜΑ: δεν βρέθηκε φάκελος '{path}'."
    limit=max(1,min(500,int(max_entries)))
    ignored={".git",".venv","venv","node_modules","__pycache__",".agent",".agent_backups"}
    rows=[];pending=[base]
    while pending:
        directory=pending.pop(0)
        for item in sorted(directory.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower())):
            if item.name in ignored or item.is_symlink():
                continue
            label=ws.relative(item)
            rows.append(label+("/" if item.is_dir() else ""))
            if len(rows)>=limit:
                return "\n".join(rows)+f"\n[Όριο {limit} εγγραφών· περιόρισε το path για περισσότερα.]"
            if recursive and item.is_dir():
                pending.append(item)
    return "\n".join(rows) or "Ο φάκελος δεν περιέχει ορατά αρχεία."


def read_file(ws: Workspace, path: str, start: int = 1, end: int | None = None,
              anchor_line: int | None = None, outline: bool = False, full: bool = False) -> str:
    from .smart_read import is_wide_read_request, missing_file_message, render_wide_read

    target = ws.resolve(path)
    if not target.is_file():
        return missing_file_message(ws, path)
    if outline:
        return SOURCE_INDEX.outline(target, ws.relative(target), limit=MAX_OUTPUT)
    if is_wide_read_request(start, end, anchor_line, full, MAX_READ_LINES):
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return render_wide_read(ws.relative(target), lines, start, end) if lines else f"Αρχείο {ws.relative(target)} είναι κενό."
    first, last, data, mode = SOURCE_INDEX.span(target, {"start":start,"end":end,"anchor_line":anchor_line})
    lines = data["lines"];total = len(lines)
    if total == 0:
        return f"Αρχείο {ws.relative(target)} είναι κενό."
    if first > total:
        return f"ΣΦΑΛΜΑ: το αρχείο έχει μόνο {total} γραμμές."
    if last < first:
        return "ΣΦΑΛΜΑ: το 'end' είναι πριν το 'start'."
    body = "\n".join(f"{n:>5} | {lines[n-1]}" for n in range(first,last+1))
    head = f"{ws.relative(target)} — γραμμές {first}-{last} από {total}:\n"
    result = _clip_with_archive(ws, head + body, limit=MAX_READ_CHARS)
    notes = []
    if mode:
        notes.append(mode + (" — η προβολή συντομεύτηκε λόγω μεγέθους· το πλήρες κείμενο διατηρείται στο παραπάνω id." if len(head+body)>MAX_READ_CHARS else ""))
    elif last < min(total,int(end) if end else total):
        notes.append(f"Δεν διαβάστηκε όλο το ζητούμενο εύρος. Συνέχεια με start={last+1}, ή outline=true και anchor_line στη σχετική συνάρτηση.")
    if not anchor_line and data["blocks"] and len(lines)>MAX_READ_LINES and start==1:
        notes.append(SOURCE_INDEX.outline(target, ws.relative(target)))
    return result + ("\n"+"\n".join(notes) if notes else "")


def search_in_file(ws: Workspace, path: str, keyword: str) -> str:
    target = ws.resolve(path)
    if not target.is_file():
        return f"ΣΦΑΛΜΑ: δεν βρήκα αρχείο '{path}'."
    if not keyword:
        return "ΣΦΑΛΜΑ: δώσε λέξη για αναζήτηση."
    hits = []
    for number, line in enumerate(target.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if keyword.lower() in line.lower():
            hits.append(f"{number:>5} | {line.strip()[:200]}")
            if len(hits) >= MAX_SEARCH_HITS:
                break
    if not hits:
        return f"Δεν βρήκα '{keyword}' στο {ws.relative(target)}."
    return _clip(f"'{keyword}' στο {ws.relative(target)} ({len(hits)} εμφανίσεις):\n" + "\n".join(hits))


def search_project(ws: Workspace, keyword: str, max_files: int = 10, regex: bool = False, time_limit: float | None = None) -> str:
    from .searching import search
    limit=getattr(getattr(ws,'config',None),'search_time_limit',Config.search_time_limit) if time_limit is None else time_limit
    result=search(ws.root,keyword,max_files,regex,limit,files=_project_files(ws.root),
                  stopped=getattr(ws,'search_stopped',lambda:False))
    return _clip(result)


def _compact_diff(label: str, original: str, updated: str, max_lines: int = 80) -> str:
    import difflib
    diff_lines = list(difflib.unified_diff(
        original.splitlines(),
        updated.splitlines(),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        lineterm="",
        n=2,
    ))
    if not diff_lines:
        return ""
    if len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines] + [f"... (+{len(diff_lines) - max_lines} γραμμές diff ακόμη)"]
    return "\n".join(diff_lines)[:4500]


def write_file(ws: Workspace, path: str, content: str, overwrite: bool = False) -> str:
    target=ws.resolve(path);_protect_live_source(target)
    existed = target.exists()
    if existed and not overwrite:
        return f"ΣΦΑΛΜΑ: το '{ws.relative(target)}' υπάρχει ήδη. Για αλλαγή σε υπάρχον αρχείο χρησιμοποίησε edit_file (ή write_file με overwrite=true)."
    try:
        raw_prev = target.read_bytes() if (existed and target.is_file()) else None
        orig_text = raw_prev.decode("utf-8", errors="replace") if raw_prev is not None else ""
        check_syntax(target,content)
        guard=getattr(ws,"write_guard",None)
        if guard:guard(target,orig_text,content)
        commit(ws,target,raw_prev,content.encode("utf-8"))
    except (EditError,FileExistsError) as exc:
        return "ΣΦΑΛΜΑ: "+str(exc)
    rel = ws.relative(target)
    diff_preview = _compact_diff(rel, orig_text, content)
    msg = f"ΕΓΙΝΕ: γράφτηκε νέο αρχείο {rel} ({len(content.splitlines())} γραμμές)."
    return EditResult(msg, (rel,), diff=diff_preview)


def _prepare_edit(ws: Workspace, path: str, diff_text, edits):
    """Returns (target, raw, original, updated, change_count) or a ΣΦΑΛΜΑ string; nothing is written."""
    target=ws.resolve(path);_protect_live_source(target)
    if not target.is_file():return f"ΣΦΑΛΜΑ: δεν βρήκα αρχείο '{path}'."
    changes=parse_edits(diff_text,edits)
    raw=target.read_bytes();original=raw.decode("utf-8")
    updated=apply_edits(original,changes)
    return target,raw,original,updated,len(changes)


def edit_file(ws: Workspace, path: str | None = None, diff_text: str | None = None, edits: list[dict] | None = None, files: list[dict] | None = None) -> str:
    """Validate the complete edit against the original file before committing anything."""
    if files is not None and any(value is not None for value in (path,diff_text,edits)):
        return "ΣΦΑΛΜΑ: δώσε files ή path με edits/diff_text, όχι και τα δύο."
    if files is None and path is None:return "ΣΦΑΛΜΑ: δώσε «path» (ένα αρχείο) ή «files» (πολλά αρχεία)."
    if files is not None:return _edit_files(ws,files)
    try:
        prepared=_prepare_edit(ws,path,diff_text,edits)
        if isinstance(prepared,str):return prepared
        target,raw,original,updated,count=prepared
        if updated==original:return "Καμία αλλαγή — το κείμενο ήταν ήδη ίδιο."
        check_syntax(target,updated)
        guard=getattr(ws,"write_guard",None)
        if guard:guard(target,original,updated)
        commit(ws,target,raw,updated.encode("utf-8"))
    except UnicodeDecodeError:
        return "ΣΦΑΛΜΑ: το αρχείο δεν είναι UTF-8. Δεν αντικαταστάθηκε με αλλοιωμένο κείμενο."
    except EditError as exc:
        return "ΣΦΑΛΜΑ: "+str(exc)
    rel = ws.relative(target)
    diff_preview = _compact_diff(rel, original, updated)
    return EditResult(f"ΕΓΙΝΕ: εφαρμόστηκαν {count} αλλαγές στο {rel} (με αντίγραφο ασφαλείας).", (rel,), diff=diff_preview)


def _edit_files(ws: Workspace, files) -> str:
    if not isinstance(files,list) or not files:return "ΣΦΑΛΜΑ: το «files» πρέπει να είναι μη κενή λίστα."
    batch=[];summary=[];diffs=[]
    try:
        for item in files:
            if not isinstance(item,dict) or not isinstance(item.get("path"),str):
                return "ΣΦΑΛΜΑ: κάθε στοιχείο του «files» χρειάζεται «path». Δεν εφαρμόστηκε τίποτα."
            label=item["path"]
            try:
                prepared=_prepare_edit(ws,label,item.get("diff_text"),item.get("edits"))
                if isinstance(prepared,str):return prepared+" Δεν εφαρμόστηκε τίποτα."
                target,raw,original,updated,count=prepared
            except UnicodeDecodeError:
                return f"ΣΦΑΛΜΑ: {label}: το αρχείο δεν είναι UTF-8. Δεν εφαρμόστηκε τίποτα."
            except EditError as exc:
                return f"ΣΦΑΛΜΑ: {label}: {exc} Δεν εφαρμόστηκε τίποτα."
            if updated==original:continue
            rel = ws.relative(target)
            batch.append((target,raw,updated.encode("utf-8")));summary.append(f"{rel} ({count} αλλαγές)")
            d = _compact_diff(rel, original, updated, max_lines=40)
            if d:
                diffs.append(d)
        if not batch:return "Καμία αλλαγή — το κείμενο ήταν ήδη ίδιο."
        paths=commit_batch(ws,batch)
    except EditError as exc:
        return EditResult("ΣΦΑΛΜΑ: "+str(exc),exc.changed_paths,bool(exc.rollback_errors),exc.recovery_paths)
    return EditResult(f"ΕΓΙΝΕ: εφαρμόστηκαν αλλαγές με επαναφορά σε {len(batch)} αρχεία: "+", ".join(summary)+" (με αντίγραφο ασφαλείας).",paths,diff="\n\n".join(diffs)[:4500])


def run_command(ws: Workspace, command: str, timeout: int = 120) -> str:
    validator = getattr(ws, "update_validator", None)
    if validator is not None:
        if (command or "").strip() != "verify":
            return "ΑΠΟΡΡΙΨΗ: στην αυτοαναβάθμιση επιτρέπεται μόνο η εντολή verify."
        ok, output = validator()
        return f"returncode={0 if ok else 1}\n{output}"
    live = Path(__file__).resolve().parents[1]
    if ws.root == live or live in ws.root.parents:
        return "ΑΠΟΡΡΙΨΗ: για αλλαγές/ελέγχους της εφαρμογής χρησιμοποίησε Αυτοαναβάθμιση."
    command = (command or "").strip()
    if not command:
        return "ΣΦΑΛΜΑ: κενή εντολή."
    if ".." in command.replace("\\", "/").split():
        return "ΑΠΟΡΡΙΨΗ: μην προσπαθείς να βγεις έξω από το project με '..'."
    from .approvals import command_risk
    guard = getattr(ws,'command_guard',None)
    risk = command_risk(command, ws.root)
    if guard is not None:
        if not guard(command,risk):
            return 'ΑΠΟΡΡΙΨΗ [OWNER_DENIED]: Ο χρήστης δεν ενέκρινε την εντολή ή σταμάτησε την εργασία.'
    elif risk == 'dangerous':
        return 'ΑΠΟΡΡΙΨΗ [OWNER_DENIED]: Η επικίνδυνη εντολή χρειάζεται ρητή έγκριση στο παράθυρο.'
    try:
        timeout_val = int(timeout or 120)
    except (TypeError, ValueError):
        timeout_val = 120
    try:
        from .job_sandbox import managed_job

        with managed_job() as job:
            proc = subprocess.Popen(  # noqa: S602 — command_policy & owner approval enforced before Popen
                command,
                shell=True,
                cwd=str(ws.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            job.assign(proc)
            try:
                raw_out, raw_err = proc.communicate(timeout=max(1, timeout_val))
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                out_tail, err_tail = proc.communicate()
                output = (_decode((exc.stdout or b"") + (out_tail or b"")) + _decode((exc.stderr or b"") + (err_tail or b""))).strip()
                return f"returncode=124\nTimeoutError: η εντολή ξεπέρασε το όριο των {timeout_val}s.\n" + _clip_with_archive(ws, output)
    except OSError as exc:
        return f"ΣΦΑΛΜΑ: δεν μπόρεσα να τρέξω την εντολή ({exc})."

    output = (_decode(raw_out) + _decode(raw_err)).strip()
    body = output or "(καμία έξοδος)"
    return f"returncode={proc.returncode}\n{_clip_with_archive(ws, body)}"


def remember(ws: Workspace, note: str, scope: str = "project") -> str:
    """Αποθηκεύει σημείωση: project (default), global (όλα τα projects) ή user (για τον χρήστη)."""
    scope = (scope or "project").strip().lower()
    if scope in ("global", "memory", "γενική"):
        from .profile import Profile
        return Profile().propose_global(note)
    if scope in ("user", "profile", "χρήστης"):
        from .profile import Profile
        return Profile().propose(str(note)[:120],note,"fact","confirmed")
    return ws.append_memory(note)


TOOL_SPECS = [
    {"type":"function","function":{
        "name":"list_files","description":"List current project files and folders. Use this instead of shell dir/ls, especially in a self-update candidate where only verify commands are allowed. Hidden runtime/dependency folders are omitted.",
        "parameters":{"type":"object","properties":{
            "path":{"type":"string"},"recursive":{"type":"boolean"},"max_entries":{"type":"integer"}
        },"required":[]}
    }},
    {"type":"function","function":{
        "name":"recall_context","description":"Retrieve exact original archived history or memory by id. Use query to locate a detail, or start/length to page; historical code may be stale.",
        "parameters":{"type":"object","properties":{
            "id":{"type":"string"},"start":{"type":"integer"},"length":{"type":"integer"},"query":{"type":"string"}
        },"required":["id"]}
    }},
    {"type": "function", "function": {
        "name": "record_diagnosis",
        "description": "Required after a failed check, before editing files. State a concrete root cause, code evidence and the files to fix. Use kind=test_fixture only for a faulty mock/fixture; existing assertions and cases must remain unchanged.",
        "parameters": {"type": "object", "properties": {
            "cause": {"type": "string", "description": "Specific root cause, not merely the exception name"},
            "evidence": {"type": "string", "description": "Observed expected/actual behavior and relevant code lines"},
            "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
            "kind": {"type": "string", "enum": ["code", "test_fixture"]}
        }, "required": ["cause", "evidence", "paths"]}
    }},
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read current source with line numbers. Set full=true (or wide start/end) to read up to 1,500 lines / 80KB in ONE step (cleanly trimmed on complete lines). For very large files, use outline=true or read_symbol/anchor_line. Request independent files together in parallel tool calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "start": {"type": "integer", "description": "First line, 1-based (default 1)"},
                    "end": {"type": "integer", "description": "Last line (default end of file)"},
                    "anchor_line": {"type":"integer","description":"Read the complete Python function/class containing this 1-based line; prefer this over guessing short ranges."},
                    "outline": {"type":"boolean","description":"Return a current Python function/class map with line ranges; bodies are NOT included."},
                    "full": {"type":"boolean","description":"Read up to 1,500 lines / 80KB in one call, cleanly cut at complete line boundaries."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": "Find which lines of a file contain a word (case-insensitive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "keyword": {"type": "string", "description": "Word or phrase to find"},
                },
                "required": ["path", "keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project",
            "description": "Search a word across all text files in the project (case-insensitive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Word or phrase to find"},
                    "max_files": {"type": "integer", "description": "Max files to return (default 10)"},
                    "regex": {"type": "boolean", "description": "Treat keyword as a case-insensitive regular expression (default false)"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Change an existing file. Prefer structured edits with search/replace and optional exact start_line. All edits refer to the original file. Legacy SEARCH/REPLACE is accepted only with unambiguous delimiters. Without start_line, SEARCH must appear "
                "exactly once. Multiple blocks are applied atomically: if any block fails, nothing is written."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "edits": {"type":"array","minItems":1,"maxItems":32,"description":"Preferred: all searches refer to the ORIGINAL file; changes must not overlap. Supports literal merge markers safely.","items":{"type":"object","properties":{
                        "search":{"type":"string"},"replace":{"type":"string"},"start_line":{"type":"integer","description":"Optional exact 1-based line in the original file, to disambiguate identical matches."}
                    },"required":["search","replace"],"additionalProperties":False}},
                    "diff_text": {
                        "type": "string",
                        "description": (
                            "One or more blocks with this exact shape:\n"
                            "<<<<<<< SEARCH\nold text\n=======\nnew text\n>>>>>>> REPLACE"
                        ),
                    },
                    "files": {"type":"array","minItems":1,"maxItems":16,"description":"Instead of path/edits/diff_text: validate several existing files before writing, restore on failure, and report any incomplete recovery explicitly.","items":{"type":"object","properties":{
                        "path":{"type":"string"},"edits":{"type":"array","minItems":1,"maxItems":32,"items":{"type":"object","properties":{"search":{"type":"string"},"replace":{"type":"string"},"start_line":{"type":"integer"}},"required":["search","replace"],"additionalProperties":False}},"diff_text":{"type":"string"}
                    },"required":["path"],"additionalProperties":False}},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new complete file (or replace an existing file when overwrite=true with automatic backup). Syntax is checked before writing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "content": {"type": "string", "description": "Full file content"},
                    "overwrite": {"type": "boolean", "description": "Set true to overwrite an existing file (with automatic backup). Default false."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command (Windows cmd.exe) inside the project folder and see its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run, e.g. 'python -m pytest -q'"},
                    "timeout": {"type": "integer", "description": "Seconds before giving up (default 120)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Remember one durable note. scope='project' (default) appends locally after a content check. "
                "'global' proposes a note for ALL projects; 'user' proposes a fact/preference. "
                "Global/user proposals require owner approval and are not active yet. Never repeat the same proposal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "One short fact"},
                    "scope": {
                        "type": "string",
                        "enum": ["project", "global", "user"],
                        "description": "Where to keep it (default: project)",
                    },
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mode",
            "description": (
                "Switch the working mode: ask (read-only answers), architect (write a plan), "
                "code (implement), debug (find the root cause of a bug)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["ask", "architect", "code", "debug", "assistant"],
                        "description": "The mode to switch to",
                    }
                },
                "required": ["mode"],
            },
        },
    },
]

from .code_engine import CODE_ENGINE_HANDLERS, CODE_ENGINE_SPECS
from .content_workflow import CONTENT_HANDLERS, CONTENT_SPECS
from .email_assistant import EMAIL_HANDLERS, EMAIL_SPECS
from .features import SPECS as FEATURE_SPECS, HANDLERS as FEATURE_HANDLERS
from .relevance import RELEVANCE_HANDLERS, RELEVANCE_SPECS
from .git_workflow import GIT_WORKFLOW_HANDLERS, GIT_WORKFLOW_SPECS
from .notes_vault import NOTES_HANDLERS, NOTES_SPECS
TOOL_SPECS += (
    NETWORK_SPECS + [COMPLETION_SPEC] + FEATURE_SPECS + RELEVANCE_SPECS
    + CODE_ENGINE_SPECS + NOTES_SPECS + CONTENT_SPECS + GIT_WORKFLOW_SPECS + EMAIL_SPECS
)

SPEC_BY_NAME = {entry.get("function", {}).get("name"): entry.get("function", {}).get("parameters") for entry in TOOL_SPECS}


def _validate_value(value, schema: dict, path: str) -> str | None:
    """Επιστρέφει ελληνικό μήνυμα αν η τιμή δεν ταιριάζει στο schema, αλλιώς None."""
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: μη έγκυρη τιμή «{value}». Επιτρεπτές: {', '.join(map(str, schema['enum']))}."
    t = schema.get("type")
    if t is None:
        return None
    if t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{path}: περίμενε ακέραιο, πήρε {type(value).__name__}."
    elif t == "string" and not isinstance(value, str):
        return f"{path}: περίμενε string, πήρε {type(value).__name__}."
    elif t == "boolean" and not isinstance(value, bool):
        return f"{path}: περίμενε boolean, πήρε {type(value).__name__}."
    elif t == 'object':
        if not isinstance(value,dict):return f'{path}: περίμενε object.'
        props=schema.get('properties',{})
        for key in schema.get('required',[]):
            if key not in value:return f'{path}: λείπει {key}.'
        for key,item in value.items():
            if key not in props and schema.get('additionalProperties') is False:return f'{path}: άγνωστο όρισμα {key}.'
            error=_validate_value(item,props.get(key,{}),path+'.'+key)
            if error:return error
    elif t == "array":
        if not isinstance(value, list):
            return f"{path}: περίμενε array, πήρε {type(value).__name__}."
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{path}: χρειάζονται τουλάχιστον {schema['minItems']} στοιχεία."
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path}: το πολύ {schema['maxItems']} στοιχεία."
        items = schema.get("items") or {}
        for i, item in enumerate(value):
            err = _validate_value(item, items, f"{path}[{i}]")
            if err:
                return err
    return None


def validate_arguments(name: str, args) -> str | None:
    """Έλεγχος ορισμάτων έναντι του JSON schema του εργαλείου (λιγότερα κακόμορφα calls)."""
    schema = SPEC_BY_NAME.get(name or "")
    if schema is None:
        return None
    if not isinstance(args, dict):
        return "Τα ορίσματα εργαλείου πρέπει να είναι JSON object."
    if name=="edit_file":
        if ("path" in args)==("files" in args):
            return "Δώσε path ή files, αποκλειστικά ένα από τα δύο."
        if "files" in args and any(key in args for key in ("edits","diff_text")):
            return "Τα edits/diff_text πρέπει να βρίσκονται μέσα σε κάθε στοιχείο files."
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in args:
            return f"Λείπει υποχρεωτικό όρισμα «{key}» για το {name}."
    for key, value in args.items():
        prop = props.get(key)
        if prop is None:
            continue
        err = _validate_value(value, prop, key)
        if err:
            return err
    return None


_HANDLERS = {
    "list_files": list_files,
    "read_file": read_file,
    "search_in_file": search_in_file,
    "search_project": search_project,
    "edit_file": edit_file,
    "write_file": write_file,
    "run_command": run_command,
    "remember": remember,
}

_HANDLERS.update(NETWORK_HANDLERS)
_HANDLERS.update(FEATURE_HANDLERS)
_HANDLERS.update(RELEVANCE_HANDLERS)
_HANDLERS.update(CODE_ENGINE_HANDLERS)
_HANDLERS.update(NOTES_HANDLERS)
_HANDLERS.update(CONTENT_HANDLERS)
_HANDLERS.update(GIT_WORKFLOW_HANDLERS)
_HANDLERS.update(EMAIL_HANDLERS)


def dispatch(ws: Workspace, name: str, arguments: str | dict | None) -> str:
    handler = _HANDLERS.get(name or "")
    if handler is None:
        return f"ΣΦΑΛΜΑ: άγνωστο εργαλείο '{name}'. Διαθέσιμα: {', '.join(_HANDLERS)}."
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"ΣΦΑΛΜΑ: τα ορίσματα δεν είναι έγκυρο JSON ({exc})."
    else:
        args = dict(arguments or {})
    invalid = validate_arguments(name, args)
    if invalid:
        return f"ΣΦΑΛΜΑ: {invalid}"
    try:
        return handler(ws, **args)
    except NetworkError as exc:
        return f"ΣΦΑΛΜΑ: {exc}"
    except PermissionError as exc:
        return f"ΑΠΟΡΡΙΨΗ: {exc}"
    except TypeError as exc:
        return f"ΣΦΑΛΜΑ: λάθος ορίσματα ({exc})."
    except Exception as exc:  # noqa: BLE001 — το λάθος πάει πίσω στο μοντέλο, δεν σκάει ο βοηθός
        return f"ΣΦΑΛΜΑ: {type(exc).__name__}: {exc}"
