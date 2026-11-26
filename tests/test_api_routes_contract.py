"""Characterization & security contract tests for app_api_server POST_ROUTES (Q4.1 + A4.3)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from akatikito.app_api import AppState, start_server
from akatikito.app_api_server import POST_ROUTES


def test_every_registered_post_route_rejects_missing_token_with_403(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    url, server = start_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert len(POST_ROUTES) >= 65
        for route in sorted(POST_ROUTES):
            req = urllib.request.Request(
                url + route,
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=3)
                raise AssertionError(f"Route {route} accepted unauthenticated request!")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403, f"Route {route} returned {exc.code}, expected 403"
    finally:
        server.shutdown()
        server.server_close()
        state.close()


def test_authenticated_post_routes_dispatch_and_unknown_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    url, server = start_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(route: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            url + route,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Akatikito-Token": server.auth_token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    try:
        code, res = post("/api/stop", {})
        assert code == 200 and res.get("ok") is True

        code, res = post("/api/poll", {"since": 0})
        assert code == 200 and "events" in res

        code, res = post("/api/i18n", {"lang": "el"})
        assert code == 200 and "strings" in res

        code, res = post("/api/usage/insights", {})
        assert code == 200 and "month" in res

        code, res = post("/api/sessions/search", {"query": ""})
        assert code == 200 and res == {"query": "", "results": []}

        # Busy guard on /api/mcp/disconnect preserved
        state.busy = True
        code, res = post("/api/mcp/disconnect", {"name": "demo"})
        assert code == 200 and "error" in res
        state.busy = False

        code, res = post("/api/does-not-exist", {})
        assert code == 404 and "error" in res
    finally:
        server.shutdown()
        server.server_close()
        state.close()
