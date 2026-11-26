"""L2 24/7 headless background service tests: disabled-by-default, 09:00 job, budget cap, autostart."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from akatikito.agenda import Agenda
from akatikito.background_service import (
    DEFAULT_BACKGROUND_DAILY_BUDGET,
    BackgroundService,
    default_autostart_command,
    is_windows_autostart_enabled,
    set_windows_autostart,
)
from akatikito.settings import Settings


def _make_settings(project: Path, *, enabled: bool = True, budget: float = 0.50) -> Settings:
    return Settings(
        provider="DeepSeek",
        model="deepseek-chat",
        project=str(project),
        background_mode=enabled,
        daily_budget=budget,
        model_prices={"deepseek-chat": {"input": 0.5, "cached": 0.1, "output": 1.0}},
    )


def test_background_service_is_closed_by_default(tmp_path):
    cfg = Settings()
    assert cfg.background_mode is False
    assert cfg.background_autostart is False
    svc = BackgroundService(data_dir=tmp_path / "data", settings=cfg)
    assert svc.start() is False
    assert svc.tick_once()["reason"] == "disabled"
    assert svc.effective_budget() == DEFAULT_BACKGROUND_DAILY_BUDGET


def test_daily_0900_authorized_job_runs_headlessly_and_notifies(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agenda = Agenda(project / ".agent" / "agenda.json")
    task = agenda.add(
        "Πρωινή ενημέρωση",
        "2026-11-26",
        "09:00",
        note="Φτιάξε πρωινή σύνοψη",
        repeat="daily",
        auto_run=True,
        auto_mode="assistant",
    )

    notifications: list[tuple[str, str]] = []
    ran_jobs: list[dict] = []

    def fake_runner(job: dict, _cfg: Settings) -> dict:
        ran_jobs.append(job)
        return {"ok": True, "answer": "Καλημέρα! 3 εκκρεμότητες σήμερα."}

    svc = BackgroundService(
        data_dir=tmp_path / "data",
        settings=_make_settings(project, enabled=True, budget=1.0),
        runner=fake_runner,
        notify=lambda title, msg: notifications.append((title, msg)),
    )
    svc.queue.authorize_task(project, task)

    result = svc.tick_once(moment=datetime(2026, 11, 26, 9, 1))
    assert result["ran"] is True
    assert result["status"] == "completed"
    assert len(ran_jobs) == 1
    assert ran_jobs[0]["prompt"] == "Φτιάξε πρωινή σύνοψη"
    assert notifications and "Πρωινή ενημέρωση" in notifications[0][0]
    assert svc.status()["runs"] == 1


def test_unauthorized_task_never_executes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agenda = Agenda(project / ".agent" / "agenda.json")
    agenda.add("Μη εγκεκριμένη", "2026-11-26", "09:00", auto_run=True)

    called: list[bool] = []
    svc = BackgroundService(
        data_dir=tmp_path / "data",
        settings=_make_settings(project, enabled=True, budget=1.0),
        runner=lambda j, c: called.append(True) or {"ok": True, "answer": "x"},
        notify=lambda t, m: None,
    )
    res = svc.tick_once(moment=datetime(2026, 11, 26, 9, 5))
    assert res["ran"] is False
    assert called == []
    assert any("έγκριση" in err for err in res["collect_errors"])


def test_daily_budget_cap_halts_background_service_before_model_call(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agenda = Agenda(project / ".agent" / "agenda.json")
    task = agenda.add("Έλεγχος", "2026-11-26", "09:00", auto_run=True)

    called: list[bool] = []
    alerts: list[tuple[str, str]] = []
    cfg = _make_settings(project, enabled=True, budget=0.20)
    svc = BackgroundService(
        data_dir=tmp_path / "data",
        settings=cfg,
        runner=lambda j, c: called.append(True) or {"ok": True, "answer": "x"},
        notify=lambda t, m: alerts.append((t, m)),
    )
    svc.queue.authorize_task(project, task)
    # Record usage exceeding the 0.20 daily cap
    svc.ledger.record(
        "prev-session",
        {"prompt_tokens": 500_000, "completion_tokens": 50_000, "cache_hit_tokens": 0},
        cfg.model_prices["deepseek-chat"],
    )

    res = svc.tick_once(moment=datetime(2026, 11, 26, 9, 5))
    assert res["ran"] is False
    assert res["reason"] == "budget"
    assert called == []
    assert svc.status()["budget_halted"] is True
    assert alerts and "Όριο κόστους" in alerts[0][0]


def test_one_click_stop_and_windows_autostart_registry_contract(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    svc = BackgroundService(
        data_dir=tmp_path / "data",
        settings=_make_settings(project, enabled=True, budget=1.0),
        runner=lambda j, c: {"ok": True, "answer": "ok"},
        notify=lambda t, m: None,
        poll_interval=0.05,
    )
    assert svc.start() is True
    assert svc.running() is True
    svc.stop(timeout=2.0)
    assert svc.running() is False

    class FakeReg:
        def __init__(self):
            self.values: dict[str, str] = {}

        def read(self, name: str):
            return self.values.get(name)

        def write(self, name: str, cmd: str):
            self.values[name] = cmd

        def delete(self, name: str):
            self.values.pop(name, None)

    reg = FakeReg()
    assert is_windows_autostart_enabled(backend=reg) is False
    cmd = default_autostart_command(python_exe="C:/Python/python.exe", app_root=tmp_path)
    assert "-m akatikito.background_service" in cmd
    assert set_windows_autostart(True, cmd, backend=reg) is True
    assert is_windows_autostart_enabled(backend=reg) is True
    assert set_windows_autostart(False, backend=reg) is False
    assert is_windows_autostart_enabled(backend=reg) is False

    # Settings persistence round-trip
    saved_path = tmp_path / "settings.json"
    s = Settings(background_mode=True, background_autostart=True)
    s.save(saved_path)
    reloaded = Settings.load(saved_path)
    assert reloaded.background_mode is True and reloaded.background_autostart is True
