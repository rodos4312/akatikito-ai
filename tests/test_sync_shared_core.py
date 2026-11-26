"""Tests for unified Windows + Android shared-core sync, parity, manifest, and APK payload checks."""
from __future__ import annotations

import json
import zipfile

import pytest

from scripts.sync_shared_core import (
    MANIFEST_NAME,
    check_parity,
    sync_core,
    verify_apk_contains_core,
)


def _setup_fake_repos(tmp_path):
    desktop = tmp_path / "desktop"
    mobile = tmp_path / "mobile"
    core = desktop / "akatikito"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "diagnostics.py").write_text('APP_VERSION = "2.5.0"\n', encoding="utf-8")
    (core / "engine.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (core / "__pycache__").mkdir()
    (core / "__pycache__" / "engine.cpython-311.pyc").write_bytes(b"pyc")
    (mobile / "pubspec.yaml").parent.mkdir(parents=True)
    (mobile / "pubspec.yaml").write_text("name: akatikito_mobile\nversion: 1.9.0+21\n", encoding="utf-8")
    return desktop, mobile


def test_sync_core_mirrors_files_removes_stale_and_passes_parity_check(tmp_path):
    desktop, mobile = _setup_fake_repos(tmp_path)
    dst_core = mobile / "android" / "app" / "src" / "main" / "python" / "akatikito"
    dst_core.mkdir(parents=True)
    (dst_core / "obsolete.py").write_text("old = True\n", encoding="utf-8")

    # Before sync -> out of sync
    before = check_parity(desktop, mobile)
    assert before["in_sync"] is False
    assert "engine.py" in before["missing"]
    assert "obsolete.py" in before["extra"]

    # Sync mirrors files, skips __pycache__, deletes obsolete.py, writes manifest
    res = sync_core(desktop, mobile)
    assert res["ok"] is True
    assert res["total_files"] == 3
    assert res["removed_stale"] == 1
    assert not (dst_core / "obsolete.py").exists()
    assert not (dst_core / "__pycache__").exists()
    manifest = json.loads((dst_core / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["app_version"] == "2.5.0" and manifest["file_count"] == 3

    # Parity check passes 100%
    after = check_parity(desktop, mobile)
    assert after["in_sync"] is True and after["manifest_ok"] is True

    # Any modification on Android breaks parity immediately
    (dst_core / "engine.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
    drifted = check_parity(desktop, mobile)
    assert drifted["in_sync"] is False and drifted["modified"] == ["engine.py"]


def test_sync_core_rejects_syntax_error_and_verifies_apk_payload(tmp_path):
    desktop, mobile = _setup_fake_repos(tmp_path)
    (desktop / "akatikito" / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="σύνταξης"):
        sync_core(desktop, mobile)

    apk = tmp_path / "app-release.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"manifest")
        zf.writestr("assets/chaquopy/app.zip", b"py-bundle")
    info = verify_apk_contains_core(apk)
    assert info["chaquopy_entries"] == 1 and len(info["sha256"]) == 64
