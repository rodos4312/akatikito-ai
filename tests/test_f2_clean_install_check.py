"""F2 real-entry-point tests: clean-install, headless boot, doctor, upgrade & rollback with data hash check."""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_build_portable():
    spec = importlib.util.spec_from_file_location("build_portable_f2", ROOT / "packaging" / "build_portable.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_f2_clean_install_script_and_ci_wiring_exist():
    script = ROOT / "scripts" / "clean_install_check.ps1"
    assert script.is_file(), "Missing scripts/clean_install_check.ps1"
    ci_yml = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "clean_install_check.ps1" in ci_yml


def test_f2_upgrade_and_rollback_preserve_user_data_byte_for_byte(tmp_path):
    from akatikito.github_updater import apply_github_update, restore_github_update_backup

    mod = _load_build_portable()
    src_old = tmp_path / "src_old"
    src_old.mkdir()
    (src_old / "app.py").write_text("VERSION = 'old'\n", encoding="utf-8")
    (src_old / "akatikito").mkdir()
    (src_old / "akatikito" / "marker.py").write_text("TAG = 'v1'\n", encoding="utf-8")
    old_dist = mod.build_portable_release(root=src_old, out_dir=tmp_path / "dist_old", version="2.4.0")

    src_new = tmp_path / "src_new"
    shutil.copytree(src_old, src_new)
    (src_new / "akatikito" / "marker.py").write_text("TAG = 'v2'\n", encoding="utf-8")
    new_dist = mod.build_portable_release(root=src_new, out_dir=tmp_path / "dist_new", version="2.5.0")

    install_root = tmp_path / "install"
    with zipfile.ZipFile(old_dist["zip_path"]) as zf:
        zf.extractall(install_root)
    app_dir = install_root / "Akatikito"

    # Seed user data and record exact hashes BEFORE upgrade
    data_dir = app_dir / "data"
    (data_dir / "memory").mkdir(parents=True)
    settings_file = data_dir / "settings.json"
    memory_db = data_dir / "memory" / "memory.db"
    settings_file.write_text('{"provider": "OpenAI", "daily_budget": 2.0}\n', encoding="utf-8")
    memory_db.write_bytes(b"SQLite-owner-data-1234567890")
    before_settings = _sha(settings_file)
    before_memory = _sha(memory_db)

    new_zip = Path(new_dist["zip_path"])
    new_sums = Path(new_dist["sums_path"])
    zip_url = f"https://github.com/rodos4312/akatikito-ai/releases/download/v2.5.0/{new_zip.name}"
    sha_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.5.0/SHA256SUMS.txt"

    def local_fetch(url: str, _max: int, _acc: str, _to: int) -> bytes:
        return new_sums.read_bytes() if url.endswith("SHA256SUMS.txt") else new_zip.read_bytes()

    res = apply_github_update(app_dir, zip_url=zip_url, sha_url=sha_url, zip_name=new_zip.name, fetcher=local_fetch)
    assert res["ok"] is True and res["version"] == "2.5.0"
    assert "TAG = 'v2'" in (app_dir / "akatikito" / "marker.py").read_text(encoding="utf-8")
    assert _sha(settings_file) == before_settings
    assert _sha(memory_db) == before_memory

    # Rollback to backup restores v1 AND preserves user data byte-for-byte
    rb = restore_github_update_backup(app_dir, Path(res["backup_dir"]))
    assert rb["ok"] is True
    assert "TAG = 'v1'" in (app_dir / "akatikito" / "marker.py").read_text(encoding="utf-8")
    assert _sha(settings_file) == before_settings
    assert _sha(memory_db) == before_memory


def test_f2_powershell_clean_install_script_passes_on_windows(tmp_path):
    if sys.platform != "win32":
        return
    mod = _load_build_portable()
    cached_embed = ROOT / "build" / "python-embed-cache" / mod.PYTHON_EMBED_FILENAME
    site_cache = ROOT / ".venv" / "Lib" / "site-packages"
    if not (cached_embed.is_file() and site_cache.is_dir()):
        return

    old_out = mod.build_portable_release(
        root=ROOT,
        out_dir=tmp_path / "old_dist",
        version="2.4.9-old",
        include_python=True,
        python_embed_zip=cached_embed,
        site_packages_dir=site_cache,
    )
    new_out = mod.build_portable_release(
        root=ROOT,
        out_dir=tmp_path / "new_dist",
        version="2.5.0-new",
        include_python=False,  # source overlay update (or full zip)
    )
    report_file = tmp_path / "report.txt"
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "clean_install_check.ps1"),
            "-OldZip",
            old_out["zip_path"],
            "-NewZip",
            new_out["zip_path"],
            "-WorkDir",
            str(tmp_path / "sandbox"),
            "-ReportPath",
            str(report_file),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    report = report_file.read_text(encoding="utf-8")
    assert "RESULT: PASS" in report
    assert "DATA_HASH_AFTER_UPGRADE: PASS" in report
    assert "DATA_HASH_AFTER_ROLLBACK: PASS" in report
