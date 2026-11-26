"""Characterization tests for AppState.set_preferences (QUALITY-PLAN-2 Q4.2)."""
from __future__ import annotations

from types import SimpleNamespace

from akatikito.app_api import AppState
from akatikito.workspace import Workspace


def test_set_preferences_validates_booleans_budget_vision_and_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    try:
        # Busy guard
        state.busy = True
        assert "error" in state.set_preferences({"autonomous": True})
        state.busy = False

        # Non-boolean rejected
        assert "error" in state.set_preferences({"autonomous": "yes"})
        assert "error" in state.set_preferences({"supports_vision": 1})

        # Negative / invalid budget rejected
        assert "error" in state.set_preferences({"daily_budget": -5})
        assert "error" in state.set_preferences({"daily_budget": "nan"})
        assert "error" in state.set_preferences({"prices": {"input": 1.0}})

        # Vision requires connected agent
        assert "error" in state.set_preferences({"supports_vision": True})

        # Routing, fallback, budget, theme, font, language, sidebar all persist cleanly
        res = state.set_preferences({
            "autonomous": True,
            "cautious_edits": True,
            "advanced_view": True,
            "fast_provider": "DeepSeek",
            "fast_model": "deepseek-chat",
            "smart_provider": "OpenRouter",
            "smart_model": "anthropic/claude-sonnet-4",
            "fallback_provider": "Ollama (τοπικό)",
            "fallback_model": "qwen2.5-coder",
            "daily_budget": 3.5,
            "ui_language": "en",
            "ui_theme": "light",
            "ui_font_size": 17,
            "sidebar_collapsed": True,
        })
        assert not res.get("error")
        assert res["fast_provider"] == "DeepSeek" and res["fast_model"] == "deepseek-chat"
        assert res["smart_provider"] == "OpenRouter" and res["smart_model"] == "anthropic/claude-sonnet-4"
        assert res["fallback_provider"] == "Ollama (τοπικό)" and res["fallback_model"] == "qwen2.5-coder"
        assert res["preferences"]["daily_budget"] == 3.5
        assert res["preferences"]["ui_language"] == "en"
        assert res["preferences"]["ui_theme"] == "light"
        assert res["preferences"]["ui_font_size"] == 17
        assert res["preferences"]["sidebar_collapsed"] is True

        # Connected agent receives vision & prices & auto_mode updates
        fake_cfg = SimpleNamespace(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            verify_command="",
            auto_mode=False,
            supports_vision=False,
            checkpoints=True,
            ui_language="en",
            fast_provider="", fast_model="", fast_base_url="", fast_api_key="",
            smart_provider="", smart_model="", smart_base_url="", smart_api_key="",
            fallback_provider="", fallback_model="", fallback_base_url="", fallback_api_key="",
        )
        fake_agent = SimpleNamespace(
            config=fake_cfg,
            ws=Workspace(tmp_path),
            model=SimpleNamespace(model="deepseek-chat"),
            mode="code",
            _manual_mode=True,
            _default_model_name="deepseek-chat",
            tokens={},
            context=SimpleNamespace(metrics={}),
            turn_usage={},
            subtasks=[],
            task=SimpleNamespace(todos=[]),
            cache_id="c1",
            stop=lambda: None,
        )
        state.agent = fake_agent
        res2 = state.set_preferences({
            "supports_vision": True,
            "auto_mode": True,
            "model": "deepseek-reasoner",
            "prices": {"input": 0.5, "cached": 0.05, "output": 1.5},
        })
        assert not res2.get("error")
        assert fake_cfg.supports_vision is True
        assert fake_cfg.auto_mode is True and fake_agent._manual_mode is False
        assert fake_cfg.model == "deepseek-reasoner" and fake_agent.model.model == "deepseek-reasoner"
    finally:
        state.close()
