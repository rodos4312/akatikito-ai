"""Akatikito CLI client (PRO-PLAN D2): talks to the same local API, enforces D0 Advanced gate."""
from __future__ import annotations

import argparse
import http.client
import json
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import advanced
from .command_policy import command_risk
from .doctor import Doctor
from .settings import DEFAULT_PATH, Settings
from .usage_ledger import UsageLedger


def evaluate_terminal_approval(
    command: str,
    *,
    auto_yes: bool = False,
    prompt_fn: Callable[[str], str] | None = None,
) -> tuple[bool, str]:
    """Prompt `[y/N]` in terminal; `--yes` approves non-dangerous actions ONLY (never dangerous)."""
    risk = command_risk(command)
    if risk == "safe":
        return True, "safe"
    if risk == "dangerous":
        if auto_yes:
            return False, "Οι επικίνδυνες εντολές δεν εγκρίνονται ποτέ αυτόματα με --yes."
        ask = prompt_fn or input
        ans = str(ask(f'ΠΡΟΣΟΧΗ (επικίνδυνη εντολή): Να εκτελεστεί "{command}"; [y/N] ') or "").strip().lower()
        return ans in ("y", "yes", "ν", "ναι"), "dangerous-prompted"
    if auto_yes:
        return True, "auto-yes"
    ask = prompt_fn or input
    ans = str(ask(f'Να εκτελεστεί "{command}"; [y/N] ') or "").strip().lower()
    return ans in ("y", "yes", "ν", "ναι"), "prompted"


def call_local_api(base_url: str, token: str, path: str, payload: dict | None = None, *, method: str = "POST") -> dict:
    """Send a loopback-only authenticated JSON request to the local Akatikito API server."""
    parts = urlsplit(str(base_url or ""))
    host = (parts.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Το CLI επικοινωνεί αποκλειστικά με το τοπικό API στο 127.0.0.1 / localhost.")
    conn = http.client.HTTPConnection(host, parts.port or 80, timeout=30)
    try:
        headers = {"X-Akatikito-Token": str(token or "")}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw.strip() else {}
        return {"status": resp.status, "data": parsed}
    finally:
        conn.close()


def _require_cli_enabled(cfg: Settings) -> str | None:
    if not advanced.enabled(cfg, "cli"):
        return "Η λειτουργία «Terminal (akatikito CLI)» είναι κλειστή. Ενεργοποίησέ την από Ρυθμίσεις → Advanced."
    return None


def _run_doctor_cmd(data_dir: Path, project: Path) -> dict:
    doc = Doctor(project_root=project, data_dir=data_dir)
    return doc.diagnose()


def _run_usage_cmd(data_dir: Path, month: str | None = None) -> dict:
    ledger = UsageLedger(data_dir / "usage.json")
    return ledger.monthly_insights(month=month)


def _compose_prompt(prompt: str, stdin_text: str) -> str:
    extra = str(stdin_text or "").strip()
    if not extra:
        return str(prompt or "").strip()
    return f"{prompt.strip()}\n\n[Είσοδος από pipe]\n{extra[:20_000]}"


def _dispatch_ask_or_code(
    args: argparse.Namespace,
    cfg: Settings,
    *,
    api_url: str,
    api_token: str,
    stdin_text: str,
    mode: str,
) -> dict:
    err = _require_cli_enabled(cfg)
    if err:
        return {"ok": False, "error": err}
    full_prompt = _compose_prompt(" ".join(args.prompt).strip(), stdin_text)
    if not full_prompt:
        return {"ok": False, "error": "Δώσε μια ερώτηση ή εντολή."}
    if not api_url:
        return {"ok": False, "error": "Δεν βρέθηκε ενεργός τοπικός server Akatikito (--api-url)."}
    res = call_local_api(api_url, api_token, "/api/send", {"text": full_prompt, "mode": mode, "yes": bool(getattr(args, "yes", False))})
    return {"ok": res["status"] < 400, "status": res["status"], "response": res["data"]}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="akatikito", description="Akatikito AI local CLI client")
    parser.add_argument("--settings", default="", help="Path to settings.json")
    parser.add_argument("--api-url", default="", help="Loopback local API URL (e.g. http://127.0.0.1:8765)")
    parser.add_argument("--token", default="", help="Local API auth token")
    sub = parser.add_subparsers(dest="cmd")

    p_ask = sub.add_parser("ask", help="Read-only question")
    p_ask.add_argument("prompt", nargs="+")

    p_code = sub.add_parser("code", help="Coding task with terminal approvals")
    p_code.add_argument("prompt", nargs="+")
    p_code.add_argument("--yes", action="store_true", help="Auto-approve non-dangerous profile commands")

    sub.add_parser("doctor", help="Run read-only Doctor diagnostics")

    p_usage = sub.add_parser("usage", help="Show monthly token and cost summary")
    p_usage.add_argument("--month", default="", help="YYYY-MM")

    p_plugin = sub.add_parser("plugin", help="Developer plugin SDK commands")
    p_plugin.add_argument("action", choices=["new", "sign"])
    p_plugin.add_argument("target", help="Plugin ID (new) or folder path (sign)")
    p_plugin.add_argument("--dir", default=".", help="Parent directory for new plugin")
    p_plugin.add_argument("--key", default="", help="Signing key hex for sign")
    return parser


def run_cli(
    argv: list[str],
    *,
    stdin_text: str = "",
    project: Path | None = None,
) -> tuple[int, dict]:
    """Execute a CLI invocation and return `(exit_code, result_dict)`."""
    known_cmds = {"ask", "code", "doctor", "usage", "plugin", "-h", "--help"}
    normalized = list(argv)
    # Allow shorthand: `akatikito "question"` -> `akatikito ask "question"`
    idx = 0
    while idx < len(normalized) and normalized[idx].startswith("--"):
        idx += 2
    if idx < len(normalized) and normalized[idx] not in known_cmds:
        normalized.insert(idx, "ask")

    args = _build_parser().parse_args(normalized)
    settings_path = Path(args.settings) if args.settings else DEFAULT_PATH
    cfg = Settings.load(settings_path)
    data_dir = settings_path.parent
    proj = project or Path.cwd()

    if args.cmd == "doctor":
        return 0, _run_doctor_cmd(data_dir, proj)
    if args.cmd == "usage":
        return 0, _run_usage_cmd(data_dir, args.month or None)
    if args.cmd in ("ask", "code"):
        out = _dispatch_ask_or_code(args, cfg, api_url=args.api_url, api_token=args.token, stdin_text=stdin_text, mode=args.cmd)
        return (0 if out.get("ok") else 1), out
    if args.cmd == "plugin":
        err = _require_cli_enabled(cfg)
        if err:
            return 1, {"ok": False, "error": err}
        from .plugin_sdk import scaffold_plugin, sign_plugin

        if args.action == "new":
            created = scaffold_plugin(Path(args.dir), args.target)
            return 0, {"ok": True, **created}
        sig = sign_plugin(Path(args.target), args.key)
        return 0, {"ok": True, **sig}
    return 1, {"ok": False, "error": "Χρήση: akatikito [ask|code|doctor|usage|plugin]"}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    piped = "" if sys.stdin.isatty() else sys.stdin.read()
    code, payload = run_cli(sys.argv[1:] if argv is None else argv, stdin_text=piped)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
