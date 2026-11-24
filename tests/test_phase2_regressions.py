"""Phase 2 regression tests locking in all security & correctness fixes for
MobileBridge, Smart Routing, and Native Tool Plugins."""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from email.message import Message
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers for invoking MobileHandler directly (no real socket required)
# ---------------------------------------------------------------------------

def _invoke_mobile_post(bridge, path: str, body: bytes | None, headers: dict[str, str]):
    handler_cls = bridge._make_handler()
    h = handler_cls.__new__(handler_cls)
    h.path = path
    msg = Message()
    for k, v in headers.items():
        msg[k] = v
    h.headers = msg
    h.rfile = io.BytesIO(body or b"")
    h.wfile = io.BytesIO()
    captured: list[tuple[int, dict]] = []

    def _capture_send(payload: dict, code: int = 200) -> None:
        captured.append((code, payload))

    h._send = _capture_send
    h.do_POST()
    assert captured, f"Handler did not call _send for POST {path}"
    return captured[-1]


def _make_bridge_with_session(tmp_path: Path):
    from akatikito.app_api import AppState
    from akatikito.mobile_bridge import MobileBridge

    state = AppState()
    proj = tmp_path / "approved_proj"
    proj.mkdir()
    state.settings.project = str(proj)
    state.settings.recent_projects = [str(proj)]
    bridge = MobileBridge(state, store_path=tmp_path / "mobile_bridge.json")
    # Simulate active bridge without binding a real TCP socket
    bridge._server = object()
    bridge.pin = "654321"
    bridge.token = "pair-secret-token"
    ok, sess_tok = bridge._authenticate_login("654321", "pair-secret-token")
    assert ok is True
    return state, bridge, proj, sess_tok


# ---------------------------------------------------------------------------
# 1. Mobile Bridge security & upload regressions
# ---------------------------------------------------------------------------

def test_mobile_switch_outside_recent_returns_403(tmp_path):
    state, bridge, _proj, sess_tok = _make_bridge_with_session(tmp_path)
    unapproved = tmp_path / "secret_other_folder"
    unapproved.mkdir()

    payload = json.dumps({"project": str(unapproved)}).encode("utf-8")
    code, resp = _invoke_mobile_post(
        bridge,
        "/api/mobile/projects/switch",
        payload,
        {
            "X-Akatikito-Mobile-Token": sess_tok,
            "Content-Length": str(len(payload)),
        },
    )
    assert code == 403
    assert "error" in resp


def test_mobile_preferences_autonomous_returns_403(tmp_path):
    _state, bridge, _proj, sess_tok = _make_bridge_with_session(tmp_path)
    payload = json.dumps({"autonomous": True, "daily_cost_limit_eur": 9999}).encode("utf-8")
    code, resp = _invoke_mobile_post(
        bridge,
        "/api/mobile/preferences",
        payload,
        {
            "X-Akatikito-Mobile-Token": sess_tok,
            "Content-Length": str(len(payload)),
        },
    )
    assert code == 403
    assert "error" in resp


def test_mobile_upload_zero_bytes_and_missing_content_length_return_400_without_modifying_file(tmp_path):
    _state, bridge, proj, sess_tok = _make_bridge_with_session(tmp_path)
    existing = proj / "keep.txt"
    existing.write_text("ORIGINAL_CONTENT", encoding="utf-8")

    # Missing Content-Length -> 400
    code1, _ = _invoke_mobile_post(
        bridge,
        "/api/mobile/files/upload?path=keep.txt",
        b"abc",
        {"X-Akatikito-Mobile-Token": sess_tok},
    )
    assert code1 == 400
    assert existing.read_text(encoding="utf-8") == "ORIGINAL_CONTENT"

    # Zero-byte Content-Length -> 400
    code2, _ = _invoke_mobile_post(
        bridge,
        "/api/mobile/files/upload?path=keep.txt",
        b"",
        {"X-Akatikito-Mobile-Token": sess_tok, "Content-Length": "0"},
    )
    assert code2 == 400
    assert existing.read_text(encoding="utf-8") == "ORIGINAL_CONTENT"


def test_mobile_legacy_plaintext_session_token_is_ignored(tmp_path):
    from akatikito.app_api import AppState
    from akatikito.mobile_bridge import MobileBridge

    store = tmp_path / "mobile_bridge.json"
    # Legacy unhashed plaintext token (not a 64-char sha256 hex string)
    store.write_text(
        json.dumps({"auto_start": False, "sessions": {"legacy-plaintext-tok": time.time()}}),
        encoding="utf-8",
    )
    state = AppState()
    bridge = MobileBridge(state, store_path=store)
    assert bridge._is_session_valid("legacy-plaintext-tok") is False
    # Valid 64-hex sha256 hash IS accepted
    good_tok = "fresh-valid-token"
    good_hash = hashlib.sha256(good_tok.encode("utf-8")).hexdigest()
    store.write_text(
        json.dumps({"auto_start": False, "sessions": {good_hash: time.time()}}),
        encoding="utf-8",
    )
    bridge2 = MobileBridge(state, store_path=store)
    assert bridge2._is_session_valid(good_tok) is True


