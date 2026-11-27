"""v2.6 «🩺 Γιατρός» checks & safe 1-click repairs: skills security, context archive & UI assets.

Keeps doctor_checks.py well under the 800-line ceiling while giving humans full visibility
into the v2.6 subsystems (Skills Security Guard, compaction/context archive & modular UI assets).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .skills_guard import scan_skill_text

_ARCHIVE_OLD_DAYS = 30
_ARCHIVE_WARN_BYTES = 100 * 1024 * 1024  # 100 MiB
_UI_ASSET_RE = re.compile(r"""(?:src|href)=["']/ui/([^"'?#]+)["']""")


def _skill_scan_roots(doc) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = [
        (doc.app_root / "skills", "builtin"),
        (doc.data_dir / "skills", "user"),
        (doc.project_root / ".akatikito" / "skills", "project"),
    ]
    if doc.project_root.resolve() != doc.app_root.resolve():
        roots.append((doc.project_root / "skills", "legacy-project"))
    return roots


def check_skills_security(doc, make_item) -> dict[str, Any]:
    """Read-only static security scan across installed SKILL.md files via skills_guard."""
    dangerous: list[str] = []
    caution: list[str] = []
    unreadable: list[str] = []
    scanned = 0
    for folder, source in _skill_scan_roots(doc):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md"))[:100]:
            if path.is_symlink() or not path.is_file():
                continue
            scanned += 1
            label = f"{source}/{path.stem}"
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                unreadable.append(label)
                continue
            res = scan_skill_text(path.stem, text)
            if res.verdict == "dangerous":
                dangerous.append(label)
            elif res.verdict == "caution":
                caution.append(label)
    if dangerous:
        return make_item(
            "skills_security",
            "Ασφάλεια συνταγών (Skills Guard)",
            "error",
            f"Εντοπίστηκαν επικίνδυνα μοτίβα ασφάλειας σε: {', '.join(dangerous[:5])}. Έλεγξε ή αφαίρεσέ τες χειροκίνητα.",
            fixable=False,
            meta={"scanned": scanned, "dangerous": dangerous, "caution": caution, "unreadable": unreadable},
        )
    if caution or unreadable:
        flagged = caution + unreadable
        return make_item(
            "skills_security",
            "Ασφάλεια συνταγών (Skills Guard)",
            "warn",
            f"Βρέθηκαν προειδοποιήσεις ή μη αναγνώσιμα SKILL.md ({len(flagged)}): {', '.join(flagged[:5])}.",
            fixable=False,
            meta={"scanned": scanned, "dangerous": [], "caution": caution, "unreadable": unreadable},
        )
    msg = (
        f"Όλες οι συνταγές ({scanned}) πέρασαν τον στατικό έλεγχο ασφάλειας (0 ευρήματα)."
        if scanned
        else "Δεν υπάρχουν εγκατεστημένες συνταγές προς έλεγχο."
    )
    return make_item(
        "skills_security",
        "Ασφάλεια συνταγών (Skills Guard)",
        "ok",
        msg,
        meta={"scanned": scanned, "dangerous": [], "caution": [], "unreadable": []},
    )


def _scan_context_archive(doc, days: int = _ARCHIVE_OLD_DAYS) -> dict[str, Any]:
    cutoff = time.time() - days * 86400
    corrupt: list[Path] = []
    stale: list[Path] = []
    stale_bytes = total_files = total_bytes = 0
    ctx_dir = doc.project_root / ".agent" / "context"
    out_dir = doc.project_root / ".agent" / "outputs"
    for folder, is_json in ((ctx_dir, True), (out_dir, False)):
        if not folder.is_dir():
            continue
        pattern = "*.json" if is_json else "*.txt"
        for entry in folder.glob(pattern):
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                st = entry.stat()
            except OSError:
                continue
            total_files += 1
            total_bytes += st.st_size
            bad_json = False
            if is_json:
                try:
                    parsed = json.loads(entry.read_text(encoding="utf-8"))
                    bad_json = not (isinstance(parsed, dict) and "value" in parsed)
                except (OSError, ValueError, UnicodeError):
                    bad_json = True
            if bad_json:
                corrupt.append(entry)
                stale_bytes += st.st_size
            elif st.st_mtime < cutoff:
                stale.append(entry)
                stale_bytes += st.st_size
    return {
        "corrupt": corrupt,
        "stale": stale,
        "stale_bytes": stale_bytes,
        "total_files": total_files,
        "total_bytes": total_bytes,
    }


def check_context_archive(doc, make_item) -> dict[str, Any]:
    """Check .agent/context/ recall archives & .agent/outputs/ spill files for corruption or bloat."""
    info = _scan_context_archive(doc)
    corrupt: list[Path] = info["corrupt"]
    stale: list[Path] = info["stale"]
    total_files: int = info["total_files"]
    total_bytes: int = info["total_bytes"]
    total_mb = round(total_bytes / (1024 * 1024), 2)
    clean_count = len(corrupt) + len(stale)
    if clean_count:
        clean_mb = round(info["stale_bytes"] / (1024 * 1024), 2)
        parts = []
        if corrupt:
            parts.append(f"{len(corrupt)} κατεστραμμένα JSON")
        if stale:
            parts.append(f"{len(stale)} παλιά αρχεία >30 ημερών")
        return make_item(
            "context_archive",
            "Αρχείο συμπίεσης & context cache (.agent/context)",
            "warn",
            f"Βρέθηκαν {' και '.join(parts)} ({clean_mb} MB από σύνολο {total_files} αρχείων / {total_mb} MB).",
            fixable=True,
            fix_label="Καθάρισμα αρχείου",
            fix_preview=f"Θα διαγράψει ΜΟΝΟ τα {clean_count} παλιά (>30 ημερών) ή χαλασμένα αρχεία από .agent/context/ και .agent/outputs/. Τα πρόσφατα αρχεία ανάκλησης μένουν ανέπαφα.",
            meta={"corrupt_count": len(corrupt), "stale_count": len(stale), "total_count": total_files, "total_bytes": total_bytes},
        )
    if total_bytes >= _ARCHIVE_WARN_BYTES:
        return make_item(
            "context_archive",
            "Αρχείο συμπίεσης & context cache (.agent/context)",
            "warn",
            f"Το αρχείο συμπίεσης/εξόδων έχει φτάσει τα {total_mb} MB ({total_files} πρόσφατα αρχεία <30 ημερών).",
            fixable=False,
            meta={"corrupt_count": 0, "stale_count": 0, "total_count": total_files, "total_bytes": total_bytes},
        )
    return make_item(
        "context_archive",
        "Αρχείο συμπίεσης & context cache (.agent/context)",
        "ok",
        f"Καθαρό αρχείο συμπίεσης & context ({total_files} αρχεία, {total_mb} MB).",
        meta={"corrupt_count": 0, "stale_count": 0, "total_count": total_files, "total_bytes": total_bytes},
    )


def fix_context_archive(doc) -> tuple[list[str], str]:
    """Safely remove ONLY corrupted or >30-day files from .agent/context/ and .agent/outputs/."""
    info = _scan_context_archive(doc)
    targets = list(info["corrupt"]) + list(info["stale"])
    touched: list[str] = []
    for p in targets:
        try:
            p.unlink(missing_ok=True)
            touched.append(str(p))
        except OSError:
            continue
    freed_mb = round(info["stale_bytes"] / (1024 * 1024), 2)
    return touched, f"Καθαρίστηκαν {len(touched)} παλιά/χαλασμένα αρχεία από .agent/context & outputs ({freed_mb} MB)."


def _check_js_syntax_batch(js_paths: list[Path]) -> list[str]:
    node_bin = shutil.which("node")
    if not node_bin or not js_paths:
        return []
    script = (
        "const fs=require('fs'),vm=require('vm'),bad=[];"
        "for(const f of process.argv.slice(1)){"
        "try{new vm.Script(fs.readFileSync(f,'utf8'),{filename:f});}"
        "catch(e){bad.push(f);}}"
        "process.stdout.write(JSON.stringify(bad));"
    )
    try:
        proc = subprocess.run(
            [node_bin, "-e", script, *(str(p) for p in js_paths)],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw = json.loads(proc.stdout)
            if isinstance(raw, list):
                return [Path(x).name for x in raw]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return []


def check_ui_assets(doc, make_item) -> dict[str, Any]:
    """Read-only check that every /ui/* asset referenced by ui.html exists, is valid UTF-8, and valid JS."""
    ui_html = doc.app_root / "akatikito" / "ui.html"
    if not ui_html.is_file():
        return make_item("ui_assets", "Αρχεία διεπαφής UI (JS & CSS)", "ok", "Δεν βρέθηκε ui.html στον φάκελο εφαρμογής.")
    try:
        html_text = ui_html.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return make_item("ui_assets", "Αρχεία διεπαφής UI (JS & CSS)", "error", "Το akatikito/ui.html δεν μπορεί να διαβαστεί ως UTF-8.", fixable=False)
    refs = sorted(set(_UI_ASSET_RE.findall(html_text)))
    ui_dir = doc.app_root / "akatikito" / "ui"
    missing: list[str] = []
    js_files: list[Path] = []
    for rel in refs:
        asset = (ui_dir / rel).resolve()
        try:
            asset.relative_to(ui_dir.resolve())
        except ValueError:
            missing.append(rel)
            continue
        if not asset.is_file() or asset.stat().st_size == 0:
            missing.append(rel)
            continue
        try:
            asset.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            missing.append(rel)
            continue
        if asset.suffix.lower() == ".js":
            js_files.append(asset)
    bad_js = _check_js_syntax_batch(js_files)
    if missing or bad_js:
        parts = []
        if missing:
            parts.append(f"λείπουν/μη αναγνώσιμα: {', '.join(missing[:5])}")
        if bad_js:
            parts.append(f"σφάλμα σύνταξης JS: {', '.join(bad_js[:5])}")
        return make_item(
            "ui_assets",
            "Αρχεία διεπαφής UI (JS & CSS)",
            "error",
            f"Πρόβλημα στα αρχεία UI ({'; '.join(parts)}).",
            fixable=False,
            meta={"checked": len(refs), "missing": missing, "bad_js": bad_js},
        )
    return make_item(
        "ui_assets",
        "Αρχεία διεπαφής UI (JS & CSS)",
        "ok",
        f"Όλα τα αρχεία διεπαφής UI ({len(refs)} JS/CSS) υπάρχουν και είναι έγκυρα.",
        meta={"checked": len(refs), "missing": [], "bad_js": []},
    )


def _route_missing_key(raw: dict[str, Any], prefix: str) -> str:
    from .model import PROVIDERS
    from .settings import unmask_secret

    model = str(raw.get(f"{prefix}_model", "") or "").strip()
    if not model:
        return ""
    prov = str(raw.get(f"{prefix}_provider", "") or raw.get("provider", "") or "").strip()
    if not prov:
        return ""
    url = str(raw.get(f"{prefix}_base_url", "") or "").strip()
    if not url:
        info = PROVIDERS.get(prov)
        url = info if isinstance(info, str) else str((info or {}).get("base_url", "") or "")
    if "127.0.0.1" in url or "localhost" in url:
        return ""
    keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
    has_key = bool(unmask_secret(keys.get(prov, ""))) if keys else False
    return "" if has_key else f"{prefix}_model ({prov}: {model})"


def check_subagents_runtime(doc, make_item) -> dict[str, Any]:
    """Verify subtask token limits, bg_processes/context_pin readiness, and fast/smart model API keys."""
    from .bg_processes import MAX_RUNNING_PROCESSES
    from .config import Config
    from .context_pin import MAX_PINNED_FILES
    from .dev_tools import DEV_TOOLS_HANDLERS

    cfg = Config()
    per_tok = int(getattr(cfg, "subtask_token_limit", 0) or 0)
    tot_tok = int(getattr(cfg, "subtask_total_token_limit", 0) or 0)
    workers = int(getattr(cfg, "max_parallel_subtasks", 2) or 2)
    dev_cnt = len(DEV_TOOLS_HANDLERS)
    raw: dict[str, Any] = {}
    spath = doc.data_dir / "settings.json"
    if spath.is_file():
        try:
            loaded = json.loads(spath.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}
    missing_keys = [m for p in ("fast", "smart") if (m := _route_missing_key(raw, p))]
    fast_ask = str(raw.get("fast_model", "") or "").strip()
    if missing_keys:
        return make_item(
            "subagents_runtime",
            "Βοηθοί agent & δρομολόγηση μοντέλων",
            "warn",
            f"Έχει οριστεί δευτερεύον μοντέλο χωρίς αποθηκευμένο API key: {', '.join(missing_keys)}.",
            fixable=False,
            meta={"token_limit": per_tok, "total_token_limit": tot_tok, "workers": workers, "missing_keys": missing_keys},
        )
    fast_note = f" · fast ask: {fast_ask}" if fast_ask else ""
    return make_item(
        "subagents_runtime",
        "Βοηθοί agent & δρομολόγηση μοντέλων",
        "ok",
        (
            f"Βοηθοί έτοιμοι (έως {workers} παράλληλοι, {per_tok // 1000}k/{tot_tok // 1000}k tokens{fast_note}"
            f" · bg max {MAX_RUNNING_PROCESSES} · pins max {MAX_PINNED_FILES} · dev tools {dev_cnt})."
        ),
        meta={
            "token_limit": per_tok, "total_token_limit": tot_tok, "workers": workers,
            "fast_model": fast_ask, "bg_max": MAX_RUNNING_PROCESSES, "pin_max": MAX_PINNED_FILES, "dev_tools": dev_cnt,
        },
    )
