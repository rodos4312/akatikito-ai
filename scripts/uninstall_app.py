"""Safe uninstaller for Akatikito portable installations (F1).

Removes application files and shortcuts while preserving `data/` (settings, keys, memory,
family profiles, sessions) unless `--purge-data` is explicitly passed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APP_DIRS = (
    "akatikito",
    "python",
    ".venv",
    "scripts",
    "skills",
    "assets",
    "docs",
    "packaging",
    "tests",
    "dist",
    "build",
    "__pycache__",
)
APP_FILES = (
    "app.py",
    "MANIFEST.json",
    "requirements.txt",
    "requirements-features.txt",
    "requirements-dev.txt",
    "ruff.toml",
    "pytest.ini",
    "README.md",
    "CHANGELOG.md",
    "AGENTS.md",
    "Akatikito.cmd",
    "AkatikitoApp.cmd",
    "akatikito-cli.cmd",
    "Doctor.cmd",
    "Install.cmd",
    "check.cmd",
    "sync-core.cmd",
)


def uninstall_installation(
    root: Path = ROOT,
    *,
    purge_data: bool = False,
    remove_shortcuts: bool = True,
) -> dict:
    """Remove application files; preserve `data/` unless `purge_data=True`."""
    target = Path(root).expanduser().resolve()
    removed_shortcuts: list[str] = []
    if remove_shortcuts:
        try:
            from scripts.create_shortcuts import remove_windows_shortcuts

            removed_shortcuts = [str(p) for p in remove_windows_shortcuts()]
        except (ImportError, OSError):
            removed_shortcuts = []

    removed_items: list[str] = []
    errors: list[str] = []
    for name in APP_DIRS:
        p = target / name
        if p.is_dir():
            try:
                shutil.rmtree(p)
                removed_items.append(name)
            except OSError as exc:
                errors.append(f"{name}: {exc}")
    for name in APP_FILES:
        p = target / name
        if p.is_file():
            try:
                p.unlink()
                removed_items.append(name)
            except OSError as exc:
                errors.append(f"{name}: {exc}")

    data_dir = target / "data"
    if purge_data and data_dir.exists():
        try:
            shutil.rmtree(data_dir)
            removed_items.append("data")
        except OSError as exc:
            errors.append(f"data: {exc}")

    return {
        "ok": not errors,
        "removed": removed_items,
        "shortcuts_removed": removed_shortcuts,
        "data_preserved": not purge_data,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Uninstall Akatikito while preserving user data by default.")
    parser.add_argument("--root", default=str(ROOT), help="Installation directory")
    parser.add_argument("--purge-data", action="store_true", help="Also delete user data/ (settings, memory, keys)")
    parser.add_argument("--keep-shortcuts", action="store_true", help="Do not remove Windows shortcuts")
    args = parser.parse_args(argv)
    res = uninstall_installation(
        Path(args.root),
        purge_data=args.purge_data,
        remove_shortcuts=not args.keep_shortcuts,
    )
    if res["data_preserved"]:
        print("Η εφαρμογή απεγκαταστάθηκε. Τα προσωπικά σου δεδομένα (data/) διατηρήθηκαν.")
    else:
        print("Η εφαρμογή και ο φάκελος δεδομένων (data/) διαγράφηκαν.")
    if res["errors"]:
        for err in res["errors"]:
            print("ΣΦΑΛΜΑ:", err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
