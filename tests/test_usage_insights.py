"""Tests for monthly cost insights, sparkline, cache savings, and breakdown (PRO-PLAN B6)."""
from __future__ import annotations

from datetime import date

from akatikito.app_api import AppState
from akatikito.usage_ledger import UsageLedger


def test_monthly_insights_tracks_models_projects_cache_savings_and_projection(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.json")
    prices = {"input": 1.0, "cached": 0.1, "output": 2.0}
    ledger.record(
        "s1",
        {"prompt_tokens": 100_000, "completion_tokens": 20_000, "cache_hit_tokens": 80_000},
        prices=prices,
        day="2026-11-05",
        model="deepseek-chat",
        project="Office",
    )
    ledger.record(
        "s2",
        {"prompt_tokens": 50_000, "completion_tokens": 10_000, "cache_hit_tokens": 0},
        prices=prices,
        day="2026-11-10",
        model="gemini-2.5-flash",
        project="Code",
    )

    ins = ledger.monthly_insights("2026-11", today=date(2026, 11, 15))
    assert ins["month"] == "2026-11"
    assert ins["has_prices"] is True
    assert ins["calls"] == 2
    assert ins["total_cost"] > 0.13
    assert ins["cache_saved"] > 0.07  # 80k * (1.0 - 0.1) / 1M = 0.072
    assert ins["cache_hit_pct"] == 53
    assert ins["projected_month_cost"] == round(ins["total_cost"] / 15 * 30, 4)
    assert len(ins["sparkline"]) == 15
    # gemini-2.5-flash had 0% cache ($0.070) vs deepseek-chat 80% cache ($0.068)
    assert [m["name"] for m in ins["by_model"]] == ["gemini-2.5-flash", "deepseek-chat"]
    assert [p["name"] for p in ins["by_project"]] == ["Code", "Office"]


def test_monthly_insights_without_prices_never_invents_rates(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    try:
        state.usage_ledger = UsageLedger(tmp_path / "usage.json")
        state.usage_ledger.record(
            "s1",
            {"prompt_tokens": 12_000, "completion_tokens": 3_000, "cache_hit_tokens": 6_000},
            prices=None,
            day="2026-11-20",
            model="local-ollama",
            project="Notes",
        )
        ins = state.usage_insights("2026-11")
        assert ins["has_prices"] is False
        assert ins["total_cost"] == 0.0
        assert ins["projected_month_cost"] is None
        assert ins["prompt_tokens"] == 12_000 and ins["cache_hit_pct"] == 50
    finally:
        state.close()
