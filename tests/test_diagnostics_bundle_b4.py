"""B4 Diagnostics Bundle tests: required files, zero leaked API keys, scrubbed OS usernames, preview."""
from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace

from akatikito.diagnostics import export_diagnostics_zip
from akatikito.diagnostics_bundle import preview_diagnostics_bundle, scrub_user_paths
from akatikito.settings import Settings


def test_scrub_user_paths_masks_windows_and_unix_usernames():
    assert scrub_user_paths(r"C:\Users\Γιώργος\Documents\secret.txt") == r"C:\Users\<user>\Documents\secret.txt"
    assert scrub_user_paths("C:/Users/Alice/project/main.py") == "C:/Users/<user>/project/main.py"
    assert scrub_user_paths("/home/bob/notes.md") == "/home/<user>/notes.md"
    assert scrub_user_paths("/Users/maria/app.py") == "/Users/<user>/app.py"


def test_diagnostics_bundle_contains_required_files_and_zero_secrets_or_usernames(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_key = "sk-test-9876543210abcdefghijklmnop"
    (data_dir / "app.log").write_text(
        f"2026-11-26 INFO loaded C:\\Users\\Γιώργος\\work\\a.py key={fake_key}\n",
        encoding="utf-8",
    )
    (data_dir / "errors.log").write_text(
        f"RuntimeError at C:\\Users\\Γιώργος\\work\\fail.py with token {fake_key}\n",
        encoding="utf-8",
    )
    (data_dir / "audit.jsonl").write_text(
        json.dumps({"action": "edit", "path": r"C:\Users\Γιώργος\work\main.py", "prompt": "private chat text"}) + "\n",
        encoding="utf-8",
    )

    cfg = Settings(provider="OpenAI", model="gpt-4o-mini", project=str(tmp_path))
    cfg.set_key("OpenAI", fake_key)
    state = SimpleNamespace(
        settings=cfg,
        agent=None,
        mcp=None,
        error=f"Crash with {fake_key} in C:\\Users\\Γιώργος\\x.py",
        _files_root=lambda: tmp_path,
    )

    preview = preview_diagnostics_bundle(state, data_dir=data_dir)
    assert preview["auto_upload"] is False
    names = {f["name"] for f in preview["files"]}
    assert {"summary.txt", "doctor.json", "logs/last-500.txt", "audit/last-200.jsonl", "errors.txt"} <= names

    out_zip = tmp_path / "support-bundle.zip"
    res = export_diagnostics_zip(state, dest_path=out_zip, allow_dialog=False, data_dir=data_dir)
    assert res["ok"] is True and out_zip.is_file()

    with zipfile.ZipFile(out_zip, "r") as zf:
        archive_names = set(zf.namelist())
        assert {"summary.txt", "doctor.json", "logs/last-500.txt", "audit/last-200.jsonl", "errors.txt"} <= archive_names
        for member in archive_names:
            text = zf.read(member).decode("utf-8", errors="replace")
            assert fake_key not in text, f"Leaked API key in {member}"
            assert "Γιώργος" not in text, f"Leaked OS username in {member}"
            assert "private chat text" not in text, f"Leaked private prompt in {member}"
        audit_text = zf.read("audit/last-200.jsonl").decode("utf-8")
        assert "main.py" in audit_text
