"""Phase 7.4: One-way file-size ratchet for Akatikito.

Rules:
- New source files must be <= 800 lines.
- Existing source files currently over 800 lines may NOT grow above their recorded
  baseline in `scripts/size_baseline.json` (they may only shrink).
- Pure data catalogs (e.g. `akatikito/i18n.py`) are excluded.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "size_baseline.json"
LIMIT = 800

EXCLUDED_REL = {
    "akatikito/i18n.py",
}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "template_parsers", ".venv", "venv"}


def collect_source_files(root: Path = ROOT) -> dict[str, int]:
    counts: dict[str, int] = {}
    candidates: list[Path] = []
    app_py = root / "app.py"
    if app_py.is_file():
        candidates.append(app_py)
    for base in (root / "akatikito", root / "scripts"):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            if p.suffix.lower() not in (".py", ".js", ".html"):
                continue
            candidates.append(p)
    for p in candidates:
        rel = p.relative_to(root).as_posix()
        if rel in EXCLUDED_REL:
            continue
        try:
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        counts[rel] = n
    return counts


def check_sizes(
    root: Path = ROOT,
    baseline_path: Path = BASELINE_PATH,
    *,
    limit: int = LIMIT,
) -> tuple[bool, list[str]]:
    counts = collect_source_files(root)
    baseline: dict[str, int] = {}
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    errors: list[str] = []
    for rel, lines in sorted(counts.items()):
        max_allowed = max(limit, int(baseline.get(rel, limit)))
        if lines > max_allowed:
            if rel in baseline and baseline[rel] > limit:
                errors.append(f"{rel}: {lines} lines (exceeds baseline {baseline[rel]}; files >{limit} may only shrink)")
            else:
                errors.append(f"{rel}: {lines} lines (exceeds {limit}-line limit)")
    return (len(errors) == 0), errors


def write_baseline(root: Path = ROOT, baseline_path: Path = BASELINE_PATH, *, limit: int = LIMIT) -> dict[str, int]:
    counts = collect_source_files(root)
    prev: dict[str, int] = {}
    if baseline_path.is_file():
        prev = json.loads(baseline_path.read_text(encoding="utf-8"))
    new_base: dict[str, int] = {}
    for rel, lines in sorted(counts.items()):
        if lines > limit:
            # Never ratchet UP an existing baseline
            new_base[rel] = min(lines, int(prev[rel])) if rel in prev else lines
    baseline_path.write_text(json.dumps(new_base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return new_base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Akatikito source file line-count ratchet")
    parser.add_argument("--update-baseline", action="store_true", help="Write/ratchet scripts/size_baseline.json")
    args = parser.parse_args(argv)
    if args.update_baseline:
        base = write_baseline()
        print(f"Updated {BASELINE_PATH.name} ({len(base)} grandfathered files >{LIMIT} lines).")
        return 0
    ok, errors = check_sizes()
    if not ok:
        print("SIZE CHECK FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    print(f"OK: file sizes within limit ({LIMIT} lines / baseline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
