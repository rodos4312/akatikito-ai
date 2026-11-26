"""Automated release readiness & docs-drift gate (QUALITY-PLAN-2 Q9 & Q11).

Checks enforced automatically:
1. Version consistency: APP_VERSION in diagnostics.py matches CHANGELOG.md, README.md, docs/index.html.
2. AGENTS.md size cap: every tracked AGENTS.md <= 120 lines.
3. Settings docs-drift: every field in akatikito/settings.py::Settings is documented in docs/SETTINGS.md.
4. Optional release artifact verification: SHA256SUMS.txt + MANIFEST.json integrity (--verify-zip).
5. Backup retention helper: prune_backups(root, keep=3) preserves the 3 most recent release backups.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_MAX_LINES = 120
SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__", "template_parsers"}


def _read_app_version(root: Path) -> str:
    diag = root / "akatikito" / "diagnostics.py"
    if not diag.is_file():
        return ""
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', diag.read_text(encoding="utf-8-sig"), re.M)
    return m.group(1).strip() if m else ""


def check_version_consistency(root: Path = ROOT) -> list[str]:
    version = _read_app_version(root)
    if not version:
        return ["[Q9-VER] Could not read APP_VERSION from akatikito/diagnostics.py."]
    errors: list[str] = []
    checks = (
        ("CHANGELOG.md", rf"##\s*\[v?{re.escape(version)}\]", f"heading '## [v{version}]'"),
        ("README.md", rf"Release-v{re.escape(version)}", f"Release badge 'Release-v{version}'"),
        ("docs/index.html", rf'"softwareVersion"\s*:\s*"{re.escape(version)}"', f'softwareVersion "{version}"'),
    )
    for rel, pattern, desc in checks:
        target = root / rel
        if not target.is_file():
            errors.append(f"[Q9-VER] Missing {rel} (expected {desc}).")
            continue
        text = target.read_text(encoding="utf-8-sig", errors="replace")
        if not re.search(pattern, text):
            errors.append(f"[Q9-VER] {rel} does not match APP_VERSION={version} (missing {desc}). Fix: Update {rel} to v{version}.")
    return errors


def check_agents_md_size(root: Path = ROOT, max_lines: int = AGENTS_MAX_LINES) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("AGENTS.md")):
        if set(path.relative_to(root).parts) & SKIP_DIRS:
            continue
        lines = len(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
        if lines > max_lines:
            rel = path.relative_to(root).as_posix()
            errors.append(
                f"[Q11-AGENTS] {rel} has {lines} lines > {max_lines} limit. "
                "Fix: Keep AGENTS.md concise and move long explanations into docs/adr/."
            )
    return errors


def _settings_fields(root: Path) -> list[str]:
    settings_py = root / "akatikito" / "settings.py"
    if not settings_py.is_file():
        return []
    tree = ast.parse(settings_py.read_text(encoding="utf-8-sig"), filename=str(settings_py))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            fields: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.append(stmt.target.id)
            return fields
    return []


def check_settings_docs_drift(root: Path = ROOT) -> list[str]:
    fields = _settings_fields(root)
    if not fields:
        return []
    doc_path = root / "docs" / "SETTINGS.md"
    if not doc_path.is_file():
        return ["[Q11-DRIFT] Missing docs/SETTINGS.md. Fix: Create docs/SETTINGS.md documenting Settings fields."]
    doc_text = doc_path.read_text(encoding="utf-8-sig", errors="replace")
    missing = [name for name in fields if f"`{name}`" not in doc_text]
    if not missing:
        return []
    return [
        f"[Q11-DRIFT] Undocumented Settings field(s) in docs/SETTINGS.md: {', '.join(missing)}. "
        "Fix: Add a row for each new Settings field to docs/SETTINGS.md."
    ]


def verify_release_zip(zip_path: Path, sums_path: Path) -> list[str]:
    if not zip_path.is_file() or not sums_path.is_file():
        return [f"[Q9-SIG] Missing release archive or checksum file: {zip_path.name}, {sums_path.name}"]
    actual_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
    expected = ""
    for line in sums_path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*").endswith(zip_path.name):
            expected = parts[0].lower()
            break
    if not expected:
        return [f"[Q9-SIG] {zip_path.name} not found in {sums_path.name}."]
    if actual_sha != expected:
        return [f"[Q9-SIG] SHA-256 mismatch for {zip_path.name}: expected {expected[:12]}, got {actual_sha[:12]}."]
    with zipfile.ZipFile(zip_path) as zf:
        manifest_names = [i.filename for i in zf.infolist() if i.filename.replace("\\", "/").endswith("MANIFEST.json")]
        if not manifest_names:
            return [f"[Q9-SIG] Missing MANIFEST.json inside {zip_path.name}."]
        manifest = json.loads(zf.read(manifest_names[0]).decode("utf-8"))
        if not manifest.get("files"):
            return [f"[Q9-SIG] Empty MANIFEST.json inside {zip_path.name}."]
    return []


def prune_backups(backup_root: Path, keep: int = 3) -> list[Path]:
    """Keep the newest `keep` release backup directories and remove older ones."""
    if not backup_root.is_dir():
        return []
    dirs = sorted(
        [p for p in backup_root.iterdir() if p.is_dir()],
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    removed: list[Path] = []
    for old in dirs[max(1, int(keep)):]:
        shutil.rmtree(old)
        removed.append(old)
    return removed


def _check_git_release_ready(root: Path) -> list[str]:
    git_bin = shutil.which("git")
    if not git_bin or not (root / ".git").exists():
        return []
    errors: list[str] = []
    br = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=10)
    if br.returncode == 0 and br.stdout.strip() != "main":
        errors.append(f"[Q9-GIT] Release must be cut from 'main' (currently '{br.stdout.strip()}').")
    st = subprocess.run([git_bin, "status", "--porcelain"], cwd=str(root), capture_output=True, text=True, timeout=10)
    if st.returncode == 0 and st.stdout.strip():
        errors.append("[Q9-GIT] Working tree is dirty. Fix: Commit or stash changes before cutting a release.")
    return errors


def check_all(root: Path = ROOT, *, strict_git: bool = False) -> list[str]:
    errors = []
    errors.extend(check_version_consistency(root))
    errors.extend(check_agents_md_size(root))
    errors.extend(check_settings_docs_drift(root))
    if strict_git:
        errors.extend(_check_git_release_ready(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Release readiness and docs-drift checks (Q9 & Q11).")
    parser.add_argument("--strict-git", action="store_true", help="Also verify clean working tree on main branch")
    parser.add_argument("--verify-zip", help="Verify a release ZIP archive against --sums")
    parser.add_argument("--sums", help="Path to SHA256SUMS.txt for --verify-zip")
    args = parser.parse_args(argv)

    errors = check_all(ROOT, strict_git=args.strict_git)
    if args.verify_zip:
        sums = Path(args.sums) if args.sums else Path(args.verify_zip).with_name("SHA256SUMS.txt")
        errors.extend(verify_release_zip(Path(args.verify_zip), sums))
    if errors:
        print("RELEASE/DOCS CHECKS FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    ver = _read_app_version(ROOT)
    print(f"OK: release & docs-drift checks passed (v{ver}, AGENTS.md <= {AGENTS_MAX_LINES} lines, Settings documented).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
