"""Build a clean, leak-free portable GitHub release ZIP with pinned embedded Python 3.11 + SHA-256."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as importlib_md
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from akatikito.diagnostics import APP_VERSION  # noqa: E402

PYTHON_EMBED_VERSION = "3.11.9"
PYTHON_EMBED_FILENAME = f"python-{PYTHON_EMBED_VERSION}-embed-amd64.zip"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/{PYTHON_EMBED_FILENAME}"
PYTHON_EMBED_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"

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
    "python",
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


def verify_python_embed_zip(zip_path: Path, *, expected_sha256: str = PYTHON_EMBED_SHA256) -> str:
    """Verify the embeddable Python archive against its pinned SHA-256 digest."""
    target = Path(zip_path)
    if not target.is_file():
        raise FileNotFoundError(f"Embeddable Python archive not found: {target}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest().lower()
    expected = str(expected_sha256 or "").strip().lower()
    if not expected or actual != expected:
        raise ValueError(
            f"SHA-256 mismatch for {target.name}: expected {expected[:16]}…, got {actual[:16]}…."
        )
    return actual


def ensure_python_embed_zip(
    cache_dir: Path,
    *,
    expected_sha256: str = PYTHON_EMBED_SHA256,
    url: str = PYTHON_EMBED_URL,
) -> Path:
    """Return verified local copy of the pinned embeddable Python ZIP, downloading if absent."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / PYTHON_EMBED_FILENAME
    if not dest.is_file():
        tmp = dest.with_suffix(".tmp")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 — pinned python.org HTTPS URL
                tmp.write_bytes(resp.read())
            verify_python_embed_zip(tmp, expected_sha256=expected_sha256)
            tmp.replace(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    else:
        verify_python_embed_zip(dest, expected_sha256=expected_sha256)
    return dest


def _patch_pth_content(raw_text: str) -> str:
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for entry in ("..", "Lib/site-packages", "import site"):
        if entry not in lines:
            lines.append(entry)
    return "\n".join(lines) + "\n"


def _requirement_names(req_file: Path) -> list[str]:
    if not req_file.is_file():
        return []
    names: list[str] = []
    for line in req_file.read_text(encoding="utf-8-sig").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        pkg = re.split(r"[<>=!~;\s\[]", clean)[0].strip()
        if pkg:
            names.append(pkg)
    return names


def _resolve_closure_files(site_dir: Path, req_names: list[str]) -> list[tuple[str, Path]]:
    """Collect exact transitive dependency files of requirements.txt from an installed site-packages."""
    dists_by_key: dict[str, importlib_md.Distribution] = {}
    for dist in importlib_md.distributions(path=[str(site_dir)]):
        name = dist.metadata.get("Name") or ""
        if name:
            dists_by_key[re.sub(r"[-_.]+", "-", name).lower()] = dist
    if not dists_by_key or not req_names:
        return []
    seen: set[str] = set()
    queue = list(req_names)
    collected: dict[str, Path] = {}
    while queue:
        raw_name = queue.pop(0)
        key = re.sub(r"[-_.]+", "-", raw_name).lower()
        if key in seen or key not in dists_by_key:
            continue
        seen.add(key)
        dist = dists_by_key[key]
        for rel_file in dist.files or ():
            full = Path(dist.locate_file(rel_file))
            if not full.is_file() or "__pycache__" in full.parts or full.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            try:
                rel_posix = full.resolve().relative_to(site_dir.resolve()).as_posix()
            except ValueError:
                continue
            collected[rel_posix] = full
        for dep_spec in dist.requires or ():
            if "extra ==" in dep_spec or "extra==" in dep_spec:
                continue
            dep = re.split(r"[<>=!~;\s\[]", dep_spec.strip())[0].strip()
            if dep:
                queue.append(dep)
    return sorted(collected.items())


def _collect_site_packages(site_dir: Path, requirements_file: Path) -> list[tuple[str, Path]]:
    closure = _resolve_closure_files(site_dir, _requirement_names(requirements_file))
    if closure:
        return closure
    out: list[tuple[str, Path]] = []
    for path in sorted(site_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(site_dir)
        if "__pycache__" in rel.parts or path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        out.append((rel.as_posix(), path))
    return out


def _install_requirements_to_temp(requirements_file: Path, target_dir: Path) -> Path:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(target_dir),
        "--no-compile",
        "--quiet",
        "-r",
        str(requirements_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"pip install for portable bundle failed: {proc.stderr or proc.stdout}")
    return target_dir


def _add_embedded_python_to_zip(
    zf: zipfile.ZipFile,
    manifest_entries: list[dict],
    embed_zip: Path,
    site_files: list[tuple[str, Path]],
    expected_sha256: str,
) -> None:
    verify_python_embed_zip(embed_zip, expected_sha256=expected_sha256)
    with zipfile.ZipFile(embed_zip) as pyzf:
        for info in pyzf.infolist():
            if info.is_dir():
                continue
            rel_name = info.filename.replace("\\", "/").lstrip("/")
            raw = pyzf.read(info.filename)
            if rel_name.endswith("._pth"):
                raw = _patch_pth_content(raw.decode("utf-8", errors="replace")).encode("utf-8")
            arc_rel = f"python/{rel_name}"
            manifest_entries.append({"path": arc_rel, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            zf.writestr(f"Akatikito/{arc_rel}", raw)
    for rel_posix, full_path in site_files:
        raw = full_path.read_bytes()
        arc_rel = f"python/Lib/site-packages/{rel_posix}"
        manifest_entries.append({"path": arc_rel, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        zf.writestr(f"Akatikito/{arc_rel}", raw)


def build_portable_release(
    root: Path = ROOT,
    out_dir: Path | None = None,
    version: str = APP_VERSION,
    *,
    include_python: bool = False,
    python_embed_zip: Path | None = None,
    expected_python_sha256: str = PYTHON_EMBED_SHA256,
    site_packages_dir: Path | None = None,
) -> dict:
    target_dir = Path(out_dir) if out_dir is not None else (root / "dist")
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"Akatikito-v{version}-portable.zip"
    zip_path = target_dir / zip_name

    files = collect_release_files(root)
    manifest_entries: list[dict] = []
    req_file = root / "requirements.txt"

    embed_path: Path | None = None
    tmp_ctx = None
    site_files: list[tuple[str, Path]] = []
    if include_python:
        embed_path = (
            Path(python_embed_zip)
            if python_embed_zip is not None
            else ensure_python_embed_zip(root / "build" / "python-embed-cache", expected_sha256=expected_python_sha256)
        )
        verify_python_embed_zip(embed_path, expected_sha256=expected_python_sha256)
        if site_packages_dir is not None:
            site_files = _collect_site_packages(Path(site_packages_dir), req_file)
        elif (root / ".venv" / "Lib" / "site-packages").is_dir():
            site_files = _collect_site_packages(root / ".venv" / "Lib" / "site-packages", req_file)
        elif req_file.is_file():
            tmp_ctx = tempfile.TemporaryDirectory(prefix="akatikito-site-")
            installed = _install_requirements_to_temp(req_file, Path(tmp_ctx.name))
            site_files = _collect_site_packages(installed, req_file)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                rel = path.relative_to(root).as_posix()
                raw = path.read_bytes()
                manifest_entries.append({"path": rel, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                zf.writestr(f"Akatikito/{rel}", raw)
            if include_python and embed_path is not None:
                _add_embedded_python_to_zip(zf, manifest_entries, embed_path, site_files, expected_python_sha256)
            manifest = {
                "app": "Akatikito",
                "version": version,
                "python_embedded": PYTHON_EMBED_VERSION if include_python else "",
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "file_count": len(manifest_entries),
                "files": manifest_entries,
            }
            zf.writestr("Akatikito/MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sums_path = target_dir / "SHA256SUMS.txt"
    sums_path.write_text(f"{zip_sha}  {zip_name}\n", encoding="utf-8")
    return {
        "zip_path": str(zip_path),
        "sums_path": str(sums_path),
        "sha256": zip_sha,
        "size_bytes": zip_path.stat().st_size,
        "file_count": len(manifest_entries),
        "version": version,
        "python_embedded": PYTHON_EMBED_VERSION if include_python else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Akatikito portable ZIP release.")
    parser.add_argument("--no-python", action="store_true", help="Build source-only ZIP without embedded Python")
    parser.add_argument("--out-dir", default="", help="Output directory (default: dist/)")
    parser.add_argument("--version", default=APP_VERSION, help="Version tag for archive name")
    args = parser.parse_args(argv)
    info = build_portable_release(
        out_dir=Path(args.out_dir) if args.out_dir else None,
        version=args.version,
        include_python=not args.no_python,
    )
    mb = info["size_bytes"] / (1024 * 1024)
    print(f"Built {info['zip_path']} ({info['file_count']} files, {mb:.2f} MB, python={info['python_embedded'] or 'none'})")
    print(f"SHA-256: {info['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
