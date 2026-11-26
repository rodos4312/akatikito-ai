"""Q6 E2E lifecycle tests: clean server + UI smoke, verified upgrade with hash proof, and rollback rehearsal."""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import threading
import zipfile
from pathlib import Path

import pytest

from akatikito import memory, settings as settings_mod
from akatikito.agent import Akatikito
from akatikito.app_api import AppState, start_server
from akatikito.config import Config
from akatikito.doctor import Doctor
from akatikito.github_updater import apply_github_update
from akatikito.memory_store import MemoryStore
from akatikito.sessions import SessionStore


class _ScriptedModel:
    def chat(self, messages, tools, cache_key=""):
        return {"content": "E2E scripted response OK", "tool_calls": []}, {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
        }


def _http_req(port: int, method: str, path: str, body: dict | None = None, token: str = ""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Akatikito-Token"] = token
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, raw


def _hash_tree(paths: list[Path]) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
        if p.is_file()
    }


def _build_release_zip(version: str, files: dict[str, bytes]) -> tuple[bytes, bytes]:
    manifest = {
        "version": version,
        "files": [
            {"path": rel, "sha256": hashlib.sha256(raw).hexdigest()}
            for rel, raw in sorted(files.items())
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2).encode("utf-8"))
        for rel, raw in files.items():
            zf.writestr(rel, raw)
    zip_bytes = buf.getvalue()
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()
    sums_bytes = f"{zip_sha}  Akatikito-v{version}.zip\n".encode("utf-8")
    return zip_bytes, sums_bytes


