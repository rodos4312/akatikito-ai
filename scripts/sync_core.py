"""Sync the single source-of-truth Python core (`akatikito/`) into Akatikito Mobile
(`android/app/src/main/python/akatikito/`) before building an APK.

Preserves mobile-only shims outside `akatikito/` (such as `openai/__init__.py` and
`mobile_server.py`) and strips desktop-only runtime/cache artifacts.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "akatikito"
DEFAULT_DST = ROOT.parent / "AKATIKITO_MOBILE" / "android" / "app" / "src" / "main" / "python" / "akatikito"

SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "template_parsers"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".part"}


def sync_core(src: Path = DEFAULT_SRC, dst: Path = DEFAULT_DST, *, dry_run: bool = False) -> dict:
    src = Path(src).resolve()
    dst = Path(dst).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Source core directory not found: {src}")

    copied: list[str] = []
    removed: list[str] = []
    expected_rel: set[Path] = set()

    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if item.is_dir():
            continue
        if item.suffix.lower() in SKIP_SUFFIXES or ".broken." in item.name or ".bak." in item.name:
            continue
        expected_rel.add(rel)
        target = dst / rel
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        copied.append(rel.as_posix())

    if dst.is_dir():
        for existing in sorted(dst.rglob("*")):
            if existing.is_dir():
                continue
            rel = existing.relative_to(dst)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if rel not in expected_rel:
                if not dry_run:
                    existing.unlink(missing_ok=True)
                removed.append(rel.as_posix())

    return {"src": str(src), "dst": str(dst), "copied": len(copied), "removed": len(removed), "dry_run": dry_run}


def check_sync(src: Path = DEFAULT_SRC, dst: Path = DEFAULT_DST) -> dict:
    """Compare Desktop core vs Mobile core without modifying anything."""
    import hashlib

    src = Path(src).resolve()
    dst = Path(dst).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Source core directory not found: {src}")
    if not dst.is_dir():
        return {"in_sync": False, "missing": ["<destination directory missing>"], "modified": [], "extra": []}

    def _sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    expected_rel: set[Path] = set()
    missing: list[str] = []
    modified: list[str] = []
    extra: list[str] = []

    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or item.is_dir():
            continue
        if item.suffix.lower() in SKIP_SUFFIXES or ".broken." in item.name or ".bak." in item.name:
            continue
        expected_rel.add(rel)
        target = dst / rel
        if not target.is_file():
            missing.append(rel.as_posix())
        elif _sha(item) != _sha(target):
            modified.append(rel.as_posix())

    for existing in sorted(dst.rglob("*")):
        if existing.is_dir():
            continue
        rel = existing.relative_to(dst)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel not in expected_rel:
            extra.append(rel.as_posix())

    in_sync = not (missing or modified or extra)
    return {"in_sync": in_sync, "missing": missing, "modified": modified, "extra": extra}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync akatikito/ core from Desktop to Mobile repo")
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    parser.add_argument("--dst", default=str(DEFAULT_DST))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true", help="Compare without copying; exit 1 if out of sync")
    args = parser.parse_args(argv)
    if args.check:
        res = check_sync(Path(args.src), Path(args.dst))
        if res["in_sync"]:
            print(f"OK: Mobile core is in sync with Desktop ({args.dst}).")
            return 0
        print(
            f"CORE DRIFT: missing={len(res['missing'])}, modified={len(res['modified'])}, extra={len(res['extra'])}. "
            "Run: python scripts/sync_core.py"
        )
        return 1
    res = sync_core(Path(args.src), Path(args.dst), dry_run=args.dry_run)
    tag = "[DRY-RUN] " if res["dry_run"] else ""
    print(f"{tag}Synced {res['copied']} files (removed {res['removed']} stale) -> {res['dst']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
