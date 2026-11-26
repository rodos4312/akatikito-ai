"""Hidden-error rules registry and per-file downward-only ratchet (QUALITY-PLAN-2 Q3).

Every rule declares an ID, title, and actionable Fix instruction.
Suppressions require an explicit inline reason:
    # quality: allow BLE001 — boundary handler reports error to UI instead of crashing
Silent suppressions without a reason are rejected.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "quality_baseline.json"
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "template_parsers", ".venv", "venv"}
IF_LADDER_MIN_BRANCHES = 6
_PATH_EXEMPT_FILES = {"akatikito/doctor_checks.py", "akatikito/settings.py", "akatikito/runtime_paths.py"}

_ALLOW_RE = re.compile(r"#\s*quality:\s*allow\s+([A-Z0-9-]+)\s*(?:—|--|-|:)\s*(.+)$")
_HARDCODED_PATH_RE = re.compile(r"(?:%APPDATA%|C:\\\\?Users\\\\?[A-Za-z0-9_.-]+|/tmp/[A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class RuleSpec:
    id: str
    title: str
    fix: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    file: str
    line: int
    detail: str


RULES: dict[str, RuleSpec] = {
    "BLE001": RuleSpec(
        "BLE001",
        "Broad except Exception",
        "Catch specific exceptions (e.g. OSError, ValueError) or justify inline with '# quality: allow BLE001 — <reason>'.",
    ),
    "S110": RuleSpec(
        "S110",
        "Silent except ...: pass",
        "Log/handle the error, narrow the exception, or justify with '# quality: allow S110 — <reason>'.",
    ),
    "AK-TIMEOUT": RuleSpec(
        "AK-TIMEOUT",
        "HTTP or subprocess.run call without explicit timeout",
        "Pass timeout=<seconds> so network or subprocess calls cannot hang indefinitely.",
    ),
    "AK-TEST-SLEEP": RuleSpec(
        "AK-TEST-SLEEP",
        "time.sleep() inside tests/",
        "Wait on threading.Event or condition polling instead of sleeping, or justify with '# quality: allow AK-TEST-SLEEP — <reason>'.",
    ),
    "AK-IF-LADDER": RuleSpec(
        "AK-IF-LADDER",
        f"Long if/elif equality ladder (>={IF_LADDER_MIN_BRANCHES} branches on one variable)",
        "Replace the if/elif ladder with a dict -> handler dispatch table.",
    ),
    "AK-HARDCODED-PATH": RuleSpec(
        "AK-HARDCODED-PATH",
        "Hardcoded user/temp path (%APPDATA%, C:\\Users\\..., /tmp/...)",
        "Use tempfile, Path.home(), or a dedicated runtime path helper instead of hardcoded paths.",
    ),
    "F401": RuleSpec(
        "F401",
        "Unused import",
        "Remove the unused import or export it explicitly in __all__.",
    ),
    "F841": RuleSpec(
        "F841",
        "Unused local variable",
        "Remove the unused assignment or prefix intentionally discarded values with '_'.",
    ),
}


def has_justified_allow(line_text: str, rule_id: str) -> bool:
    """Return True only if line carries '# quality: allow <rule_id> — <reason>' with a real reason."""
    m = _ALLOW_RE.search(line_text or "")
    if not m:
        return False
    return m.group(1) == rule_id and len(m.group(2).strip()) >= 3


def _is_broad_exception(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    if isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "BaseException"):
        return True
    if isinstance(handler.type, ast.Tuple):
        return any(isinstance(el, ast.Name) and el.id in ("Exception", "BaseException") for el in handler.type.elts)
    return False


def _is_silent_pass(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        if isinstance(fn.value, ast.Name):
            return f"{fn.value.id}.{fn.attr}"
        if isinstance(fn.value, ast.Attribute) and isinstance(fn.value.value, ast.Name):
            return f"{fn.value.value.id}.{fn.value.attr}.{fn.attr}"
    return ""


def _eq_target_name(test: ast.expr) -> str | None:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return None
    if isinstance(test.left, ast.Name):
        return test.left.id
    if isinstance(test.left, ast.Attribute) and isinstance(test.left.value, ast.Name):
        return f"{test.left.value.id}.{test.left.attr}"
    return None


def _if_ladder_length(node: ast.If) -> tuple[str | None, int]:
    target = _eq_target_name(node.test)
    if not target:
        return None, 0
    count = 1
    cur = node
    while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
        nxt = cur.orelse[0]
        if _eq_target_name(nxt.test) != target:
            break
        count += 1
        cur = nxt
    return target, count


def _collect_py_files(root: Path, *, include_tests: bool = True) -> list[Path]:
    files: list[Path] = []
    app_py = root / "app.py"
    if app_py.is_file():
        files.append(app_py)
    bases = [root / "akatikito", root / "scripts"]
    if include_tests:
        bases.append(root / "tests")
    for base in bases:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            files.append(p)
    return files


_TIMEOUT_CALLS = frozenset({
    "subprocess.run",
    "urllib.request.urlopen",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
    "httpx.request",
})


def _line_allowed(lines: list[str], lineno: int, rule_id: str) -> bool:
    idx = lineno - 1
    return 0 <= idx < len(lines) and has_justified_allow(lines[idx], rule_id)


def _check_except_node(node: ast.ExceptHandler, rel: str, lines: list[str], findings: list[Finding]) -> None:
    if _is_broad_exception(node) and not _line_allowed(lines, node.lineno, "BLE001"):
        findings.append(Finding("BLE001", rel, node.lineno, "broad except Exception"))
    if _is_silent_pass(node) and not _line_allowed(lines, node.lineno, "S110"):
        findings.append(Finding("S110", rel, node.lineno, "silent except: pass"))


def _check_call_node(node: ast.Call, rel: str, lines: list[str], is_test: bool, findings: list[Finding]) -> None:
    cname = _call_name(node)
    if not is_test and cname in _TIMEOUT_CALLS:
        if not any(kw.arg == "timeout" for kw in node.keywords) and not _line_allowed(lines, node.lineno, "AK-TIMEOUT"):
            findings.append(Finding("AK-TIMEOUT", rel, node.lineno, f"{cname}() without timeout="))
    if is_test and cname in ("time.sleep", "sleep") and not _line_allowed(lines, node.lineno, "AK-TEST-SLEEP"):
        findings.append(Finding("AK-TEST-SLEEP", rel, node.lineno, f"{cname}() in test"))


def _check_if_ladder(node: ast.If, rel: str, lines: list[str], seen_elifs: set[int], findings: list[Finding]) -> None:
    target, branches = _if_ladder_length(node)
    cur = node
    while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
        cur = cur.orelse[0]
        seen_elifs.add(id(cur))
    if branches >= IF_LADDER_MIN_BRANCHES and not _line_allowed(lines, node.lineno, "AK-IF-LADDER"):
        findings.append(Finding("AK-IF-LADDER", rel, node.lineno, f"{branches}-branch if/elif on '{target}'"))


def _check_hardcoded_paths(rel: str, lines: list[str], findings: list[Finding]) -> None:
    if not rel.startswith("akatikito/") or rel in _PATH_EXEMPT_FILES:
        return
    for idx, ln in enumerate(lines, 1):
        if not ln.lstrip().startswith("#") and _HARDCODED_PATH_RE.search(ln) and not _line_allowed(lines, idx, "AK-HARDCODED-PATH"):
            findings.append(Finding("AK-HARDCODED-PATH", rel, idx, "hardcoded user/temp path"))


def _scan_ast_file(rel: str, text: str) -> list[Finding]:
    lines = text.splitlines()
    tree = ast.parse(text, filename=rel)
    is_test = rel.startswith("tests/")
    findings: list[Finding] = []
    seen_elifs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and not is_test:
            _check_except_node(node, rel, lines, findings)
        elif isinstance(node, ast.Call):
            _check_call_node(node, rel, lines, is_test, findings)
        elif isinstance(node, ast.If) and not is_test and id(node) not in seen_elifs:
            _check_if_ladder(node, rel, lines, seen_elifs, findings)
    _check_hardcoded_paths(rel, lines, findings)
    return findings


def _scan_ruff_unused(root: Path) -> list[Finding]:
    targets = [t for t in ("akatikito", "scripts", "tests", "app.py") if (root / t).exists()]
    if not targets:
        return []
    cmd = [sys.executable, "-m", "ruff", "check", "--select", "F401,F841", "--output-format", "json", *targets]
    probe = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True, timeout=15)
    if probe.returncode != 0:
        ruff_bin = shutil.which("ruff")
        if not ruff_bin:
            return []
        cmd = [ruff_bin, "check", "--select", "F401,F841", "--output-format", "json", *targets]
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    findings: list[Finding] = []
    for item in items:
        code = str(item.get("code") or "")
        if code not in ("F401", "F841"):
            continue
        fpath = Path(str(item.get("filename") or ""))
        try:
            rel = fpath.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = fpath.as_posix()
        line = int((item.get("location") or {}).get("row", 1))
        findings.append(Finding(code, rel, line, str(item.get("message") or code)))
    return findings


def scan_all(root: Path = ROOT, *, include_ruff: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for p in _collect_py_files(root):
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        findings.extend(_scan_ast_file(rel, text))
    if include_ruff:
        findings.extend(_scan_ruff_unused(root))
    return findings


def aggregate_counts(findings: list[Finding]) -> dict[str, dict[str, int]]:
    by_rule: dict[str, dict[str, int]] = {rid: {} for rid in RULES}
    for f in findings:
        bucket = by_rule.setdefault(f.rule_id, {})
        bucket[f.file] = bucket.get(f.file, 0) + 1
    return {rid: dict(sorted(files.items())) for rid, files in sorted(by_rule.items())}


def _read_git_baseline(root: Path, base_ref: str = "main") -> dict | None:
    git_bin = shutil.which("git")
    if not git_bin or not (root / ".git").exists():
        return None
    mb = subprocess.run([git_bin, "merge-base", "HEAD", base_ref], cwd=str(root), capture_output=True, text=True, timeout=10)
    if mb.returncode != 0 or not mb.stdout.strip():
        return None
    show = subprocess.run(
        [git_bin, "show", f"{mb.stdout.strip()}:scripts/quality_baseline.json"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    if show.returncode != 0 or not show.stdout.strip():
        return None
    try:
        parsed = json.loads(show.stdout)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def check_rules(
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
    *,
    include_ruff: bool = True,
    use_git_base: bool = True,
) -> tuple[bool, list[str]]:
    working = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    ref_main = _read_git_baseline(root) if (use_git_base and baseline_path == root / "scripts" / "quality_baseline.json") else None

    errors: list[str] = []
    if ref_main:
        for rid, w_files in sorted(working.items()):
            if rid not in ref_main:
                continue
            m_files = ref_main.get(rid, {})
            for rel, cnt in sorted(w_files.items()):
                if int(cnt) > int(m_files.get(rel, 0)):
                    errors.append(f"[{rid}] baseline relaxed for {rel} ({m_files.get(rel, 0)} -> {cnt} vs main). Fix: lower violations in code.")

    findings = scan_all(root, include_ruff=include_ruff)
    counts = aggregate_counts(findings)
    by_rule_file: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        by_rule_file.setdefault((f.rule_id, f.file), []).append(f)

    for rid, file_counts in counts.items():
        spec = RULES[rid]
        w_sec = working.get(rid, {})
        m_sec = ref_main.get(rid, {}) if (ref_main and rid in ref_main) else None
        for rel, count in file_counts.items():
            allowed = int(w_sec.get(rel, 0))
            if m_sec is not None:
                allowed = min(allowed, int(m_sec.get(rel, 0)))
            if count > allowed:
                sample = by_rule_file[(rid, rel)][:3]
                locs = ", ".join(f"L{s.line} ({s.detail})" for s in sample)
                errors.append(f"[{rid}] {rel}: {count} > {allowed} ({spec.title}; {locs}). Fix: {spec.fix}")
    return (len(errors) == 0), errors


def write_baseline(root: Path = ROOT, baseline_path: Path = BASELINE_PATH, *, include_ruff: bool = True) -> dict[str, dict[str, int]]:
    prev = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    counts = aggregate_counts(scan_all(root, include_ruff=include_ruff))
    payload: dict[str, dict[str, int]] = {}
    for rid, file_counts in counts.items():
        prev_sec = prev.get(rid, {})
        new_sec: dict[str, int] = {}
        for rel, cnt in file_counts.items():
            if cnt > 0:
                new_sec[rel] = min(cnt, int(prev_sec[rel])) if rel in prev_sec else cnt
        payload[rid] = new_sec
    baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:
            pass
    parser = argparse.ArgumentParser(description="Hidden-error quality rules ratchet (Q3)")
    parser.add_argument("--update-baseline", "--tighten", dest="tighten", action="store_true",
                        help="Write or tighten scripts/quality_baseline.json")
    parser.add_argument("--list-rules", action="store_true", help="Print all registered rules and fix hints")
    parser.add_argument("--no-git-base", action="store_true", help="Skip comparing baseline against git main")
    args = parser.parse_args(argv)
    if args.list_rules:
        for spec in RULES.values():
            print(f"{spec.id:18s} {spec.title}\n                   Fix: {spec.fix}")
        return 0
    if args.tighten:
        data = write_baseline()
        summary = ", ".join(f"{rid}={sum(files.values())}" for rid, files in data.items())
        print(f"Tightened {BASELINE_PATH.name}: {summary}")
        return 0
    ok, errors = check_rules(use_git_base=not args.no_git_base)
    if not ok:
        print("QUALITY RULES FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    print(f"OK: hidden-error rules ratchet within baseline ({len(RULES)} rules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
