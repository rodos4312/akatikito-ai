"""F1 real-entry-point tests: zero-Python portable ZIP, SHA256 verification, empty-PATH boot, safe uninstall."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_build_portable():
    spec = importlib.util.spec_from_file_location("build_portable_f1", ROOT / "packaging" / "build_portable.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_f1_python_embed_sha256_is_pinned_and_wrong_sha_fails_build(tmp_path):
    mod = _load_build_portable()
    assert mod.PYTHON_EMBED_VERSION.startswith("3.11.")
    assert len(mod.PYTHON_EMBED_SHA256) == 64

    bad_zip = tmp_path / "corrupt-python.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("python.exe", b"not-a-real-python")

    with pytest.raises(ValueError, match="SHA-256"):
        mod.verify_python_embed_zip(bad_zip, expected_sha256=mod.PYTHON_EMBED_SHA256)

    mini_repo = tmp_path / "repo"
    mini_repo.mkdir()
    (mini_repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        mod.build_portable_release(
            root=mini_repo,
            out_dir=tmp_path / "dist",
            version="2.5.0-test",
            include_python=True,
            python_embed_zip=bad_zip,
        )


def test_f1_portable_zip_contains_python_and_requirements_and_runs_with_empty_path(tmp_path):
    mod = _load_build_portable()
    cached_embed = ROOT / "build" / "python-embed-cache" / mod.PYTHON_EMBED_FILENAME
    site_cache = ROOT / ".venv" / "Lib" / "site-packages"

    if sys.platform == "win32" and cached_embed.is_file() and site_cache.is_dir():
        out = mod.build_portable_release(
            root=ROOT,
            out_dir=tmp_path / "dist",
            version="2.5.0-f1",
            include_python=True,
            python_embed_zip=cached_embed,
            site_packages_dir=site_cache,
        )
        zpath = Path(out["zip_path"])
        with zipfile.ZipFile(zpath) as zf:
            names = set(zf.namelist())
            assert "Akatikito/python/python.exe" in names
            assert "Akatikito/python/pythonw.exe" in names
            assert any(n.startswith("Akatikito/python/python") and n.endswith("._pth") for n in names)
            # Core requirements.txt packages must be bundled inside python/Lib/site-packages
            for pkg_marker in ("yaml", "httpx", "openai", "pyperclip", "snowballstemmer", "webview"):
                assert any(
                    n.startswith(f"Akatikito/python/Lib/site-packages/{pkg_marker}") for n in names
                ), f"Missing required package '{pkg_marker}' in portable ZIP"

            extract_root = tmp_path / "unpacked"
            zf.extractall(extract_root)

        app_dir = extract_root / "Akatikito"
        clean_env = {
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "ComSpec": os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"),
            "TEMP": str(tmp_path / "tmp"),
            "TMP": str(tmp_path / "tmp"),
            "PATH": "",
            "PYTHONUTF8": "1",
        }
        (tmp_path / "tmp").mkdir(exist_ok=True)

        # 2a. Launch app --version with EMPTY PATH (zero system Python!)
        ver = subprocess.run(
            [clean_env["ComSpec"], "/c", str(app_dir / "AkatikitoApp.cmd"), "--version"],
            cwd=str(app_dir),
            env=clean_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert ver.returncode == 0, ver.stdout + "\n" + ver.stderr
        assert "Akatikito" in ver.stdout

        # 2b. Run Doctor with EMPTY PATH using bundled python.exe
        doc = subprocess.run(
            [str(app_dir / "python" / "python.exe"), "-m", "akatikito.doctor", "--json"],
            cwd=str(app_dir),
            env=clean_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        assert doc.returncode == 0, doc.stdout + "\n" + doc.stderr
        parsed = json.loads(doc.stdout)
        errs = [c for c in parsed.get("checks", []) if c.get("status") == "error"]
        assert errs == [], f"Doctor errors in zero-Python portable run: {errs}"
    else:
        # Offline/Linux fallback: verify SHA-pinned embeddable bundling & zip structure
        fake_embed = tmp_path / "python-embed.zip"
        with zipfile.ZipFile(fake_embed, "w") as zf:
            zf.writestr("python.exe", b"MZ-fake")
            zf.writestr("pythonw.exe", b"MZ-fake-w")
            zf.writestr("python311._pth", "python311.zip\n.\n#import site\n")
        fake_sha = hashlib.sha256(fake_embed.read_bytes()).hexdigest()
        fake_site = tmp_path / "site"
        for pkg in ("yaml", "httpx", "openai", "pyperclip", "snowballstemmer", "webview"):
            (fake_site / pkg).mkdir(parents=True)
            (fake_site / pkg / "__init__.py").write_text("", encoding="utf-8")
        mini_repo = tmp_path / "repo"
        mini_repo.mkdir()
        (mini_repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (mini_repo / "requirements.txt").write_text((ROOT / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8")
        out = mod.build_portable_release(
            root=mini_repo,
            out_dir=tmp_path / "dist",
            version="2.5.0-f1",
            include_python=True,
            python_embed_zip=fake_embed,
            expected_python_sha256=fake_sha,
            site_packages_dir=fake_site,
        )
        with zipfile.ZipFile(out["zip_path"]) as zf:
            names = set(zf.namelist())
            assert "Akatikito/python/python.exe" in names
            assert "Akatikito/python/Lib/site-packages/httpx/__init__.py" in names
            pth = zf.read("Akatikito/python/python311._pth").decode("utf-8")
            assert "Lib/site-packages" in pth and "\nimport site" in pth


def test_f1_uninstall_removes_app_but_preserves_user_data_unless_explicitly_purged(tmp_path):
    from scripts.uninstall_app import uninstall_installation

    install_dir = tmp_path / "Akatikito"
    (install_dir / "akatikito").mkdir(parents=True)
    (install_dir / "python").mkdir(parents=True)
    (install_dir / "data" / "memory").mkdir(parents=True)
    (install_dir / "akatikito" / "__init__.py").write_text("__version__ = '2.5.0'\n", encoding="utf-8")
    (install_dir / "python" / "python.exe").write_bytes(b"exe")
    (install_dir / "app.py").write_text("pass\n", encoding="utf-8")
    (install_dir / "data" / "settings.json").write_text('{"provider": "OpenAI"}\n', encoding="utf-8")
    (install_dir / "data" / "memory" / "memory.db").write_bytes(b"sqlite-owner-memory")

    # Default uninstall: removes app code + bundled python, PRESERVES data/
    res = uninstall_installation(install_dir, purge_data=False, remove_shortcuts=False)
    assert res["data_preserved"] is True
    assert not (install_dir / "akatikito").exists()
    assert not (install_dir / "python").exists()
    assert not (install_dir / "app.py").exists()
    assert (install_dir / "data" / "settings.json").is_file()
    assert (install_dir / "data" / "memory" / "memory.db").read_bytes() == b"sqlite-owner-memory"

    # Explicit purge_data=True: removes data/ as well
    res2 = uninstall_installation(install_dir, purge_data=True, remove_shortcuts=False)
    assert res2["data_preserved"] is False
    assert not (install_dir / "data").exists()
