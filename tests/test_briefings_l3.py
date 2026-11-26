"""L3 morning briefings tests: builtin skills, 1-click activation, next-day run with visible cost."""
from __future__ import annotations

from datetime import date, datetime

from akatikito.background_service import BackgroundService
from akatikito.briefings import (
    activate_briefing,
    deactivate_briefing,
    format_briefing_with_cost,
    list_presets,
)
from akatikito.settings import Settings
from akatikito.skills import Skills


def test_builtin_briefing_skills_are_in_catalog(tmp_path):
    names = {row["name"] for row in Skills(tmp_path).catalog()}
    assert {"morning-briefing", "weekly-project-summary", "bill-reminders"} <= names
    preset_ids = {p["id"] for p in list_presets()}
    assert preset_ids == {"morning-briefing", "weekly-project-summary", "bill-reminders"}


def test_activate_briefing_runs_next_day_with_visible_cost(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    prices = {"deepseek-chat": {"input": 0.5, "cached": 0.1, "output": 1.0}}
    cfg = Settings(
        provider="DeepSeek",
        model="deepseek-chat",
        project=str(project),
        background_mode=True,
        daily_budget=1.0,
        model_prices=prices,
    )
    delivered: list[tuple[str, str]] = []

    def fake_runner(job: dict, settings: Settings) -> dict:
        usage = {"prompt_tokens": 1000, "completion_tokens": 200, "cache_hit_tokens": 400}
        body = format_briefing_with_cost(job["title"], "• 2 εκκρεμότητες σήμερα.", usage, settings.model_prices["deepseek-chat"])
        return {"ok": True, "answer": body}

    svc = BackgroundService(
        data_dir=tmp_path / "data",
        settings=cfg,
        runner=fake_runner,
        notify=lambda title, msg: delivered.append((title, msg)),
    )

    activated = activate_briefing(
        project,
        svc.queue,
        "morning-briefing",
        start_date=date(2026, 11, 27),
        time_hhmm="09:00",
        delivery="telegram",
    )
    assert activated["ok"] is True
    assert activated["repeat"] == "daily"

    # Before 09:00 next day -> does not run
    early = svc.tick_once(moment=datetime(2026, 11, 27, 8, 55))
    assert early["ran"] is False

    # At 09:01 next day -> runs and includes visible cost
    run = svc.tick_once(moment=datetime(2026, 11, 27, 9, 1))
    assert run["ran"] is True and run["status"] == "completed"
    assert "Κόστος ενημέρωσης: $0.0005" in run["answer"]
    assert "1,200 tokens" in run["answer"]
    assert delivered and "Πρωινή ενημέρωση" in delivered[0][0]

    # 1-click deactivation removes task & cancels queue authorization
    assert deactivate_briefing(project, svc.queue, activated["task_id"]) is True
