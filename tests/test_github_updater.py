"""Security & transactional tests for the verified GitHub Releases updater."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_release_zip(files: dict[str, bytes], version: str = "2.1.0", tamper_manifest: bool = False) -> tuple[bytes, bytes]:
    manifest_rows = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, raw in files.items():
            sha = hashlib.sha256(raw).hexdigest()
            manifest_rows.append({"path": rel, "size": len(raw), "sha256": ("0" * 64 if tamper_manifest else sha)})
            zf.writestr(f"Akatikito/{rel}", raw)
        manifest = {"app": "Akatikito", "version": version, "file_count": len(manifest_rows), "files": manifest_rows}
        zf.writestr("Akatikito/MANIFEST.json", json.dumps(manifest).encode("utf-8"))
    zbytes = buf.getvalue()
    sums = f"{hashlib.sha256(zbytes).hexdigest()}  Akatikito-v{version}-portable.zip\n".encode("utf-8")
    return zbytes, sums


def test_version_parsing_and_github_release_check():
    from akatikito.github_updater import check_github_update, is_newer_version, parse_version

    assert parse_version("v2.1.0") == (2, 1, 0)
    assert is_newer_version("2.1.0", "2.0.0") is True
    assert is_newer_version("2.1.0", "2.1.0") is False
    assert is_newer_version("2.0.9", "2.1.0") is False

    fake_release = {
        "tag_name": "v2.2.0",
        "name": "Akatikito AI v2.2.0",
        "html_url": "https://github.com/rodos4312/akatikito-ai/releases/tag/v2.2.0",
        "body": "Security & speed improvements.",
        "published_at": "2026-11-23T10:00:00Z",
        "assets": [
            {
                "name": "Akatikito-v2.2.0-portable.zip",
                "browser_download_url": "https://github.com/rodos4312/akatikito-ai/releases/download/v2.2.0/Akatikito-v2.2.0-portable.zip",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://github.com/rodos4312/akatikito-ai/releases/download/v2.2.0/SHA256SUMS.txt",
            },
        ],
    }

    info = check_github_update(
        current_version="2.1.0",
        fetcher=lambda _u, _m, _a, _t: json.dumps(fake_release).encode("utf-8"),
    )
    assert info["update_available"] is True
    assert info["can_auto_install"] is True
    assert info["latest_version"] == "2.2.0"


def test_apply_github_update_verifies_sha256_preserves_user_data_and_backs_up(tmp_path):
    from akatikito.github_updater import apply_github_update

    # Existing installation with user secrets & venv
    (tmp_path / "app.py").write_text("# old app v2.0.0\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text('{"keys":{"OpenAI":"dpapi:v1:KEEP"}}', encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "marker.txt").write_text("venv-intact", encoding="utf-8")

    zbytes, sums = _make_release_zip(
        {
            "app.py": b"# new app v2.1.0\n",
            "akatikito/new_mod.py": b"VALUE = 42\n",
        },
        version="2.1.0",
    )

    zip_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.1.0/Akatikito-v2.1.0-portable.zip"
    sha_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.1.0/SHA256SUMS.txt"

    def fake_fetch(url, _max, _acc, _to):
        return sums if url == sha_url else zbytes

    res = apply_github_update(tmp_path, zip_url, sha_url, "Akatikito-v2.1.0-portable.zip", fetcher=fake_fetch)
    assert res["ok"] is True
    assert res["version"] == "2.1.0"
    assert res["updated_files"] == 2
    assert res["restart_required"] is True
    # Updated files applied
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "# new app v2.1.0\n"
    assert (tmp_path / "akatikito" / "new_mod.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    # Backup of old app.py exists
    assert (Path(res["backup_dir"]) / "app.py").read_text(encoding="utf-8") == "# old app v2.0.0\n"
    # User data & venv untouched!
    assert "dpapi:v1:KEEP" in (tmp_path / "data" / "settings.json").read_text(encoding="utf-8")
    assert (tmp_path / ".venv" / "marker.txt").read_text(encoding="utf-8") == "venv-intact"


def test_apply_github_update_rejects_untrusted_host_tampered_sha_and_protected_paths(tmp_path):
    from akatikito.github_updater import apply_github_update

    zbytes, sums = _make_release_zip({"app.py": b"ok\n"}, version="2.1.0")
    zip_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.1.0/Akatikito-v2.1.0-portable.zip"
    sha_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.1.0/SHA256SUMS.txt"

    # 1. Untrusted host rejected
    with pytest.raises(ValueError, match="Μη αξιόπιστη"):
        apply_github_update(tmp_path, "https://evil.example.com/a.zip", sha_url, fetcher=lambda *_a: b"")

    # 2. Tampered ZIP rejected on SHA-256 mismatch
    with pytest.raises(ValueError, match="SHA-256"):
        apply_github_update(
            tmp_path,
            zip_url,
            sha_url,
            "Akatikito-v2.1.0-portable.zip",
            fetcher=lambda u, *_a: sums if u == sha_url else (zbytes + b"tampered"),
        )

    # 3. Tampered MANIFEST file hash rejected
    bad_z, bad_sums = _make_release_zip({"app.py": b"ok\n"}, version="2.1.0", tamper_manifest=True)
    with pytest.raises(ValueError, match="Αλλοιωμένο"):
        apply_github_update(
            tmp_path,
            zip_url,
            sha_url,
            "Akatikito-v2.1.0-portable.zip",
            fetcher=lambda u, *_a: bad_sums if u == sha_url else bad_z,
        )

    # 4. Attempt to overwrite user data/settings.json rejected
    evil_z, evil_sums = _make_release_zip({"data/settings.json": b"{}"}, version="2.1.0")
    with pytest.raises(PermissionError, match="δεν επιτρέπεται"):
        apply_github_update(
            tmp_path,
            zip_url,
            sha_url,
            "Akatikito-v2.1.0-portable.zip",
            fetcher=lambda u, *_a: evil_sums if u == sha_url else evil_z,
        )
