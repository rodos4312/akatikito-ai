"""Build a clean, leak-free portable GitHub release ZIP with SHA-256 manifest & checksums."""
from __future__ import annotations

import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from akatikito.diagnostics import APP_VERSION

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".agent",
    ".agent_backups",
    "data",
    "dist",
    "build",
    "node_modules",
    ".vscode",
    ".idea",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".lock", ".zip"}
FORBIDDEN_NAMES = {"settings.json", "mcp.json", "channels.json", "scheduled.json", "profile.json", "skill_proposals.json"}


def collect_release_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if path.name.lower() in FORBIDDEN_NAMES:
            raise RuntimeError(f"Refusing to package sensitive file: {rel.as_posix()}")
        files.append(path)
    return files


def build_portable_release(root: Path = ROOT, out_dir: Path | None = None, version: str = APP_VERSION) -> dict:
    target_dir = Path(out_dir) if out_dir is not None else (root / "dist")
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"Akatikito-v{version}-portable.zip"
    zip_path = target_dir / zip_name

    files = collect_release_files(root)
    manifest_entries = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            manifest_entries.append({"path": rel, "size": len(raw), "sha256": sha})
            zf.writestr(f"Akatikito/{rel}", raw)
        manifest = {
            "app": "Akatikito",
            "version": version,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_count": len(manifest_entries),
            "files": manifest_entries,
        }
        zf.writestr("Akatikito/MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))

    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sums_path = target_dir / "SHA256SUMS.txt"
    sums_path.write_text(f"{zip_sha}  {zip_name}\n", encoding="utf-8")
    return {
        "zip_path": str(zip_path),
        "sums_path": str(sums_path),
        "sha256": zip_sha,
        "file_count": len(manifest_entries),
        "version": version,
    }


def main() -> int:
    info = build_portable_release()
    print(f"Built {info['zip_path']} ({info['file_count']} files)")
    print(f"SHA-256: {info['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
