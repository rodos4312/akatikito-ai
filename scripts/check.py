"""Unified quality check runner for Akatikito (local, git hooks, and CI — QUALITY-PLAN-2 Q1).

Usage:
  python scripts/check.py                     # full check (pre-push / CI)
  python scripts/check.py --fast              # everything except pytest
  python scripts/check.py --staged            # staged-aware pre-commit check
  python scripts/check.py --only ruff,health  # run selected steps only
  python scripts/check.py --list              # print available step names
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
RUFF_TARGETS = ("akatikito", "scripts", "tests", "app.py")


def _configure_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:
            pass


def resolve_python(root: Path = ROOT) -> str:
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return sys.executable


def staged_files(root: Path = ROOT) -> list[str]:
    git_bin = shutil.which("git")
    if not git_bin:
        return []
    proc = subprocess.run(
        [git_bin, "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


@dataclass(frozen=True)
class RunContext:
    root: Path
    python: str
    staged: list[str] | None  # None = full repo, list = staged paths only
    ci: bool = False


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    fn: Callable[[RunContext], tuple[int, str]]
    fast: bool = True


def _run_cmd(cmd: list[str], cwd: Path, *, timeout: int = 600, quiet_ok: bool = False) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=quiet_ok,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if quiet_ok and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            print(detail)
    return proc.returncode, ""


def _in_ruff_scope(rel: str) -> bool:
    if not rel.endswith(".py"):
        return False
    return rel == "app.py" or rel.startswith(("akatikito/", "scripts/", "tests/"))


def _step_ruff(ctx: RunContext) -> tuple[int, str]:
    targets = list(RUFF_TARGETS)
    if ctx.staged is not None:
        targets = [p for p in ctx.staged if _in_ruff_scope(p) and (ctx.root / p).is_file()]
        if not targets:
            return 0, "no staged Python files"
    cmd = [ctx.python, "-m", "ruff", "check", "--config", "ruff.toml", *targets]
    # Fallback to PATH ruff if venv doesn't expose -m ruff
    probe = subprocess.run([ctx.python, "-m", "ruff", "--version"], capture_output=True, timeout=15)
    if probe.returncode != 0:
        ruff_bin = shutil.which("ruff")
        if not ruff_bin:
            print("ruff not found: install with pip install -r requirements-dev.txt")
            return 1, "ruff missing"
        cmd = [ruff_bin, "check", "--config", "ruff.toml", *targets]
    return _run_cmd(cmd, ctx.root, timeout=120)


def _step_size(ctx: RunContext) -> tuple[int, str]:
    return _run_cmd([ctx.python, "scripts/size_check.py"], ctx.root, timeout=60)


def _step_health(ctx: RunContext) -> tuple[int, str]:
    return _run_cmd([ctx.python, "scripts/health_check.py"], ctx.root, timeout=60)


def _step_rules(ctx: RunContext) -> tuple[int, str]:
    return _run_cmd([ctx.python, "scripts/quality_rules.py"], ctx.root, timeout=90)


def _ui_js_files(root: Path) -> list[str]:
    files = sorted(p.relative_to(root).as_posix() for p in (root / "akatikito" / "ui").glob("*.js"))
    onb = root / "akatikito" / "onboarding.js"
    if onb.is_file():
        files.append("akatikito/onboarding.js")
    return files


def _step_js(ctx: RunContext) -> tuple[int, str]:
    node = shutil.which("node")
    if not node:
        print("node not found in PATH")
        return 1, "node missing"
    files = _ui_js_files(ctx.root)
    if ctx.staged is not None:
        staged_set = set(ctx.staged)
        files = [f for f in files if f in staged_set]
        if not files:
            return 0, "no staged UI JS files"
    for rel in files:
        rc, _ = _run_cmd([node, "--check", rel], ctx.root, timeout=30)
        if rc != 0:
            return rc, f"syntax error in {rel}"
    return 0, f"{len(files)} file(s)"


def _step_node(ctx: RunContext) -> tuple[int, str]:
    node = shutil.which("node")
    if not node:
        print("node not found in PATH")
        return 1, "node missing"
    if ctx.staged is not None:
        ui_touched = any(
            p.startswith(("akatikito/ui/", "tests/")) or p in ("akatikito/ui.html", "akatikito/onboarding.js")
            for p in ctx.staged
        )
        if not ui_touched:
            return 0, "no staged UI/test files"
    tests = sorted((ctx.root / "tests").glob("*.cjs"))
    for test_file in tests:
        rel = test_file.relative_to(ctx.root).as_posix()
        rc, _ = _run_cmd([node, rel], ctx.root, timeout=60, quiet_ok=True)
        if rc != 0:
            print(f"FAILED: {rel}")
            return rc, f"failed {rel}"
    return 0, f"{len(tests)} suite(s)"


def _step_pytest(ctx: RunContext) -> tuple[int, str]:
    cmd = [ctx.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if ctx.ci:
        cmd.extend(["-k", "not real_chromium"])
    else:
        cmd.append("-x")
    return _run_cmd(cmd, ctx.root, timeout=900)


STEPS: tuple[Step, ...] = (
    Step("ruff", "ruff (ruff.toml)", _step_ruff, fast=True),
    Step("size", "file size ratchet", _step_size, fast=True),
    Step("health", "complexity and long-line ratchet", _step_health, fast=True),
    Step("rules", "hidden-error rules ratchet", _step_rules, fast=True),
    Step("js", "UI JavaScript syntax", _step_js, fast=True),
    Step("node", "node UI tests", _step_node, fast=True),
    Step("pytest", "pytest", _step_pytest, fast=False),
)


def select_steps(only: str | None, *, fast: bool, staged: bool) -> list[Step]:
    by_name = {s.name: s for s in STEPS}
    if only:
        requested = [part.strip() for part in only.split(",") if part.strip()]
        unknown = [name for name in requested if name not in by_name]
        if unknown:
            valid = ", ".join(by_name)
            raise ValueError(f"Unknown step(s): {', '.join(unknown)}. Valid steps: {valid}")
        return [by_name[name] for name in requested]
    if fast or staged:
        return [s for s in STEPS if s.fast]
    return list(STEPS)


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    _configure_utf8()
    parser = argparse.ArgumentParser(description="Akatikito unified quality check runner")
    parser.add_argument("--fast", action="store_true", help="Run all fast checks (skip pytest)")
    parser.add_argument("--staged", action="store_true", help="Scope fast checks to git staged files")
    parser.add_argument("--only", default="", help="Comma-separated step names to run")
    parser.add_argument("--ci", action="store_true", help="CI mode (filters real_chromium in pytest)")
    parser.add_argument("--list", action="store_true", help="List available steps and exit")
    args = parser.parse_args(argv)

    if args.list:
        for s in STEPS:
            tier = "fast" if s.fast else "full"
            print(f"{s.name:10s} [{tier}]  {s.title}")
        return 0

    try:
        chosen = select_steps(args.only or None, fast=args.fast, staged=args.staged)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    staged = staged_files(root) if args.staged else None
    ctx = RunContext(root=root, python=resolve_python(root), staged=staged, ci=args.ci)
    total = len(chosen)
    started_all = time.monotonic()

    for idx, step in enumerate(chosen, 1):
        print(f"[{idx}/{total}] {step.title}", flush=True)
        t0 = time.monotonic()
        rc, note = step.fn(ctx)
        elapsed = time.monotonic() - t0
        if rc != 0:
            print(f"\nCHECKS FAILED at [{step.name}] ({elapsed:.1f}s): fix the errors above first.", flush=True)
            return rc
        if note:
            print(f"  -> {note} ({elapsed:.1f}s)", flush=True)

    if (args.fast or args.staged) and not args.only:
        print("[pytest] skipped in fast/staged mode; runs on pre-push and CI.")

    print(f"\nOK: all checks passed ({time.monotonic() - started_all:.1f}s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
