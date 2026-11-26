"""Tests for Q9 release readiness gate and Q11 docs-drift / AGENTS.md cap."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import release_check as rc


def test_real_repo_passes_release_and_docs_checks():
    assert rc.check_all(ROOT, strict_git=False) == []
    assert (ROOT / "docs" / "adr" / "0001-per-call-sqlite-memory.md").is_file()
    assert (ROOT / "docs" / "adr" / "0002-narrow-core-edges.md").is_file()
    assert (ROOT / "docs" / "adr" / "0003-downward-only-quality-ratchets.md").is_file()


def test_version_mismatch_is_caught(tmp_path):
    (tmp_path / "akatikito").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "akatikito" / "diagnostics.py").write_text('APP_VERSION = "2.5.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [v2.4.0]\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Release-v2.5.0\n", encoding="utf-8")
    (tmp_path / "docs" / "index.html").write_text('"softwareVersion": "2.4.0"\n', encoding="utf-8")

    errs = rc.check_version_consistency(tmp_path)
    assert any("CHANGELOG.md" in e for e in errs)
    assert any("docs/index.html" in e for e in errs)
    assert not any("README.md" in e for e in errs)


def test_agents_md_size_cap_and_settings_drift_are_caught(tmp_path):
    (tmp_path / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(150)), encoding="utf-8")
    assert any("Q11-AGENTS" in e for e in rc.check_agents_md_size(tmp_path, max_lines=120))

    (tmp_path / "akatikito").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "akatikito" / "settings.py").write_text(
        "class Settings:\n    documented: str = ''\n    secret_new_flag: bool = False\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "SETTINGS.md").write_text("| `documented` | ok |\n", encoding="utf-8")
    drift = rc.check_settings_docs_drift(tmp_path)
    assert any("secret_new_flag" in e for e in drift)


def test_verify_release_zip_and_prune_backups(tmp_path):
    zpath = tmp_path / "Akatikito-v2.5.0.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("MANIFEST.json", json.dumps({"version": "2.5.0", "files": [{"path": "app.py", "sha256": "00"}]}))
    sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{sha}  {zpath.name}\n", encoding="utf-8")
    assert rc.verify_release_zip(zpath, sums) == []

    # Tampering checksum triggers failure
    sums.write_text(f"{'0' * 64}  {zpath.name}\n", encoding="utf-8")
    assert any("SHA-256 mismatch" in e for e in rc.verify_release_zip(zpath, sums))

    # Backup pruning keeps newest 3 release backups
    backups = tmp_path / ".agent_backups"
    backups.mkdir()
    for i in range(5):
        d = backups / f"release-{i}"
        d.mkdir()
        os.utime(d, (1000 + i * 10, 1000 + i * 10))
    removed = rc.prune_backups(backups, keep=3)
    assert len(removed) == 2
    assert sorted(p.name for p in backups.iterdir()) == ["release-2", "release-3", "release-4"]
