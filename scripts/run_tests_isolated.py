"""Per-file process-isolated parallel pytest runner with timings and flaky tracking (Q5)."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FileResult:
    file: str
    returncode: int
    seconds: float
    output: str
    flaky: bool = False


def discover_test_files(tests_dir: Path) -> list[Path]:
    if not tests_dir.is_dir():
        return []
    return sorted(p for p in tests_dir.glob("test_*.py") if p.is_file())


def _run_one(
    python: str,
    cwd: Path,
    file_path: Path,
    *,
    extra_args: list[str],
    timeout: int,
    retry_flaky: bool,
) -> FileResult:
    rel = file_path.relative_to(cwd).as_posix() if file_path.is_relative_to(cwd) else str(file_path)
    cmd = [python, "-m", "pytest", str(file_path), "-q", "-p", "no:cacheprovider", *extra_args]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    elapsed = time.monotonic() - t0
    if proc.returncode == 0 or proc.returncode == 5:  # 5 = all deselected by -m/-k filter
        return FileResult(rel, 0, elapsed, (proc.stdout or "").strip())

    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if retry_flaky:
        t1 = time.monotonic()
        retry = subprocess.run(
            cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if retry.returncode == 0:
            return FileResult(rel, 0, elapsed + (time.monotonic() - t1), out, flaky=True)
    return FileResult(rel, proc.returncode, elapsed, out)


def run_isolated(
    files: list[Path],
    *,
    cwd: Path = ROOT,
    python: str = sys.executable,
    workers: int = 0,
    tier: str = "full",
    ci: bool = False,
    retry_flaky: bool = False,
    timeout: int = 180,
) -> list[FileResult]:
    if not files:
        return []
    max_workers = workers if workers > 0 else max(1, min(4, os.cpu_count() or 2))
    extra: list[str] = []
    if tier == "fast":
        extra.extend(["-m", "not slow"])
    if ci:
        extra.extend(["-k", "not real_chromium"])

    results: list[FileResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [
            pool.submit(_run_one, python, cwd, f, extra_args=extra, timeout=timeout, retry_flaky=retry_flaky)
            for f in files
        ]
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: r.file)
    return results


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # quality: allow S110 — optional reconfigure on non-TTY pipe
            pass
    parser = argparse.ArgumentParser(description="Isolated per-file parallel pytest runner (Q5)")
    parser.add_argument("--tier", choices=("fast", "full"), default="full")
    parser.add_argument("--workers", type=int, default=0, help="Parallel worker count (default: min(4, cpu))")
    parser.add_argument("--ci", action="store_true", help="Filter out real_chromium tests")
    parser.add_argument("--retry-flaky", action="store_true", help="Retry failed file once and flag flaky passes")
    parser.add_argument("paths", nargs="*", help="Optional specific test files")
    args = parser.parse_args(argv)

    files = [Path(p).resolve() for p in args.paths] if args.paths else discover_test_files(ROOT / "tests")
    t0 = time.monotonic()
    results = run_isolated(
        files,
        cwd=ROOT,
        python=sys.executable,
        workers=args.workers,
        tier=args.tier,
        ci=args.ci,
        retry_flaky=args.retry_flaky,
    )
    wall = time.monotonic() - t0
    failed = [r for r in results if r.returncode != 0]
    flaky = [r for r in results if r.flaky]
    slowest = sorted(results, key=lambda r: r.seconds, reverse=True)[:5]

    for r in failed:
        print(f"FAIL [{r.seconds:.1f}s] {r.file}\n{r.output}\n", flush=True)
    for r in flaky:
        print(f"FLAKY [{r.seconds:.1f}s] {r.file} (failed once, passed on retry — needs fix)", flush=True)

    slow_str = ", ".join(f"{Path(r.file).name}:{r.seconds:.1f}s" for r in slowest)
    print(
        f"Isolated pytest ({args.tier}): {len(results) - len(failed)}/{len(results)} files passed "
        f"in {wall:.1f}s (flaky={len(flaky)}). Slowest: {slow_str}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
