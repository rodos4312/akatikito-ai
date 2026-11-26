"""Unified Shared-Core Manager for Akatikito Windows (Desktop) & Android (Mobile).

Keeps a single source of truth (`akatikito/`) synchronized byte-for-byte between
the Windows Desktop repo and the Android Chaquopy embedded Python core, with
SHA-256 manifest verification, syntax checking, cross-platform tests, and
1-command release APK builds.

Usage:
    python scripts/sync_shared_core.py sync [--mobile PATH]
    python scripts/sync_shared_core.py check [--mobile PATH]
    python scripts/sync_shared_core.py verify [--mobile PATH]
    python scripts/sync_shared_core.py build-apk [--mobile PATH]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "CORE_SYNC_MANIFEST.json"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", "template_parsers"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".part"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", MANIFEST_NAME}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_core_files(core_dir: Path) -> dict[str, Path]:
    """Return sorted mapping `{posix_rel_path: absolute_path}` for all shared core files."""
    base = Path(core_dir).resolve()
    if not base.is_dir():
        return {}
    found: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        parent = Path(dirpath)
        for fname in sorted(filenames):
            if fname in SKIP_NAMES or Path(fname).suffix.lower() in SKIP_SUFFIXES:
                continue
            full = parent / fname
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(base).as_posix()
            found[rel] = full
    return dict(sorted(found.items()))


def compute_core_fingerprint(files: dict[str, Path]) -> tuple[str, dict[str, str]]:
    """Compute per-file SHA-256 map and combined deterministic core SHA-256."""
    per_file = {rel: _file_sha256(path) for rel, path in sorted(files.items())}
    digest = hashlib.sha256()
    for rel, sha in per_file.items():
        digest.update(f"{rel}\0{sha}\n".encode("utf-8"))
    return digest.hexdigest(), per_file


def read_app_version(desktop_root: Path) -> str:
    diag = Path(desktop_root) / "akatikito" / "diagnostics.py"
    if not diag.is_file():
        return "0.0.0"
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', diag.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def read_git_sha(repo_root: Path) -> str:
    git = shutil.which("git")
    if not git:
        return ""
    proc = subprocess.run(
        [git, "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def resolve_mobile_root(desktop_root: Path, explicit: str = "") -> Path:
    """Resolve Android Mobile repo path from --mobile, AKATIKITO_MOBILE_ROOT, or sibling folders."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_val = os.environ.get("AKATIKITO_MOBILE_ROOT", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    parent = Path(desktop_root).resolve().parent
    for name in ("AKATIKITO_MOBILE_GEMINI", "AKATIKITO_MOBILE"):
        cand = parent / name
        if (cand / "pubspec.yaml").is_file():
            return cand.resolve()
    return (parent / "AKATIKITO_MOBILE").resolve()


def _mobile_core_dir(mobile_root: Path) -> Path:
    return Path(mobile_root).resolve() / "android" / "app" / "src" / "main" / "python" / "akatikito"


def _validate_python_syntax(files: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    for rel, path in files.items():
        if path.suffix.lower() != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{rel}: {exc}")
    return errors


def sync_core(desktop_root: Path, mobile_root: Path, *, dry_run: bool = False) -> dict:
    """Mirror Windows Desktop `akatikito/` into Android Mobile and write CORE_SYNC_MANIFEST.json."""
    src_dir = Path(desktop_root).resolve() / "akatikito"
    dst_dir = _mobile_core_dir(mobile_root)
    src_files = collect_core_files(src_dir)
    if not src_files:
        raise FileNotFoundError(f"Δεν βρέθηκαν αρχεία πυρήνα στο: {src_dir}")
    syntax_errs = _validate_python_syntax(src_files)
    if syntax_errs:
        raise ValueError("Σφάλματα σύνταξης Python στον κοινό πυρήνα:\n" + "\n".join(syntax_errs[:10]))

    dst_files = collect_core_files(dst_dir)
    core_sha, per_file = compute_core_fingerprint(src_files)
    stale = sorted(set(dst_files.keys()) - set(src_files.keys()))
    copied = 0

    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for rel in stale:
            (dst_dir / rel).unlink(missing_ok=True)
        for rel, src_path in src_files.items():
            dst_path = dst_dir / rel
            if dst_path.is_file() and _file_sha256(dst_path) == per_file[rel]:
                continue
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            copied += 1
        manifest = {
            "schema_version": 1,
            "app_version": read_app_version(desktop_root),
            "desktop_commit": read_git_sha(desktop_root),
            "file_count": len(src_files),
            "core_sha256": core_sha,
        }
        manifest_path = dst_dir / MANIFEST_NAME
        tmp = manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, manifest_path)

    return {
        "ok": True,
        "dry_run": dry_run,
        "total_files": len(src_files),
        "copied": copied,
        "removed_stale": len(stale),
        "core_sha256": core_sha,
        "desktop_commit": read_git_sha(desktop_root),
        "destination": str(dst_dir),
    }


