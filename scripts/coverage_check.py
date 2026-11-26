"""Area-based code coverage ratchet (QUALITY-PLAN-2 Q5).

Measures line coverage per functional area (agent, memory, tools, app_api, overall)
and enforces an upward-only ratchet against scripts/coverage_baseline.json (and main).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "coverage_baseline.json"
TOLERANCE = 0.5  # Allow <= 0.5% jitter across OS/Python patch runs

AREA_PATTERNS: dict[str, tuple[str, ...]] = {
    "agent": (
        "akatikito/agent*.py",
        "akatikito/context.py",
        "akatikito/repair.py",
        "akatikito/activity.py",
        "akatikito/modes.py",
    ),
    "memory": (
        "akatikito/memory*.py",
        "akatikito/profile.py",
        "akatikito/skills*.py",
        "akatikito/workflows.py",
    ),
    "tools": (
        "akatikito/tools.py",
        "akatikito/editing.py",
        "akatikito/reading.py",
        "akatikito/searching.py",
        "akatikito/workspace.py",
        "akatikito/command_policy.py",
        "akatikito/execution.py",
    ),
    "app_api": (
        "akatikito/app_api*.py",
        "akatikito/settings.py",
        "akatikito/sessions.py",
        "akatikito/doctor*.py",
    ),
    "overall": ("akatikito/*.py",),
}

FAST_COVERAGE_TESTS: tuple[str, ...] = (
    "tests/test_agent_dispatch_contract.py",
    "tests/test_agent_loop_contract.py",
    "tests/test_api_routes_contract.py",
    "tests/test_context_prepare_contract.py",
    "tests/test_preferences_contract.py",
    "tests/test_reading_scan_contract.py",
    "tests/test_skills_propose_contract.py",
    "tests/test_workflows_observed_contract.py",
    "tests/test_memory_store.py",
    "tests/test_memory_recall.py",
    "tests/test_engine_improvements.py",
    "tests/test_doctor.py",
    "tests/test_telegram_l1.py",
    "tests/test_background_service_l2.py",
    "tests/test_briefings_l3.py",
    "tests/test_email_assistant_l4.py",
    "tests/test_notes_vault_l5.py",
    "tests/test_content_workflow_l6.py",
    "tests/test_cache_discipline_b2.py",
    "tests/test_diagnostics_bundle_b4.py",
    "tests/test_skill_curator_b9.py",
    "tests/test_family_profiles_b10.py",
    "tests/test_cli_d1_d2.py",
    "tests/test_git_workflow_d4.py",
    "tests/test_plugin_sdk_d5.py",
    "tests/test_acp_server_d3.py",
    "tests/test_channels_wave4_l7.py",
)


def _norm_rel(path_str: str, root: Path) -> str:
    p = Path(path_str)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return p.as_posix()
    return path_str.replace("\\", "/").lstrip("./")


def compute_area_coverage(cov_data: dict, root: Path = ROOT) -> dict[str, dict[str, float | int]]:
    files = cov_data.get("files") or {}
    norm_files: dict[str, tuple[int, int]] = {}
    for raw_path, info in files.items():
        rel = _norm_rel(raw_path, root)
        summary = info.get("summary") or {}
        covered = int(summary.get("covered_lines", 0))
        total = int(summary.get("num_statements", 0))
        norm_files[rel] = (covered, total)

    areas: dict[str, dict[str, float | int]] = {}
    for area, patterns in AREA_PATTERNS.items():
        cov_sum = 0
        tot_sum = 0
        for rel, (cov, tot) in norm_files.items():
            if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                cov_sum += cov
                tot_sum += tot
        pct = round((cov_sum / tot_sum) * 100.0, 2) if tot_sum else 0.0
        areas[area] = {"covered": cov_sum, "statements": tot_sum, "percent": pct}
    return areas


def _read_git_baseline(root: Path, base_ref: str = "main") -> dict | None:
    git_bin = shutil.which("git")
    if not git_bin or not (root / ".git").exists():
        return None
    mb = subprocess.run([git_bin, "merge-base", "HEAD", base_ref], cwd=str(root), capture_output=True, text=True, timeout=10)
    if mb.returncode != 0 or not mb.stdout.strip():
        return None
    show = subprocess.run(
        [git_bin, "show", f"{mb.stdout.strip()}:scripts/coverage_baseline.json"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    if show.returncode != 0 or not show.stdout.strip():
        return None
    try:
        parsed = json.loads(show.stdout)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def check_floors(
    areas: dict[str, dict[str, float | int]],
    baseline: dict[str, float],
    *,
    main_baseline: dict[str, float] | None = None,
    tolerance: float = TOLERANCE,
) -> list[str]:
    errors: list[str] = []
    if main_baseline:
        for area, main_floor in sorted(main_baseline.items()):
            cur_floor = baseline.get(area)
            if cur_floor is None or float(cur_floor) + 1e-6 < float(main_floor):
                errors.append(
                    f"baseline floor for '{area}' lowered ({cur_floor} < {main_floor} on main). "
                    "Fix: Restore floor >= main or add tests to increase coverage."
                )
    effective = dict(baseline)
    if main_baseline:
        for area, main_floor in main_baseline.items():
            effective[area] = max(float(effective.get(area, 0.0)), float(main_floor))
    for area, floor in sorted(effective.items()):
        actual = float((areas.get(area) or {}).get("percent", 0.0))
        if actual + tolerance < float(floor):
            errors.append(
                f"coverage '{area}': {actual:.2f}% < floor {float(floor):.2f}% (tolerance {tolerance}%). "
                "Fix: Add behavioral tests covering the modified code in this area."
            )
    return errors


def run_coverage(root: Path, test_targets: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="akatikito-cov-") as tmp:
        data_file = Path(tmp) / ".coverage"
        json_file = Path(tmp) / "coverage.json"
        run_cmd = [
            sys.executable, "-m", "coverage", "run",
            f"--data-file={data_file}",
            "--source=akatikito",
            "-m", "pytest", "-q",
            "-k", "not test_ten_thousand_memories_answer_fast",
            *test_targets,
        ]
        res = subprocess.run(run_cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if res.returncode != 0:
            raise RuntimeError(f"pytest failed during coverage run (exit {res.returncode}):\n{res.stdout[-1500:]}\n{res.stderr[-500:]}")
        json_cmd = [
            sys.executable, "-m", "coverage", "json",
            f"--data-file={data_file}",
            "-o", str(json_file),
        ]
        jres = subprocess.run(json_cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if jres.returncode != 0 or not json_file.is_file():
            raise RuntimeError(f"coverage json failed: {jres.stderr}")
        return json.loads(json_file.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Area coverage ratchet (QUALITY-PLAN-2 Q5).")
    parser.add_argument("--from-json", help="Read existing coverage.py JSON instead of running pytest")
    parser.add_argument("--full", action="store_true", help="Run full tests/ suite instead of fast core subset")
    parser.add_argument("--tighten", action="store_true", help="Raise floors in coverage_baseline.json to measured values")
    parser.add_argument("--base-ref", default="main")
    args = parser.parse_args(argv)

    if args.from_json:
        cov_data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    else:
        targets = ["tests"] if args.full else [t for t in FAST_COVERAGE_TESTS if (ROOT / t).is_file()]
        cov_data = run_coverage(ROOT, targets)

    areas = compute_area_coverage(cov_data, ROOT)
    baseline_doc = json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.is_file() else {"areas": {}}
    baseline_floors = {k: float(v) for k, v in (baseline_doc.get("areas") or {}).items()}
    main_doc = _read_git_baseline(ROOT, args.base_ref)
    main_floors = {k: float(v) for k, v in (main_doc.get("areas") or {}).items()} if main_doc else None

    if args.tighten:
        new_floors = {}
        for area, info in sorted(areas.items()):
            prev = max(baseline_floors.get(area, 0.0), (main_floors or {}).get(area, 0.0))
            measured_floor = round(max(0.0, float(info["percent"]) - 1.0), 1)
            new_floors[area] = max(prev, measured_floor)
        BASELINE_PATH.write_text(json.dumps({"areas": new_floors}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("Tightened coverage_baseline.json:", ", ".join(f"{k}>={v:.1f}%" for k, v in new_floors.items()))
        return 0

    errors = check_floors(areas, baseline_floors, main_baseline=main_floors)
    summary = ", ".join(f"{k}={info['percent']:.1f}%" for k, info in sorted(areas.items()))
    if errors:
        print(f"COVERAGE RATCHET FAILED ({summary}):")
        for err in errors:
            print("  -", err)
        return 1
    print(f"OK: coverage ratchet passed ({summary}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
