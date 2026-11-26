"""F3 real-entry-point tests: simple local/cloud onboarding, friendly errors in 6 langs, existing users skip wizard."""
from __future__ import annotations

import http.server
import json
import threading

from akatikito import i18n
from akatikito.app_api import AppState
from akatikito.settings import Settings


def test_f3_all_6_languages_have_simple_onboarding_examples_and_friendly_errors():
    keys = (
        "setup_how_title",
        "setup_opt_cloud",
        "setup_opt_local",
        "setup_opt_advanced",
        "setup_ex_1",
        "setup_ex_2",
        "setup_ex_3",
        "err_bad_key",
        "err_no_internet",
        "err_no_balance",
    )
    for lang in i18n.SUPPORTED_LANGUAGES:
        bundle = i18n.TRANSLATIONS[lang]
        for k in keys:
            assert bundle.get(k), f"Missing {k} in {lang}"


def test_f3_appstate_detect_local_endpoint_completes_onboarding_and_existing_install_skips_wizard(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")

    # Fake local Ollama/OpenAI-compatible loopback server
    class FakeLocalHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/v1/models":
                body = json.dumps({"data": [{"id": "llama3.2-local"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    srv = http.server.HTTPServer(("127.0.0.1", 0), FakeLocalHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    try:
        state = AppState()
        assert state.providers()["onboarding"]["complete"] is False

        detected = state.detect_local_ai(
            endpoints=[("LM Studio", f"http://127.0.0.1:{port}/v1/models", f"http://127.0.0.1:{port}/v1")]
        )
        assert detected["found"] and detected["found"][0]["models"] == ["llama3.2-local"]

        # Complete onboarding through real AppState.connect using the detected local endpoint
        proj = tmp_path / "MyFolder"
        proj.mkdir()
        res = state.connect(
            provider="Custom",
            base_url=f"http://127.0.0.1:{port}/v1",
            model="llama3.2-local",
            api_key="",
            project=str(proj),
            max_steps=25,
            verify="",
        )
        assert res.get("connected") is True and res["onboarding"]["complete"] is True
        state.close()

        # Re-open AppState (existing installation) -> onboarding_complete stays True (never re-shows wizard)
        state2 = AppState()
        assert state2.providers()["onboarding"]["complete"] is True
        state2.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_f3_bad_key_network_and_quota_return_plain_user_language_messages_no_traceback(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")

    cfg = Settings(ui_language="en")
    cfg.save(data_dir / "settings.json")
    state = AppState()

    def raise_401(_url, _key, force=True):
        raise RuntimeError("HTTP 401 Unauthorized: invalid_api_key provided")

    monkeypatch.setattr("akatikito.app_api.list_models", raise_401)
    bad = state.load_models("OpenAI", "https://api.openai.com/v1", "sk-wrong-123")
    assert bad["models"] == []
    err = bad["error"]
    assert err and "RuntimeError" not in err and "Traceback" not in err
    assert "key" in err.lower()

    # Check friendly classifier for Greek no-internet and no-balance too
    from akatikito.onboarding_helpers import friendly_connection_error

    no_net = friendly_connection_error("URLError: getaddrinfo failed", lang="el")
    assert "internet" in no_net.lower() or "σύνδεσ" in no_net.lower()
    assert "URLError" not in no_net

    no_bal = friendly_connection_error("HTTP 402 insufficient_quota", lang="el")
    assert "υπόλοιπ" in no_bal.lower()
    assert "402" not in no_bal or "υπόλοιπ" in no_bal.lower()
    state.close()
