"""Anonymized system health check and 1-click Support Diagnostics Bundle (.zip).

Strictly excludes API keys, MCP secrets, channel bot tokens, user prompts, and project file contents.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import platform
import shutil
import time
import zipfile
from pathlib import Path

from .secret_store import is_dpapi_available, redact_secrets

APP_VERSION = "2.5.0"

_TS_GRAMMARS = (
    "tree_sitter_c",
    "tree_sitter_cpp",
    "tree_sitter_rust",
    "tree_sitter_java",
    "tree_sitter_c_sharp",
    "tree_sitter_php",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_html",
    "tree_sitter_css",
    "tree_sitter_xml",
)


def _has_mod(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _collect_known_secrets(state) -> tuple[str, ...]:
    secrets = []
    if state is None:
        return ()
    try:
        from .settings import unmask_secret

        for val in (getattr(state.settings, "keys", None) or {}).values():
            raw = unmask_secret(val)
            if raw and len(raw) >= 4:
                secrets.append(raw)
        if getattr(state, "mcp", None):
            for cfg in state.mcp.configured().values():
                for bucket in ("env", "headers"):
                    for v in (cfg.get(bucket) or {}).values():
                        raw = unmask_secret(v)
                        if raw and len(raw) >= 4:
                            secrets.append(raw)
    except Exception:
        pass
    return tuple(secrets)


def health_report(state=None) -> dict:
    """Return a clean, secret-free capability & runtime health matrix."""
    from .network_tools import find_chromium_binary

    root = Path(__file__).resolve().parents[1]
    proj_root = state._files_root() if (state is not None and hasattr(state, "_files_root")) else root
    try:
        usage = shutil.disk_usage(proj_root)
        disk_free_gb = round(usage.free / (1024**3), 2)
        disk_total_gb = round(usage.total / (1024**3), 2)
    except OSError:
        disk_free_gb = 0.0
        disk_total_gb = 0.0

    ts_core = _has_mod("tree_sitter")
    ts_installed = [g.removeprefix("tree_sitter_") for g in _TS_GRAMMARS if _has_mod(g)]
    chromium = find_chromium_binary()
    tpl_nm = (Path(__file__).with_name("template_parsers") / "node_modules").is_dir()

    checks = {
        "dpapi_vault": {"ok": is_dpapi_available(), "label": "Κρυπτογράφηση Windows DPAPI"},
        "chromium_engine": {"ok": bool(chromium), "label": "Μηχανή Chromium (Web & PDF)", "binary": Path(chromium).name if chromium else ""},
        "tree_sitter": {"ok": ts_core and len(ts_installed) >= 10, "label": f"Tree-sitter Parsers ({len(ts_installed)}/{len(_TS_GRAMMARS)})", "grammars": ts_installed},
        "template_parsers": {"ok": bool(shutil.which("node")) and tpl_nm, "label": "Framework Parsers (Vue/Svelte/Angular)"},
        "office_docs": {"ok": all(_has_mod(m) for m in ("docx", "openpyxl", "pypdf")), "label": "Έγγραφα Word / Excel / PDF"},
        "git": {"ok": bool(shutil.which("git")), "label": "Git (Checkpoints & Diff)"},
        "voice_stt": {"ok": _has_mod("faster_whisper"), "label": "Τοπική Φωνή (Whisper STT)"},
    }

    known_secrets = _collect_known_secrets(state)
    mcp_summary = []
    scheduled_counts = {}
    runtime = {
        "connected": False,
        "provider": "",
        "model": "",
        "mode": "",
        "advanced_view": False,
        "last_error": "",
    }
    if state is not None:
        runtime.update(
            connected=bool(getattr(state, "agent", None)),
            provider=str(getattr(state.settings, "provider", "") or ""),
            model=str((state.agent.config.model if getattr(state, "agent", None) else getattr(state.settings, "model", "")) or ""),
            mode=str(state.agent.mode if getattr(state, "agent", None) else ""),
            advanced_view=bool(getattr(state.settings, "advanced_view", False)),
            last_error=redact_secrets(str(getattr(state, "error", "") or "")[:500], known_secrets),
        )
        if getattr(state, "mcp", None):
            try:
                pub = state.mcp.public_configs()
                active = set(state.mcp.servers.keys())
                for name, cfg in pub.items():
                    mcp_summary.append(
                        {
                            "name": name,
                            "transport": cfg.get("transport", "stdio"),
                            "connected": name in active,
                            "autoConnect": bool(cfg.get("autoConnect", False)),
                            "env_keys_count": len(cfg.get("envKeys") or []),
                            "header_keys_count": len(cfg.get("headerKeys") or []),
                        }
                    )
            except Exception:
                pass
        try:
            from .scheduler import queue_for

            for job in queue_for(state).view():
                st = str(job.get("status") or "unknown")
                scheduled_counts[st] = scheduled_counts.get(st, 0) + 1
        except Exception:
            pass

    return {
        "app_version": APP_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "system": {
            "os": f"{platform.system()} {platform.release()}",
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_cores": os.cpu_count() or 1,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
        },
        "checks": checks,
        "runtime": runtime,
        "mcp_servers": mcp_summary,
        "scheduled_jobs_summary": scheduled_counts,
        "all_core_ok": all(checks[k]["ok"] for k in ("dpapi_vault", "office_docs", "git")),
    }


def _render_summary_md(report: dict) -> str:
    lines = [
        f"# Αναφορά Διαγνωστικών Akatikito v{report['app_version']}",
        f"- **Ημερομηνία:** {report['generated_at']}",
        f"- **Σύστημα:** {report['system']['os']} ({report['system']['architecture']}) · Python {report['system']['python']}",
        f"- **Ελεύθερος δίσκος:** {report['system']['disk_free_gb']} GB / {report['system']['disk_total_gb']} GB",
        "",
        "## Έλεγχος Δυνατοτήτων",
    ]
    for key, item in report["checks"].items():
        mark = "OK" if item["ok"] else "ΕΛΛΕΙΠΕΙ"
        lines.append(f"- [{mark}] **{item['label']}** (`{key}`)")
    lines.append("")
    lines.append("## Κατάσταση Λειτουργίας")
    rt = report["runtime"]
    lines.append(f"- Πάροχος: `{rt['provider'] or '—'}` · Μοντέλο: `{rt['model'] or '—'}` · Λειτουργία: `{rt['mode'] or '—'}`")
    lines.append(f"- MCP Servers: {len(report['mcp_servers'])} ρυθμισμένοι")
    lines.append(f"- Προγραμματισμένες εργασίες: {json.dumps(report['scheduled_jobs_summary'], ensure_ascii=False)}")
    if rt["last_error"]:
        lines.append(f"- Τελευταίο σφάλμα (ανωνυμοποιημένο): `{rt['last_error']}`")
    lines.append("")
    lines.append("_Σημείωση απορρήτου: Το πακέτο δεν περιέχει API keys, tokens, μυστικά MCP, συνομιλίες ή περιεχόμενα αρχείων._")
    return "\n".join(lines) + "\n"


def export_diagnostics_zip(state, dest_path: Path | None = None, allow_dialog: bool = True, data_dir: Path | None = None) -> dict:
    """Create a sanitized .zip diagnostics bundle via Save dialog (desktop) or .agent/exports/."""
    from .diagnostics_bundle import build_bundle_files
    from .workspace import Workspace

    report = health_report(state)
    known_secrets = _collect_known_secrets(state)
    files = build_bundle_files(state, report, _render_summary_md(report), known_secrets, data_dir=data_dir)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in files.items():
            zf.writestr(arcname, content.encode("utf-8"))
    zip_bytes = buf.getvalue()

    root = state._files_root() if (state is not None and hasattr(state, "_files_root")) else Path.cwd()
    ws = Workspace(root)
    filename = f"akatikito-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    target = Path(dest_path) if dest_path is not None else None

    if target is None and allow_dialog:
        try:
            import webview

            windows = getattr(webview, "windows", None) or []
            if windows:
                save_mode = getattr(getattr(webview, "FileDialog", None), "SAVE", getattr(webview, "SAVE_DIALOG", None))
                picked = windows[0].create_file_dialog(
                    save_mode,
                    directory=str(root),
                    save_filename=filename,
                    file_types=("ZIP Archive (*.zip)",),
                )
                if not picked:
                    return {"cancelled": True, "report": report}
                chosen = picked[0] if isinstance(picked, (list, tuple)) else picked
                if not chosen:
                    return {"cancelled": True, "report": report}
                target = Path(chosen).resolve()
        except Exception:
            target = None

    if target is None:
        target = ws.resolve(f".agent/exports/{filename}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name("." + target.name + ".tmp")
    try:
        tmp.write_bytes(zip_bytes)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    rel = ws.relative(target) if target.is_relative_to(ws.root) else str(target)
    return {"ok": True, "path": rel, "full_path": str(target), "size": len(zip_bytes), "report": report}
