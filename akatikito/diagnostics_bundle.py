"""Sanitized Support Diagnostics Bundle builder with user-path & secret scrubbing (PRO-PLAN B4).

Strictly excludes API keys, tokens, user conversations, memory contents, project file contents,
and OS usernames in file paths. Never transmits anything over the network.
"""
from __future__ import annotations

import json
import platform
import re
import urllib.parse
from pathlib import Path

from .redact import redact as redact_text
from .secret_store import redact_secrets

_WIN_USER_RE = re.compile(r"([A-Za-z]:\\{1,2}Users\\{1,2})[^\\/\r\n\"']+")
_WIN_USER_FWD_RE = re.compile(r"([A-Za-z]:/Users/)[^\\/\r\n\"']+")
_UNIX_USER_RE = re.compile(r"(/(?:Users|home)/)[^/\r\n\"']+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GITHUB_ISSUE_NEW_URL = "https://github.com/rodos4312/akatikito-ai/issues/new"


def scrub_user_paths(text: str) -> str:
    """Replace OS user directory names with `<user>` in Windows and POSIX paths."""
    out = str(text or "")
    out = _WIN_USER_RE.sub(r"\1<user>", out)
    out = _WIN_USER_FWD_RE.sub(r"\1<user>", out)
    out = _UNIX_USER_RE.sub(r"\1<user>", out)
    return out


def sanitize_bundle_text(text: str, known_secrets: tuple[str, ...] = ()) -> str:
    """Apply secret redaction (pattern + exact known secrets), email scrubbing, and OS username path scrubbing."""
    out = redact_text(str(text or ""))
    out = redact_secrets(out, known_secrets)
    for sec in known_secrets:
        if sec and len(sec) >= 4 and sec in out:
            out = out.replace(sec, "***")
    out = _EMAIL_RE.sub("<email>", out)
    return scrub_user_paths(out)


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return raw[-max_lines:]
    except OSError:
        return []


def _collect_logs(data_dir: Path, known_secrets: tuple[str, ...]) -> str:
    lines: list[str] = []
    for candidate in sorted(data_dir.glob("*.log"))[:5]:
        lines.extend(_tail_lines(candidate, 500))
    if not lines:
        return "(Κανένα αρχείο log.)\n"
    return sanitize_bundle_text("\n".join(lines[-500:]) + "\n", known_secrets)


def _strip_audit_paths(row: dict) -> dict:
    clean = {}
    for key, val in row.items():
        if key in ("content", "prompt", "text", "body", "memory"):
            continue
        if isinstance(val, str) and ("\\" in val or "/" in val) and key in ("path", "file", "target", "root", "cwd"):
            clean[key] = Path(val).name or "<path>"
        elif isinstance(val, dict):
            clean[key] = _strip_audit_paths(val)
        else:
            clean[key] = val
    return clean


def _collect_audit(data_dir: Path, known_secrets: tuple[str, ...]) -> str:
    audit_path = data_dir / "audit.jsonl"
    raw_lines = _tail_lines(audit_path, 200)
    if not raw_lines:
        return ""
    sanitized_rows: list[str] = []
    for line in raw_lines:
        try:
            parsed = json.loads(line)
        except ValueError:
            sanitized_rows.append(line)
            continue
        if isinstance(parsed, dict):
            sanitized_rows.append(json.dumps(_strip_audit_paths(parsed), ensure_ascii=False))
        else:
            sanitized_rows.append(line)
    return sanitize_bundle_text("\n".join(sanitized_rows) + "\n", known_secrets)


def _collect_doctor(state, data_dir: Path, known_secrets: tuple[str, ...]) -> str:
    try:
        from .doctor import Doctor

        root = state._files_root() if (state is not None and hasattr(state, "_files_root")) else Path.cwd()
        result = Doctor(project_root=root, data_dir=data_dir, state=state).diagnose()
        return sanitize_bundle_text(json.dumps(result, ensure_ascii=False, indent=2), known_secrets)
    except (OSError, ValueError, RuntimeError) as exc:
        return sanitize_bundle_text(json.dumps({"error": str(exc)}, ensure_ascii=False), known_secrets)


def _collect_errors(state, data_dir: Path, known_secrets: tuple[str, ...]) -> str:
    entries: list[str] = []
    if state is not None and getattr(state, "error", ""):
        entries.append(f"state.error: {state.error}")
    for line in _tail_lines(data_dir / "errors.log", 200):
        entries.append(line)
    if not entries:
        return "(Κανένα πρόσφατο σφάλμα.)\n"
    return sanitize_bundle_text("\n".join(entries[-20:]) + "\n", known_secrets)


def build_bundle_files(
    state,
    report: dict,
    summary_md: str,
    known_secrets: tuple[str, ...],
    *,
    data_dir: Path | None = None,
) -> dict[str, str]:
    """Assemble all sanitized text files for the support diagnostics ZIP bundle."""
    from .settings import DEFAULT_PATH

    d_dir = Path(data_dir) if data_dir else DEFAULT_PATH.parent
    raw_json = sanitize_bundle_text(json.dumps(report, ensure_ascii=False, indent=2), known_secrets)
    safe_summary = sanitize_bundle_text(summary_md, known_secrets)
    return {
        "summary.txt": safe_summary,
        "SUMMARY.md": safe_summary,
        "diagnostics.json": raw_json,
        "doctor.json": _collect_doctor(state, d_dir, known_secrets),
        "logs/last-500.txt": _collect_logs(d_dir, known_secrets),
        "audit/last-200.jsonl": _collect_audit(d_dir, known_secrets),
        "errors.txt": _collect_errors(state, d_dir, known_secrets),
    }


def build_github_issue_url(version: str = "", os_label: str = "") -> str:
    """Build a pre-filled GitHub new-issue URL containing ONLY app version and OS (zero personal data)."""
    from .diagnostics import APP_VERSION

    ver = str(version or APP_VERSION).strip()[:32]
    os_txt = str(os_label or f"{platform.system()} {platform.release()}").strip()[:48]
    body = (
        f"**Έκδοση / Version:** Akatikito v{ver}\n"
        f"**Σύστημα / OS:** {os_txt}\n\n"
        "## Τι συνέβη / What happened\n"
        "(Περίγραψε σύντομα τι προσπάθησες να κάνεις και τι εμφανίστηκε.)\n\n"
        "## Πακέτο διαγνωστικών / Diagnostics ZIP (προαιρετικό)\n"
        "(Αν αποθήκευσες το καθαρισμένο zip διαγνωστικών, μπορείς να το σύρεις εδώ.)\n"
    )
    qs = urllib.parse.urlencode({"title": f"[Bug] Akatikito v{ver}: ", "body": body})
    return f"{GITHUB_ISSUE_NEW_URL}?{qs}"


def preview_diagnostics_bundle(state, *, data_dir: Path | None = None) -> dict:
    """Return file manifest and safe previews for user inspection before saving the ZIP."""
    from .diagnostics import _collect_known_secrets, _render_summary_md, health_report

    report = health_report(state)
    known = _collect_known_secrets(state)
    files = build_bundle_files(state, report, _render_summary_md(report), known, data_dir=data_dir)
    return {
        "auto_upload": False,
        "issue_url": build_github_issue_url(),
        "files": [
            {"name": name, "bytes": len(body.encode("utf-8")), "preview": body[:400]}
            for name, body in files.items()
        ],
    }
