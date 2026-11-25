"""Backend tests for Studio UI preferences (sidebar_collapsed, 12-20px font, fallback) and assets."""
from __future__ import annotations

import threading
import urllib.request

from akatikito.app_api import AppState, start_server
from akatikito.settings import Settings


def test_settings_persists_sidebar_collapsed_font_20_and_fallback(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(
        sidebar_collapsed=True,
        ui_font_size=20,
        fallback_provider="OpenRouter",
        fallback_model="deepseek/deepseek-chat",
    )
    s.save(path)

    loaded = Settings.load(path)
    assert loaded.sidebar_collapsed is True
    assert loaded.ui_font_size == 20
    assert loaded.fallback_provider == "OpenRouter"
    assert loaded.fallback_model == "deepseek/deepseek-chat"


def test_app_state_preferences_and_studio_asset_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    try:
        err = state.set_preferences({"sidebar_collapsed": "yes"})
        assert "error" in err

        ok = state.set_preferences({
            "sidebar_collapsed": True,
            "ui_font_size": 20,
            "fallback_provider": "OpenRouter",
            "fallback_model": "openai/gpt-4o-mini",
        })
        assert not ok.get("error")
        assert ok["preferences"]["sidebar_collapsed"] is True
        assert ok["preferences"]["ui_font_size"] == 20
        assert ok["fallback_provider"] == "OpenRouter"
        assert ok["fallback_model"] == "openai/gpt-4o-mini"

        url, server = start_server(state)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            for asset, expected_ct in (
                ("/ui/studio.css", "text/css"),
                ("/ui/studio.js", "text/javascript"),
            ):
                with urllib.request.urlopen(url + asset, timeout=3) as resp:
                    assert resp.status == 200
                    assert expected_ct in resp.headers.get("Content-Type", "")
                    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
                    assert len(resp.read()) > 100
        finally:
            server.shutdown()
            server.server_close()
    finally:
        state.close()