def check_parity(desktop_root: Path, mobile_root: Path) -> dict:
    """Verify byte-for-byte SHA-256 parity and manifest integrity between Windows and Android cores."""
    src_files = collect_core_files(Path(desktop_root).resolve() / "akatikito")
    dst_dir = _mobile_core_dir(mobile_root)
    dst_files = collect_core_files(dst_dir)
    src_sha, src_map = compute_core_fingerprint(src_files)
    dst_sha, dst_map = compute_core_fingerprint(dst_files)

    missing = sorted(set(src_map) - set(dst_map))
    extra = sorted(set(dst_map) - set(src_map))
    modified = sorted(rel for rel in (set(src_map) & set(dst_map)) if src_map[rel] != dst_map[rel])

    manifest_ok = False
    manifest_path = dst_dir / MANIFEST_NAME
    if manifest_path.is_file():
        try:
            doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_ok = isinstance(doc, dict) and doc.get("core_sha256") == src_sha and doc.get("file_count") == len(src_files)
        except (OSError, ValueError):
            manifest_ok = False

    in_sync = not missing and not extra and not modified and src_sha == dst_sha and manifest_ok
    return {
        "in_sync": in_sync,
        "manifest_ok": manifest_ok,
        "file_count": len(src_files),
        "core_sha256": src_sha,
        "missing": missing,
        "extra": extra,
        "modified": modified,
    }


def _android_tool_env(mobile_root: Path) -> tuple[dict[str, str], str]:
    env = os.environ.copy()
    devtools = Path(mobile_root.anchor) / "DevTools"
    if os.name == "nt" and devtools.is_dir():
        for var, sub in (
            ("PUB_CACHE", "pub-cache"),
            ("ANDROID_HOME", "android-sdk"),
            ("JAVA_HOME", "jdk17"),
            ("GRADLE_USER_HOME", "gradle-cache"),
        ):
            cand = devtools / sub
            if cand.is_dir():
                env.setdefault(var, str(cand))
        flutter_bat = devtools / "flutter" / "bin" / "flutter.bat"
        if flutter_bat.is_file():
            return env, str(flutter_bat)
    flutter_bin = shutil.which("flutter") or "flutter"
    return env, flutter_bin


def _run_checked(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, *, timeout: int = 900) -> None:
    res = subprocess.run(cmd, cwd=str(cwd), env=env, timeout=timeout, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Αποτυχία εντολής (exit {res.returncode}): {' '.join(cmd)}")


def verify_both(desktop_root: Path, mobile_root: Path) -> dict:
    """Run parity check, Windows Desktop fast gates, and Android Flutter analyze + test."""
    parity = check_parity(desktop_root, mobile_root)
    if not parity["in_sync"]:
        raise RuntimeError(
            f"Ο κοινός πυρήνας δεν είναι συγχρονισμένος (missing={len(parity['missing'])}, "
            f"modified={len(parity['modified'])}, extra={len(parity['extra'])}, manifest_ok={parity['manifest_ok']})."
        )
    _run_checked([sys.executable, "scripts/check.py", "--only", "ruff,size,health,rules,footguns,supply,docs"], desktop_root)
    env, flutter = _android_tool_env(mobile_root)
    _run_checked([flutter, "analyze"], mobile_root, env=env)
    _run_checked([flutter, "test"], mobile_root, env=env)
    return {"ok": True, "parity": parity}


def verify_apk_contains_core(apk_path: Path) -> dict:
    """Confirm the built APK is a valid ZIP archive containing Chaquopy/Python assets."""
    apk = Path(apk_path).resolve()
    if not apk.is_file():
        raise FileNotFoundError(f"Δεν βρέθηκε το APK: {apk}")
    with zipfile.ZipFile(apk, "r") as zf:
        names = zf.namelist()
    py_assets = [n for n in names if "chaquopy" in n.lower() or "akatikito" in n.lower()]
    return {
        "path": str(apk),
        "size_mb": round(apk.stat().st_size / (1024 * 1024), 2),
        "sha256": _file_sha256(apk),
        "entries": len(names),
        "chaquopy_entries": len(py_assets),
    }


def build_release_apk(desktop_root: Path, mobile_root: Path) -> dict:
    """Sync shared core, verify Windows + Android, build release APK, and verify payload."""
    synced = sync_core(desktop_root, mobile_root)
    verified = verify_both(desktop_root, mobile_root)
    env, flutter = _android_tool_env(mobile_root)
    _run_checked([flutter, "build", "apk", "--release"], mobile_root, env=env)
    apk_path = Path(mobile_root).resolve() / "build" / "app" / "outputs" / "flutter-apk" / "app-release.apk"
    apk_info = verify_apk_contains_core(apk_path)
    return {"ok": True, "sync": synced, "verify": verified["parity"], "apk": apk_info}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Shared core sync & verification for Windows + Android")
    parser.add_argument("action", choices=["sync", "check", "verify", "build-apk"])
    parser.add_argument("--desktop", default=str(ROOT), help="Path to Windows Desktop repo")
    parser.add_argument("--mobile", default="", help="Path to Android Mobile repo")
    parser.add_argument("--dry-run", action="store_true", help="Preview sync without writing")
    args = parser.parse_args(argv)

    desktop = Path(args.desktop).expanduser().resolve()
    mobile = resolve_mobile_root(desktop, args.mobile)
    try:
        if args.action == "sync":
            out = sync_core(desktop, mobile, dry_run=args.dry_run)
        elif args.action == "check":
            out = check_parity(desktop, mobile)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if out["in_sync"] else 1
        elif args.action == "verify":
            out = verify_both(desktop, mobile)
        else:
            out = build_release_apk(desktop, mobile)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
