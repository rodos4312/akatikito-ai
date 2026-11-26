"""Downward-only health ratchet for Python & UI JS (PRO-PLAN A2 + QUALITY-PLAN-2 Q2).

Enforced per-function and per-file limits (new code must stay within limits; existing items
in `scripts/health_baseline.json` may only shrink, never grow):
- Python cyclomatic complexity <= 20
- Python function length <= 300 lines
- Python control-flow nesting depth <= 6
- UI JavaScript function cyclomatic complexity <= 20
- Python lines > 200 chars <= baseline

Anti-relaxation: when running inside a git repository, the working baseline is also compared
against `main` (via merge-base) so a branch cannot loosen its own limits.
"""
from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from scripts.js_metrics import measure_js_files
except ImportError:
    from js_metrics import measure_js_files  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "health_baseline.json"
COMPLEXITY_LIMIT = 20
LENGTH_LIMIT = 300
DEPTH_LIMIT = 6
JS_COMPLEXITY_LIMIT = 20
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
_NESTING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
)
if hasattr(ast, "Match"):
    _BRANCHES = (*_BRANCHES, ast.Match)
    _NESTING_NODES = (*_NESTING_NODES, ast.Match)

_FIX_HINTS = {
    "complexity": f"Fix: extract helper functions or a dict->handler table so complexity <= {COMPLEXITY_LIMIT}.",
    "length": f"Fix: split the function into smaller phase helpers so length <= {LENGTH_LIMIT} lines.",
    "depth": f"Fix: flatten nested blocks with guard clauses / early returns so nesting depth <= {DEPTH_LIMIT}.",
    "js_complexity": f"Fix: split JS branches into small helper functions so complexity <= {JS_COMPLEXITY_LIMIT}.",
    "long_lines": f"Fix: wrap long lines so lines > {LONG_LINE_LIMIT} chars do not increase.",
}


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


def function_length(fn: ast.AST) -> int:
    """Line span of a function definition."""
    start = int(getattr(fn, "lineno", 0) or 0)
    end = int(getattr(fn, "end_lineno", start) or start)
    return max(1, end - start + 1)


def _nesting_in_body(stmts: list[ast.stmt], current: int) -> int:
    best = current
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.If):
            best = max(best, _nesting_if(stmt, current + 1))
        elif isinstance(stmt, _NESTING_NODES):
            best = max(best, _nesting_compound(stmt, current + 1))
    return best


def _nesting_if(node: ast.If, depth: int) -> int:
    best = max(depth, _nesting_in_body(node.body, depth))
    orelse = node.orelse
    while len(orelse) == 1 and isinstance(orelse[0], ast.If):
        elif_node = orelse[0]
        best = max(best, _nesting_in_body(elif_node.body, depth))
        orelse = elif_node.orelse
    if orelse:
        best = max(best, _nesting_in_body(orelse, depth))
    return best


def _nesting_compound(node: ast.AST, depth: int) -> int:
    best = depth
    for attr in ("body", "orelse", "finalbody"):
        block = getattr(node, attr, None)
        if isinstance(block, list):
            best = max(best, _nesting_in_body(block, depth))
    for handler in getattr(node, "handlers", None) or ():
        best = max(best, _nesting_in_body(handler.body, depth))
    for case in getattr(node, "cases", None) or ():
        best = max(best, _nesting_in_body(case.body, depth))
    return best


def function_depth(fn: ast.AST) -> int:
    """Maximum control-flow nesting depth inside a function (elif siblings do not add depth)."""
    body = getattr(fn, "body", None)
    return _nesting_in_body(body, 0) if isinstance(body, list) else 0


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
            found.extend(_walk_functions(node, f"{prefix}{node.name}."))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            base_name = f"{prefix}{node.name}"
            idx = seen_counts.get(base_name, 0)
            seen_counts[base_name] = idx + 1
            key = base_name if idx == 0 else f"{base_name}#{idx}"
            found.append((key, node))
            found.extend(_walk_functions(node, f"{key}."))
    return found


def measure_all(root: Path = ROOT) -> dict[str, dict[str, int]]:
    """Measure all 5 ratchet dimensions across Python and UI JS files."""
    complexities: dict[str, int] = {}
    lengths: dict[str, int] = {}
    depths: dict[str, int] = {}
    long_lines: dict[str, int] = {}
    for p in _collect_py_files(root):
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        long_count = sum(1 for ln in text.splitlines() if len(ln) > LONG_LINE_LIMIT)
        if long_count:
            long_lines[rel] = long_count
        tree = ast.parse(text, filename=str(p))
        for qname, fn in _walk_functions(tree):
            fqn = f"{rel}::{qname}"
            complexities[fqn] = function_complexity(fn)
            lengths[fqn] = function_length(fn)
            depths[fqn] = function_depth(fn)
    return {
        "complexity": complexities,
        "length": lengths,
        "depth": depths,
        "js_complexity": measure_js_files(root),
        "long_lines": long_lines,
    }


def measure(root: Path = ROOT) -> tuple[dict[str, int], dict[str, int]]:
    """Backward-compatible wrapper returning (complexities_by_fn_key, long_line_counts_by_file)."""
    all_m = measure_all(root)
    return all_m["complexity"], all_m["long_lines"]


