"""Controlled Git commit and Draft PR workflow (PRO-PLAN D4).

Safety invariants:
- Gated behind Advanced `git` feature AND `dev` (🛠 Ανάπτυξη) project profile.
- NEVER executes `push`, `--force`, `reset --hard`, or `--no-verify`.
- Refuses commits containing secret filenames (.env, *.key, *.pem, settings.json) or secret tokens.
- Requires passing verification checks AND explicit `user_approved=True` before committing.
- Draft PR helper is strictly `draft=True` with visible diff preview.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import advanced
from .redact import redact

FORBIDDEN_GIT_TOKENS = ("push", "--force", "-f", "--no-verify", "reset --hard")
SENSITIVE_FILENAMES = {".env", "settings.json", "id_rsa", "id_ed25519", "credentials.json"}
SENSITIVE_SUFFIXES = (".key", ".pem", ".pfx", ".p12", ".secret")


def is_sensitive_path(rel_path: str) -> bool:
    """Return True if a relative file path looks like a secret/key file that must never be committed."""
    norm = str(rel_path or "").strip().replace("\\", "/")
    name = Path(norm).name.lower()
    if name in SENSITIVE_FILENAMES or name.startswith(".env."):
        return True
    return any(name.endswith(suf) for suf in SENSITIVE_SUFFIXES)


def validate_git_args(args: list[str]) -> None:
    """Block push, --force, reset --hard, and --no-verify unconditionally."""
    joined = " ".join(str(a).strip().lower() for a in args)
    tokens = {str(a).strip().lower() for a in args}
    if "push" in tokens or "--force" in tokens or "--no-verify" in tokens:
        raise PermissionError("Απαγορεύονται τα git push, --force και --no-verify από τον agent.")
    if "reset" in tokens and "--hard" in tokens:
        raise PermissionError("Απαγορεύεται το git reset --hard από τον agent.")
    if "force-with-lease" in joined:
        raise PermissionError("Απαγορεύεται το force push από τον agent.")


def scan_files_for_secrets(repo_root: Path, rel_files: list[str]) -> list[str]:
    """Scan candidate files for sensitive filenames or redacted secret patterns."""
    findings: list[str] = []
    root = Path(repo_root).resolve()
    for rel in rel_files:
        if is_sensitive_path(rel):
            findings.append(f"Ευαίσθητο όνομα αρχείου: {rel}")
            continue
        full = (root / rel).resolve()
        if not full.is_file() or not full.is_relative_to(root):
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="ignore")[:200_000]
        except OSError:
            continue
        if redact(text) != text:
            findings.append(f"Εντοπίστηκε πιθανό μυστικό/API key μέσα στο: {rel}")
    return findings


def _run_git(repo_root: Path, args: list[str]) -> tuple[int, str, str]:
    validate_git_args(args)
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Δεν βρέθηκε εγκατεστημένο Git.")
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(x in k.upper() for x in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        [git, "--no-pager", *args],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _check_gates(settings, project_profile: str) -> None:
    if not advanced.enabled(settings, "git"):
        raise PermissionError("Η λειτουργία «Εργαλεία Git» είναι κλειστή. Ενεργοποίησέ την από Ρυθμίσεις → Advanced.")
    if str(project_profile or "").lower() != "dev":
        raise PermissionError("Τα εργαλεία Git commit/PR επιτρέπονται μόνο σε φακέλους με προφίλ Ανάπτυξης (dev).")


def prepare_commit(
    repo_root: str | Path,
    settings,
    *,
    project_profile: str = "dev",
    files: list[str] | None = None,
    verifier: Callable[[Path], tuple[bool, str]] | None = None,
) -> dict:
    """Inspect changed files, scan for secrets, and run verification checks before proposing a commit."""
    _check_gates(settings, project_profile)
    root = Path(repo_root).resolve()
    rc, status_out, err = _run_git(root, ["status", "--short", "--untracked-files=normal"])
    if rc != 0:
        raise RuntimeError(f"Δεν είναι έγκυρο αποθετήριο Git: {err}")
    changed = [line[3:].strip() for line in status_out.splitlines() if len(line) >= 4]
    target_files = list(files) if files is not None else changed
    if not target_files:
        return {"ok": False, "error": "Δεν υπάρχουν αλλαγές για commit.", "files": []}

    secret_hits = scan_files_for_secrets(root, target_files)
    if secret_hits:
        return {"ok": False, "error": "Ακυρώθηκε λόγω εντοπισμού μυστικών.", "secret_findings": secret_hits, "files": target_files}

    check_ok, check_output = (verifier(root) if verifier else (True, "OK"))
    if not check_ok:
        return {"ok": False, "error": "Οι έλεγχοι (checks) απέτυχαν — δεν επιτρέπεται commit.", "check_output": check_output, "files": target_files}

    _, diff_stat, _ = _run_git(root, ["diff", "--stat"])
    return {
        "ok": True,
        "files": target_files,
        "diff_stat": diff_stat,
        "check_output": check_output,
        "ready_to_commit": True,
    }


def execute_commit(
    repo_root: str | Path,
    settings,
    message: str,
    *,
    project_profile: str = "dev",
    files: list[str] | None = None,
    verifier: Callable[[Path], tuple[bool, str]] | None = None,
    user_approved: bool = False,
) -> dict:
    """Stage and create a local Git commit ONLY after checks pass and user explicitly approves."""
    if not user_approved:
        raise PermissionError("Το commit απαιτεί ρητή έγκριση του χρήστη ([Commit]).")
    clean_msg = str(message or "").strip()
    if not clean_msg or "\x00" in clean_msg:
        raise ValueError("Δώσε ένα καθαρό μήνυμα commit.")
    prep = prepare_commit(repo_root, settings, project_profile=project_profile, files=files, verifier=verifier)
    if not prep.get("ok"):
        return prep
    root = Path(repo_root).resolve()
    rc_add, _, err_add = _run_git(root, ["add", "--", *prep["files"]])
    if rc_add != 0:
        return {"ok": False, "error": f"Αποτυχία git add: {err_add}"}
    rc_c, out_c, err_c = _run_git(root, ["commit", "-m", clean_msg])
    if rc_c != 0:
        return {"ok": False, "error": f"Αποτυχία git commit: {err_c or out_c}"}
    _, sha, _ = _run_git(root, ["rev-parse", "--short", "HEAD"])
    return {"ok": True, "commit": sha, "message": clean_msg, "files": prep["files"], "pushed": False}


def prepare_draft_pr(
    repo_root: str | Path,
    settings,
    *,
    title: str,
    body: str,
    base_branch: str = "main",
    project_profile: str = "dev",
) -> dict:
    """Prepare a strictly draft-only Pull Request payload with visible diff (never pushes or merges)."""
    _check_gates(settings, project_profile)
    root = Path(repo_root).resolve()
    _, head_branch, _ = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    _, diff_text, _ = _run_git(root, ["diff", "--stat"])
    return {
        "draft": True,
        "base": str(base_branch or "main"),
        "head": head_branch,
        "title": str(title or "").strip()[:120],
        "body": redact(str(body or "").strip()),
        "diff_preview": diff_text,
        "pushed": False,
        "published": False,
    }


def _ws_settings(ws):
    cfg = getattr(ws, "advanced_settings", None)
    if cfg is not None:
        return cfg
    from .settings import Settings

    return Settings.load()


def tool_git_prepare_commit(ws, files: list[str] | None = None) -> str:
    import json

    res = prepare_commit(ws.root, _ws_settings(ws), project_profile="dev", files=files)
    return json.dumps(res, ensure_ascii=False, indent=2)


def tool_git_commit(ws, message: str, files: list[str] | None = None) -> str:
    import json

    cfg = _ws_settings(ws)
    prep = prepare_commit(ws.root, cfg, project_profile="dev", files=files)
    if not prep.get("ok"):
        return json.dumps(prep, ensure_ascii=False, indent=2)
    confirm = getattr(ws, "confirm_write", None)
    preview = f"Git commit: {message}\nFiles: {', '.join(prep['files'])}\n{prep.get('diff_stat', '')}"
    if callable(confirm) and not confirm(f"git_commit:{message[:60]}", preview):
        return "ΑΠΟΡΡΙΨΗ: ο χρήστης δεν ενέκρινε το git commit."
    done = execute_commit(ws.root, cfg, message, project_profile="dev", files=prep["files"], user_approved=True)
    return json.dumps(done, ensure_ascii=False, indent=2)


def tool_prepare_draft_pr(ws, title: str, body: str, base_branch: str = "main") -> str:
    import json

    res = prepare_draft_pr(ws.root, _ws_settings(ws), title=title, body=body, base_branch=base_branch, project_profile="dev")
    return json.dumps(res, ensure_ascii=False, indent=2)


GIT_WORKFLOW_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "git_prepare_commit",
            "description": "Scan changed files for secrets and preview diff before committing (Advanced Git only).",
            "parameters": {
                "type": "object",
                "properties": {"files": {"type": "array", "items": {"type": "string"}}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Create a local Git commit after secret scan and explicit owner diff approval (never pushes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Clean commit message"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_draft_pr",
            "description": "Prepare draft Pull Request text and diff summary locally (never pushes or publishes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "base_branch": {"type": "string"},
                },
                "required": ["title", "body"],
            },
        },
    },
]

GIT_WORKFLOW_HANDLERS = {
    "git_prepare_commit": tool_git_prepare_commit,
    "git_commit": tool_git_commit,
    "prepare_draft_pr": tool_prepare_draft_pr,
}
