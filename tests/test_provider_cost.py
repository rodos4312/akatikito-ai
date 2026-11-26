"""Tests for live provider-reported cost (OpenRouter usage.cost -> ledger, daily budget & UI delta)."""
from __future__ import annotations

from types import SimpleNamespace

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.streaming import usage_of
from akatikito.usage_ledger import UsageLedger, summarize_turn_delta


def test_usage_of_extracts_openrouter_cost_from_dict_and_sdk_model_extra():
    u1 = usage_of({"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0125})
    assert u1["provider_cost"] == 0.0125

    sdk_usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=400,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1500),
        model_extra={"cost": 0.031},
    )
    u2 = usage_of(sdk_usage)
    assert u2["cache_hit_tokens"] == 1500
    assert u2["provider_cost"] == 0.031

    # Reject invalid / negative / NaN / bool values
    assert "provider_cost" not in usage_of({"prompt_tokens": 10, "cost": -1.0})
    assert "provider_cost" not in usage_of({"prompt_tokens": 10, "cost": float("nan")})
    assert "provider_cost" not in usage_of({"prompt_tokens": 10, "cost": True})


def test_usage_ledger_records_provider_cost_and_enforces_daily_budget(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.json")
    recorded = ledger.record(
        "sess1",
        {"prompt_tokens": 50000, "completion_tokens": 3000, "cache_hit_tokens": 40000, "provider_cost": 0.051},
        prices=None,
        model="particle-ai/particle-1",
        project="game",
    )
    assert recorded == 0.051
    view = ledger.view("sess1")
    assert round(view["session"]["cost"], 4) == 0.051
    assert view["day"]["unknown"] == 0
    assert ledger.check(budget=1.0, session="sess1", prices=None) == ""
    assert "ημερήσιο όριο κόστους" in ledger.check(budget=0.05, session="sess1", prices=None)


def test_agent_turn_accumulates_provider_cost_into_summarize_turn_delta(tmp_path):
    class CostModel:
        def chat(self, messages, tools=None, cache_key=None):
            return {"content": "Έτοιμο.", "tool_calls": []}, {
                "prompt_tokens": 10000,
                "completion_tokens": 500,
                "cache_hit_tokens": 8000,
                "provider_cost": 0.0234,
            }

    agent = Akatikito(str(tmp_path), Config(auto_mode=False), CostModel())
    before = dict(agent.tokens)
    assert agent.chat("Γεια") == "Έτοιμο."
    delta = summarize_turn_delta(before, agent.tokens, prices=None)
    assert delta["cost_usd"] == 0.0234
    assert delta["cost_eur"] is None
    assert delta["cache_hit_pct"] == 80

    # Second turn must accumulate without double-counting provider_cost
    before2 = dict(agent.tokens)
    assert agent.chat("Πάλι") == "Έτοιμο."
    delta2 = summarize_turn_delta(before2, agent.tokens, prices=None)
    assert delta2["cost_usd"] == 0.0234
    assert round(agent.tokens["provider_cost"], 4) == 0.0468
