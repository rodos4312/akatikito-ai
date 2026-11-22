"""CLI runner for Akatikito offline dry-run evals and live LLM benchmarks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from akatikito.eval_harness import run_dry_evals, run_live_evals


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Akatikito evaluation suite.")
    parser.add_argument("--live", action="store_true", help="Run against the configured live LLM provider.")
    parser.add_argument("--json-out", type=str, default="", help="Optional path to save JSON scorecard.")
    args = parser.parse_args()

    report = run_live_evals() if args.live else run_dry_evals()
    for row in report["results"]:
        status = "PASS" if row["passed"] else "FAIL"
        print(f"[{status}] {row['id']} ({row['elapsed_ms']} ms) — {row['title']}: {row['details']}")
    print(f"\nSummary ({report['mode']}): {report['passed']}/{report['total']} passed in {report['elapsed_ms']} ms")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
