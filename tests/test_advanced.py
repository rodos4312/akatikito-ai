"""Contract tests for 3-door Advanced feature gate (PRO-PLAN D0+D6): UI + API 403 + Model tools."""
from __future__ import annotations

import http.client
import json
import threading
from urllib.parse import urlsplit

from akatikito import advanced
from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.app_api_server import start_server
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.settings import Settings


def test_advanced_features_closed_by_default_enforces_all_3_doors(tmp_path, monkeypatch):
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", tmp_path / "settings.json")
    st = Settings()
    assert st.advanced_features == []

    # Door 1 (API): every declared route of every closed Feature returns HTTP 403
    state = AppState()
    state.settings = st
    state.agent = Akatikito(str(tmp_path), Config(verify_command=" "), ScriptedModel([]))
    state.agent._blocked_advanced_tools = advanced.blocked_tools(st)
    url, server = start_server(state)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        parsed = urlsplit(url)
        token = server.auth_token
        for feat in advanced.FEATURES.values():
            for route in feat.routes:
                conn = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=4)
                conn.request("POST", route, json.dumps({}), {"Content-Type": "application/json", "X-Akatikito-Token": token})
                resp = conn.getresponse()
                body = json.loads(resp.read().decode("utf-8"))
                conn.close()
                assert resp.status == 403, f"Route {route} must return 403 when {feat.key} is closed"
                assert "Advanced" in body.get("error", "")
    finally:
        server.shutdown()
        server.server_close()

    # Door 2 (Model tools): disabled feature tools absent from allowed_tools and blocked on execution
    code_tools = state.agent.allowed_tools("code")
    for feat in advanced.FEATURES.values():
        for tool_name in feat.tools:
            assert tool_name not in code_tools

    # Door 3 (Chat intent + UI catalog): self_update blocked via chat and shown disabled in catalog
    res = state.send("αναβάθμισε τον εαυτό σου")
    assert res.get("error") and "Advanced" in res["error"]
    assert state.self_update is None
    assert all(row["enabled"] is False for row in state.status()["advanced_catalog"])


def test_enabling_advanced_features_persists_and_unlocks_tools_and_rejects_unknown(tmp_path, monkeypatch):
    spath = tmp_path / "settings.json"
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", spath)
    state = AppState()
    state.agent = Akatikito(str(tmp_path), Config(verify_command=" "), ScriptedModel([]))
    state.agent._blocked_advanced_tools = advanced.blocked_tools(state.settings)

    # Unknown feature key rejected
    bad = state.set_preferences({"advanced_features": ["nonexistent_feature"]})
    assert bad.get("error")

    # Enabling subtasks + git unlocks those tools and persists to settings.json
    ok = state.set_preferences({"advanced_features": ["subtasks", "git"], "apply_now": True})
    assert not ok.get("error")
    assert {"run_subtask", "run_subtasks", "git_status_diff"} <= state.agent.allowed_tools("code")
    reloaded = Settings.load(spath)
    assert reloaded.advanced_features == ["subtasks", "git"]
