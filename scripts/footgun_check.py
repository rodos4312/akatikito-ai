"""Cheap footgun checks for Windows/cross-platform reliability (QUALITY-PLAN-2 Q8).

Checks:
1. FG-CASE: Case-only filename collisions (breaks Windows git checkouts).
2. FG-BLOB: Large accidental files > 2 MiB outside allowlisted media paths.
3. FG-PATH: Hardcoded /tmp/... or C:\\Users\\... paths in runtime/script Python code.
4. FG-DETACHED: Discarded subprocess.Popen(...) calls (untracked child processes).
5. FG-UTF8: CLI entrypoints printing non-ASCII must guard stdout UTF-8 encoding.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "node_modules", "template_parsers", "data", "dist", "build", ".agent", ".agent_backups",
}
BLOB_ALLOW_PREFIXES = ("assets/", "docs/screenshots/")
_HARDCODED_PATH_RE = re.compile(r"(?:C:\\\\?Users\\\\?[A-Za-z0-9_.-]+|/tmp/[A-Za-z0-9_.-]+)")
_ALLOW_RE = re.compile(r"#\s*quality:\s*allow\s+(FG-[A-Z0-9-]+)\s*(?:—|--|-|:)\s*(.{3,})$")
_CLI_UTF8_TARGETS = (
    "akatikito/doctor.py",
    "scripts/check.py",
    "scripts/health_check.py",
    "scripts/quality_rules.py",
    "scripts/footgun_check.py",
)


def _is_allowed(line: str, code: str) -> bool:
    m = _ALLOW_RE.search(line or "")
    return bool(m and m.group(1) == code)


def _iter_repo_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        found.append(p)
    return found


def check_case_collisions(paths: list[str]) -> list[str]:
    """Detect two relative paths that differ only in letter case."""
    seen: dict[str, str] = {}
    errors: list[str] = []
    for rel in sorted(paths):
        folded = rel.casefold()
        if folded in seen and seen[folded] != rel:
            errors.append(
                f"[FG-CASE] Case-only path collision: '{seen[folded]}' vs '{rel}'. "
                "Fix: rename one file so paths are unique case-insensitively on Windows."
            )
        else:
            seen[folded] = rel
    return errors


def check_large_blobs(root: Path, files: list[Path], *, max_bytes: int = MAX_BLOB_BYTES) -> list[str]:
    errors: list[str] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel.startswith(BLOB_ALLOW_PREFIXES):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            mb = size / (1024 * 1024)
            errors.append(
                f"[FG-BLOB] {rel} is {mb:.2f} MiB (limit {max_bytes / (1024 * 1024):.1f} MiB). "
                "Fix: remove large artifacts from git or add them to .gitignore."
            )
    return errors


def check_hardcoded_paths(root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if not rel.endswith(".py") or not (rel == "app.py" or rel.startswith(("akatikito/", "scripts/"))):
            continue
        if rel in ("scripts/footgun_check.py", "scripts/quality_rules.py"):
            continue
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        for idx, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _HARDCODED_PATH_RE.search(line) and not _is_allowed(line, "FG-PATH"):
                errors.append(
                    f"[FG-PATH] {rel}:{idx} contains hardcoded /tmp or C:\\Users path. "
                    "Fix: use tempfile.gettempdir() or Path.home() instead."
                )
    return errors


def _is_popen_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == "Popen":
        return True
    return isinstance(fn, ast.Attribute) and fn.attr == "Popen"


def check_detached_spawns(root: Path, files: list[Path]) -> list[str]:
    """Flag bare expression-statement subprocess.Popen(...) calls whose handle is discarded."""
    errors: list[str] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if not rel.endswith(".py") or not (rel == "app.py" or rel.startswith(("akatikito/", "scripts/"))):
            continue
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and _is_popen_call(node.value):
                ln = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
                if not _is_allowed(ln, "FG-DETACHED"):
                    errors.append(
                        f"[FG-DETACHED] {rel}:{node.lineno} discards subprocess.Popen() handle. "
                        "Fix: assign the Popen handle and wait/close it, or justify with '# quality: allow FG-DETACHED — <reason>'."
                    )
    return errors


def check_cli_utf8_guards(root: Path, targets: tuple[str, ...] = _CLI_UTF8_TARGETS) -> list[str]:
    """Ensure CLI entrypoints configure UTF-8 stdout so cp1252/cp1253 Windows consoles never crash."""
    errors: list[str] = []
    for rel in targets:
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        if "reconfigure" not in text or "utf-8" not in text.lower():
            errors.append(
                f"[FG-UTF8] {rel} lacks sys.stdout.reconfigure(encoding='utf-8', errors='replace'). "
                "Fix: configure UTF-8 stdout at CLI startup so non-UTF8 Windows consoles never raise UnicodeEncodeError."
            )
    return errors


def run_all(root: Path = ROOT, *, max_blob_bytes: int = MAX_BLOB_BYTES) -> tuple[bool, list[str]]:
    files = _iter_repo_files(root)
    rel_paths = [p.relative_to(root).as_posix() for p in files]
    errors: list[str] = []
    errors.extend(check_case_collisions(rel_paths))
    errors.extend(check_large_blobs(root, files, max_bytes=max_blob_bytes))
    errors.extend(check_hardcoded_paths(root, files))
    errors.extend(check_detached_spawns(root, files))
    errors.extend(check_cli_utf8_guards(root))
    return (len(errors) == 0), errors


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # quality: allow S110 — optional reconfigure on non-TTY pipe
            pass
    parser = argparse.ArgumentParser(description="Run 5 cheap footgun checks (QUALITY-PLAN-2 Q8)")
    parser.parse_args(argv)
    ok, errors = run_all()
    if not ok:
        print("FOOTGUN CHECK FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    print("OK: all 5 footgun checks passed (case, blobs, paths, spawns, UTF-8 CLI).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