def read_git_baseline(root: Path, baseline_rel: str = "scripts/health_baseline.json", base_ref: str = "main") -> dict | None:
    """Read baseline JSON from merge-base(HEAD, main) so branches cannot relax their own limits."""
    git_bin = shutil.which("git")
    if not git_bin or not (root / ".git").exists():
        return None
    mb = subprocess.run(
        [git_bin, "merge-base", "HEAD", base_ref],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if mb.returncode != 0 or not mb.stdout.strip():
        return None
    commit = mb.stdout.strip()
    show = subprocess.run(
        [git_bin, "show", f"{commit}:{baseline_rel}"],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if show.returncode != 0 or not show.stdout.strip():
        return None
    try:
        parsed = json.loads(show.stdout)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def detect_baseline_relaxation(working: dict, ref_main: dict) -> list[str]:
    """Fail if working-tree baseline raised or added grandfathered limits compared to main."""
    errors: list[str] = []
    for section in ("complexity", "length", "depth", "js_complexity", "long_lines"):
        if section not in ref_main:
            continue  # Newly introduced metric dimension not yet recorded on main
        w_map = working.get(section, {})
        m_map = ref_main.get(section, {})
        for key, w_val in sorted(w_map.items()):
            if key not in m_map:
                errors.append(
                    f"baseline relaxation [{section}] {key}: added new grandfathered entry ({w_val}) not in main. "
                    "Fix: refactor the code to stay within the limit instead of editing health_baseline.json."
                )
            elif int(w_val) > int(m_map[key]):
                errors.append(
                    f"baseline relaxation [{section}] {key}: raised {m_map[key]} -> {w_val} vs main. "
                    "Fix: revert the baseline increase and lower the metric in code."
                )
    return errors


def _effective_section(working: dict, ref_main: dict | None, section: str) -> dict[str, int]:
    w_map = {k: int(v) for k, v in (working.get(section) or {}).items()}
    if not ref_main or section not in ref_main:
        return w_map
    m_map = {k: int(v) for k, v in (ref_main.get(section) or {}).items()}
    return {k: min(w_map[k], m_map[k]) for k in (set(w_map) & set(m_map))}


def _check_section(
    section: str,
    measured: dict[str, int],
    baseline: dict[str, int],
    limit: int,
) -> list[str]:
    errors: list[str] = []
    hint = _FIX_HINTS[section]
    for key, val in sorted(measured.items()):
        allowed = max(limit, baseline.get(key, limit))
        if val > allowed:
            kind = f"baseline {baseline[key]}" if key in baseline else f"limit {limit}"
            errors.append(f"{key}: {section} {val} > {allowed} ({kind}). {hint}")
    return errors


def check_health(
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
    *,
    complexity_limit: int = COMPLEXITY_LIMIT,
    length_limit: int = LENGTH_LIMIT,
    depth_limit: int = DEPTH_LIMIT,
    js_complexity_limit: int = JS_COMPLEXITY_LIMIT,
    use_git_base: bool = True,
) -> tuple[bool, list[str]]:
    working = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    ref_main = read_git_baseline(root) if (use_git_base and baseline_path == root / "scripts" / "health_baseline.json") else None

    errors: list[str] = []
    if ref_main:
        errors.extend(detect_baseline_relaxation(working, ref_main))

    metrics = measure_all(root)
    limits = (
        ("complexity", complexity_limit),
        ("length", length_limit),
        ("depth", depth_limit),
        ("js_complexity", js_complexity_limit),
        ("long_lines", 0),
    )
    for section, limit in limits:
        eff = _effective_section(working, ref_main, section)
        errors.extend(_check_section(section, metrics[section], eff, limit))
    return (len(errors) == 0), errors


def write_baseline(
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
    *,
    complexity_limit: int = COMPLEXITY_LIMIT,
    length_limit: int = LENGTH_LIMIT,
    depth_limit: int = DEPTH_LIMIT,
    js_complexity_limit: int = JS_COMPLEXITY_LIMIT,
) -> dict[str, dict[str, int]]:
    """Write or tighten scripts/health_baseline.json (existing keys only shrink, never grow)."""
    prev = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    metrics = measure_all(root)
    limits = {
        "complexity": complexity_limit,
        "length": length_limit,
        "depth": depth_limit,
        "js_complexity": js_complexity_limit,
        "long_lines": 0,
    }
    payload: dict[str, dict[str, int]] = {}
    for section, limit in limits.items():
        prev_sec = {k: int(v) for k, v in (prev.get(section) or {}).items()}
        new_sec: dict[str, int] = {}
        for key, val in sorted(metrics[section].items()):
            if val > limit:
                new_sec[key] = min(val, prev_sec[key]) if key in prev_sec else val
        payload[section] = new_sec
    baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:
            pass
    parser = argparse.ArgumentParser(description="Check Akatikito full health ratchet (Q2)")
    parser.add_argument("--update-baseline", "--tighten", dest="tighten", action="store_true",
                        help="Write or tighten scripts/health_baseline.json (downward-only)")
    parser.add_argument("--no-git-base", action="store_true", help="Skip comparing baseline against git main")
    args = parser.parse_args(argv)
    if args.tighten:
        data = write_baseline()
        print(
            f"Tightened {BASELINE_PATH.name}: "
            f"CC>{COMPLEXITY_LIMIT}={len(data['complexity'])}, "
            f"len>{LENGTH_LIMIT}={len(data['length'])}, "
            f"depth>{DEPTH_LIMIT}={len(data['depth'])}, "
            f"js_CC>{JS_COMPLEXITY_LIMIT}={len(data['js_complexity'])}, "
            f"long_line_files={len(data['long_lines'])}."
        )
        return 0
    ok, errors = check_health(use_git_base=not args.no_git_base)
    if not ok:
        print("HEALTH CHECK FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    print(
        f"OK: health ratchet within limits "
        f"(CC<={COMPLEXITY_LIMIT}, len<={LENGTH_LIMIT}, depth<={DEPTH_LIMIT}, JS CC<={JS_COMPLEXITY_LIMIT}, lines<={LONG_LINE_LIMIT})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
