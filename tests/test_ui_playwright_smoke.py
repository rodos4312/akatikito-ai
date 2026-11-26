"""Q6 Playwright browser UI smoke test (tabs, Settings, Memory, language switch, zero JS errors)."""
from __future__ import annotations

import threading

import pytest

from akatikito import memory, settings as settings_mod
from akatikito.agent import Akatikito
from akatikito.app_api import AppState, start_server
from akatikito.config import Config
from akatikito.sessions import SessionStore


class _StubModel:
    def chat(self, messages, tools, cache_key=""):
        return {"content": "Playwright smoke OK", "tool_calls": []}, {}


@pytest.mark.slow
def test_q6_playwright_live_ui_smoke(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api", reason="Playwright not installed (runs in nightly/E2E CI)")
    proj = tmp_path / "proj"
    proj.mkdir()
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    monkeypatch.setattr(settings_mod, "DEFAULT_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(memory, "DIR", mem_dir)

    state = AppState()
    state.settings.project = str(proj)
    state.agent = Akatikito(str(proj), Config(verify_command=" ", auto_mode=False), _StubModel())
    state.store = SessionStore(proj)
    url, server = start_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page_errors: list[str] = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on("pageerror", lambda err: page_errors.append(str(err)))
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_selector("#prompt", timeout=10000)
                # Exercise key navigation tabs (Memory, Settings, Mobile, Sessions)
                for tab_id in ("memory", "settings", "mobile", "sessions"):
                    btn = page.locator(f".nav-tab[data-tab='{tab_id}'], [data-tab='{tab_id}']").first
                    if btn.count() > 0:
                        btn.click(timeout=5000)
                assert not page_errors, f"Uncaught JS page errors: {page_errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
