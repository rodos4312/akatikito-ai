"""Downward-only ratchet for Python function cyclomatic complexity and long-line density (PRO-PLAN A2).

Rules:
- New functions must have McCabe complexity <= COMPLEXITY_LIMIT (20).
- Existing functions in `scripts/health_baseline.json` may NOT grow above their recorded baseline
  (they may only shrink).
- Files may NOT add new lines > LONG_LINE_LIMIT (200 chars) above their recorded baseline
  (pure data catalogs like `akatikito/i18n.py` are excluded).
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "health_baseline.json"
COMPLEXITY_LIMIT = 20
LONG_LINE_LIMIT = 200
EXCLUDED_REL = {"akatikito/i18n.py"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "template_parsers", ".venv", "venv"}

_BRANCHES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.BoolOp,
    ast.IfExp,
    ast.comprehension,
    ast.ExceptHandler,
)
if hasattr(ast, "Match"):
    _BRANCHES = (*_BRANCHES, ast.Match)


def function_complexity(fn: ast.AST) -> int:
    """McCabe complexity: 1 + branch points inside the function (excluding nested defs)."""
    score = 1
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, _BRANCHES):
            score += (len(node.values) - 1) if isinstance(node, ast.BoolOp) else 1
        stack.extend(ast.iter_child_nodes(node))
    return score


def _collect_py_files(root: Path) -> list[Path]:
    files: list[Path] = []
    app_py = root / "app.py"
    if app_py.is_file():
        files.append(app_py)
    for base in (root / "akatikito", root / "scripts"):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            if p.relative_to(root).as_posix() in EXCLUDED_REL:
                continue
            files.append(p)
    return files


def _walk_functions(tree: ast.AST, prefix: str = "") -> list[tuple[str, ast.AST]]:
    found: list[tuple[str, ast.AST]] = []
    seen_counts: dict[str, int] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            cls_prefix = f"{prefix}{node.name}."
            found.extend(_walk_functions(node, cls_prefix))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            base_name = f"{prefix}{node.name}"
            idx = seen_counts.get(base_name, 0)
            seen_counts[base_name] = idx + 1
            key = base_name if idx == 0 else f"{base_name}#{idx}"
            found.append((key, node))
            found.extend(_walk_functions(node, f"{key}."))
    return found


def measure(root: Path = ROOT) -> tuple[dict[str, int], dict[str, int]]:
    """Return (complexities_by_fn_key, long_line_counts_by_file)."""
    complexities: dict[str, int] = {}
    long_lines: dict[str, int] = {}
    for p in _collect_py_files(root):
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        long_count = sum(1 for ln in text.splitlines() if len(ln) > LONG_LINE_LIMIT)
        if long_count:
            long_lines[rel] = long_count
        tree = ast.parse(text, filename=str(p))
        for qname, fn in _walk_functions(tree):
            complexities[f"{rel}::{qname}"] = function_complexity(fn)
    return complexities, long_lines


def check_health(
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
    *,
    complexity_limit: int = COMPLEXITY_LIMIT,
) -> tuple[bool, list[str]]:
    raw = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    fn_base: dict[str, int] = dict(raw.get("complexity", {}))
    line_base: dict[str, int] = dict(raw.get("long_lines", {}))

    complexities, long_lines = measure(root)
    errors: list[str] = []
    for key, val in sorted(complexities.items()):
        allowed = max(complexity_limit, int(fn_base.get(key, complexity_limit)))
        if val > allowed:
            kind = f"baseline {fn_base[key]}" if key in fn_base else f"new function limit {complexity_limit}"
            errors.append(f"{key}: complexity {val} > {allowed} ({kind})")
    for rel, count in sorted(long_lines.items()):
        allowed_lines = int(line_base.get(rel, 0))
        if count > allowed_lines:
            errors.append(f"{rel}: {count} lines >{LONG_LINE_LIMIT} chars (allowed {allowed_lines})")
    return (len(errors) == 0), errors


def write_baseline(
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
    *,
    complexity_limit: int = COMPLEXITY_LIMIT,
) -> dict[str, dict[str, int]]:
    prev = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    prev_fn = dict(prev.get("complexity", {}))
    prev_lines = dict(prev.get("long_lines", {}))

    complexities, long_lines = measure(root)
    new_fn: dict[str, int] = {}
    for key, val in sorted(complexities.items()):
        if val > complexity_limit:
            new_fn[key] = min(val, int(prev_fn[key])) if key in prev_fn else val
    new_lines: dict[str, int] = {}
    for rel, count in sorted(long_lines.items()):
        if count > 0:
            new_lines[rel] = min(count, int(prev_lines[rel])) if rel in prev_lines else count
    payload = {"complexity": new_fn, "long_lines": new_lines}
    baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Akatikito complexity & long-line ratchet")
    parser.add_argument("--update-baseline", action="store_true", help="Write/ratchet scripts/health_baseline.json")
    args = parser.parse_args(argv)
    if args.update_baseline:
        data = write_baseline()
        print(f"Updated {BASELINE_PATH.name} ({len(data['complexity'])} functions >{COMPLEXITY_LIMIT} CC, {len(data['long_lines'])} files with >{LONG_LINE_LIMIT}-char lines).")
        return 0
    ok, errors = check_health()
    if not ok:
        print("HEALTH CHECK FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    print(f"OK: complexity (<={COMPLEXITY_LIMIT} / baseline) and long-line density within limits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