def test_mobile_11_wrong_pins_triggers_lockout(tmp_path):
    _state, bridge, _proj, _sess_tok = _make_bridge_with_session(tmp_path)
    for _ in range(10):
        ok, _ = bridge._authenticate_login("000000", bridge.token)
        assert ok is False
    # 11th attempt must be locked out EVEN with the correct PIN & token
    ok_11, err_11 = bridge._authenticate_login(bridge.pin, bridge.token)
    assert ok_11 is False
    assert "πολλές" in err_11 or "επανεκκίνηση" in err_11


# ---------------------------------------------------------------------------
# 2. Smart Routing regressions
# ---------------------------------------------------------------------------

def test_smart_routing_provider_without_key_never_leaks_primary_key(tmp_path):
    from akatikito.agent import Akatikito
    from akatikito.config import Config

    class StubModel:
        def __init__(self):
            self.model = "primary-m"
            self.calls = 0

        def chat(self, messages, tools=None, cache_key=""):
            self.calls += 1
            return {"content": "OK", "tool_calls": []}, {"prompt_tokens": 5, "completion_tokens": 5}

    cfg = Config(
        model="primary-m",
        base_url="https://provider-a.example/v1",
        api_key="SECRET_PRIMARY_KEY",
        fast_model="fast-b",
        fast_base_url="https://provider-b.example/v1",
        fast_api_key="",  # Missing key for different remote provider!
        auto_mode=True,
        checkpoints=False,
        stream_responses=False,
    )
    stub = StubModel()
    ag = Akatikito(str(tmp_path), cfg, stub)
    ag.chat("γεια")
    # Must stay on primary client & primary URL, never routing to provider-b without its own key
    assert ag.model is stub
    assert ag.config.base_url == "https://provider-a.example/v1"
    assert stub.calls == 1


def test_without_fast_or_smart_model_agent_model_never_changes(tmp_path):
    from akatikito.agent import Akatikito
    from akatikito.config import Config

    class StubModel:
        def __init__(self):
            self.model = "base-only"

        def chat(self, messages, tools=None, cache_key=""):
            return {"content": "OK", "tool_calls": []}, {"prompt_tokens": 5, "completion_tokens": 5}

    cfg = Config(
        model="base-only",
        base_url="https://provider-a.example/v1",
        api_key="k",
        fast_model="",
        smart_model="",
        auto_mode=True,
        checkpoints=False,
        stream_responses=False,
    )
    stub = StubModel()
    ag = Akatikito(str(tmp_path), cfg, stub)
    ag.chat("απλή ερώτηση")
    assert ag.model is stub
    assert ag.config.model == "base-only"
    assert stub.model == "base-only"


# ---------------------------------------------------------------------------
# 3. Native Tool Plugins & Read-Only profile regressions
# ---------------------------------------------------------------------------

def test_readonly_profile_blocks_pdf_image_and_zip_plugins(tmp_path):
    from akatikito.project_profiles import ProjectProfiles

    for tool_name in ("pdf_toolkit", "image_tools", "zip_manager"):
        err = ProjectProfiles.check_tool("readonly", tool_name)
        assert err and "Μόνο Ανάγνωση" in err, f"Expected Read-Only profile to block {tool_name}"


def test_zip_manager_rejects_traversal_and_sibling_prefix(tmp_path):
    from akatikito.tool_plugins import tool_zip_manager
    from akatikito.workspace import Workspace

    ws = Workspace(str(tmp_path))

    # Case 1: classic ../ traversal
    z_trav = tmp_path / "trav.zip"
    with zipfile.ZipFile(z_trav, "w") as zf:
        zf.writestr("../escaped.txt", "evil")
    with pytest.raises(PermissionError):
        tool_zip_manager(ws, "extract", "trav.zip", destination="out")

    # Case 2: sibling directory prefix (out/../out2/pwn.txt)
    z_sib = tmp_path / "sib.zip"
    with zipfile.ZipFile(z_sib, "w") as zf:
        zf.writestr("../out2/pwn.txt", "evil")
    with pytest.raises(PermissionError):
        tool_zip_manager(ws, "extract", "sib.zip", destination="out")
    assert not (tmp_path / "out2" / "pwn.txt").exists()


def test_plugin_overwrite_false_rejects_existing_file(tmp_path):
    from akatikito.tool_plugins import _guard_output_write
    from akatikito.workspace import Workspace

    ws = Workspace(str(tmp_path))
    existing = tmp_path / "report.pdf"
    existing.write_bytes(b"%PDF-1.4")

    with pytest.raises(FileExistsError):
        _guard_output_write(ws, existing, overwrite=False)
    # overwrite=True succeeds
    _guard_output_write(ws, existing, overwrite=True)