def test_q6_e2e_clean_server_ui_assets_i18n_and_scripted_chat(tmp_path, monkeypatch):
    proj = tmp_path / "project"
    proj.mkdir()
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    monkeypatch.setattr(settings_mod, "DEFAULT_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(memory, "DIR", mem_dir)

    # 1. Clean Doctor check on isolated workspace
    doc_report = Doctor(app_root=Path(__file__).resolve().parents[1]).diagnose()
    errs = [c for c in doc_report["checks"] if c["status"] == "error"]
    assert not errs, f"Doctor reported errors: {errs}"

    # 2. Boot real HTTP server with scripted model
    state = AppState()
    state.settings.project = str(proj)
    state.agent = Akatikito(str(proj), Config(verify_command=" ", auto_mode=False), _ScriptedModel())
    state.store = SessionStore(proj)
    done_event = threading.Event()
    orig_push = state.push

    def _on_push(kind, **data):
        orig_push(kind, **data)
        if kind == "done":
            done_event.set()

    state.push = _on_push

    _url, server = start_server(state)
    port = server.server_address[1]
    token = server.auth_token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # 3. UI shell & split JS/CSS smoke
        for asset in ("/", "/ui/studio.css", "/ui/studio.js", "/ui/memory.js", "/ui/chat.js", "/ui/settings.js"):
            code, raw = _http_req(port, "GET", asset)
            assert code == 200 and len(raw) > 100, f"Asset failed: {asset} -> {code}"

        # 4. All 6 languages serve valid i18n bundles
        for lang in ("el", "en", "ru", "fr", "de", "es"):
            code, raw = _http_req(port, "POST", "/api/i18n", {"lang": lang}, token=token)
            assert code == 200
            data = json.loads(raw)
            assert data["lang"] == lang and data["strings"].get("tab_mobile")

        # 5. Preferences & memory endpoints respond cleanly
        code, raw = _http_req(port, "POST", "/api/preferences", {"ui_language": "en", "ui_font_size": 16}, token=token)
        prefs = json.loads(raw)
        assert code == 200 and not prefs.get("error")
        assert state.settings.ui_language == "en" and state.settings.ui_font_size == 16

        code, raw = _http_req(port, "POST", "/api/memory", {}, token=token)
        assert code == 200 and "rows" in json.loads(raw)

        # 6. Full scripted chat conversation end-to-end
        code, raw = _http_req(port, "POST", "/api/send", {"text": "Say hello for E2E"}, token=token)
        assert code == 200 and json.loads(raw).get("ok") is True
        assert done_event.wait(10), "Scripted chat turn did not finish in time"
        assert not state.error
        assert any("E2E scripted response OK" in str(m.get("content", "")) for m in state.agent.messages)
    finally:
        server.shutdown()
        server.server_close()


def test_q6_e2e_upgrade_preserves_user_data_hashes_and_rolls_back_cleanly(tmp_path, monkeypatch):
    app_root = tmp_path / "akatikito_install"
    (app_root / "akatikito").mkdir(parents=True)
    (app_root / "data" / "memory").mkdir(parents=True)
    (app_root / ".agent" / "sessions").mkdir(parents=True)

    # Seed v2.4.0 app files
    (app_root / "app.py").write_text("# Akatikito v2.4.0\nVERSION = '2.4.0'\n", encoding="utf-8")
    (app_root / "akatikito" / "diagnostics.py").write_text("APP_VERSION = '2.4.0'\n", encoding="utf-8")

    # Seed REAL user data: SQLite Central Memory DB + profile + settings + session
    mem_db = app_root / "data" / "memory" / "memory.db"
    mstore = MemoryStore(mem_db)
    mstore.add("User prefers dark mode and concise Greek answers.", scope="global", source="explicit")
    mstore.close()
    profile_f = app_root / "data" / "profile.json"
    profile_f.write_text(json.dumps({"owner": "Rhodes", "notes": ["keep me safe"]}), encoding="utf-8")
    settings_f = app_root / "data" / "settings.json"
    settings_f.write_text(json.dumps({"ui_language": "el", "font_size": 15}), encoding="utf-8")
    session_f = app_root / ".agent" / "sessions" / "sess-1.json"
    session_f.write_text(json.dumps({"id": "sess-1", "messages": [{"role": "user", "content": "hi"}]}), encoding="utf-8")

    user_files = [mem_db, profile_f, settings_f, session_f]
    hashes_before = _hash_tree(user_files)

    # 1. Verified upgrade v2.4.0 -> v2.5.0
    zip_v25, sums_v25 = _build_release_zip(
        "2.5.0",
        {
            "app.py": b"# Akatikito v2.5.0\nVERSION = '2.5.0'\n",
            "akatikito/diagnostics.py": b"APP_VERSION = '2.5.0'\n",
        },
    )
    zip_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.5.0/Akatikito-v2.5.0.zip"
    sha_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.5.0/SHA256SUMS.txt"
    fetcher = lambda url, *_a: sums_v25 if url == sha_url else zip_v25

    res = apply_github_update(app_root, zip_url, sha_url, "Akatikito-v2.5.0.zip", fetcher=fetcher)
    assert res["ok"] is True and res["version"] == "2.5.0"
    assert "2.5.0" in (app_root / "app.py").read_text(encoding="utf-8")
    assert _hash_tree(user_files) == hashes_before, "User data hashes changed during upgrade!"

    # 2. Failed upgrade v2.5.0 -> v2.6.0 rolls back atomically with zero data loss
    zip_v26, sums_v26 = _build_release_zip(
        "2.6.0",
        {
            "app.py": b"# Akatikito v2.6.0\nVERSION = '2.6.0'\n",
            "akatikito/diagnostics.py": b"APP_VERSION = '2.6.0'\n",
        },
    )
    real_replace = os.replace
    calls = {"n": 0}

    def _fail_second_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("Simulated disk lock during upgrade")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_second_replace)
    zip26_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.6.0/Akatikito-v2.6.0.zip"
    sha26_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.6.0/SHA256SUMS.txt"
    fetcher26 = lambda url, *_a: sums_v26 if url == sha26_url else zip_v26

    with pytest.raises(RuntimeError, match="αναιρέθηκε με ασφάλεια"):
        apply_github_update(app_root, zip26_url, sha26_url, "Akatikito-v2.6.0.zip", fetcher=fetcher26)

    # Rolled back cleanly to v2.5.0 AND all user data hashes still 100% identical!
    assert "2.5.0" in (app_root / "app.py").read_text(encoding="utf-8")
    assert "2.5.0" in (app_root / "akatikito" / "diagnostics.py").read_text(encoding="utf-8")
    assert _hash_tree(user_files) == hashes_before
    mcheck = MemoryStore(mem_db)
    try:
        assert mcheck.integrity_ok() is True
        assert len(mcheck.active()) == 1
    finally:
        mcheck.close()
