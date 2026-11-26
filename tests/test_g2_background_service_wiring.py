"""G2 real-entry-point wiring tests for 24/7 background_service setting in AppState."""
from __future__ import annotations

from akatikito import i18n
from akatikito.app_api import AppState
from akatikito.background_service import RUN_REG_VALUE
from akatikito.settings import Settings


class _FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.values.get(name)

    def write(self, name: str, command: str) -> None:
        self.values[name] = command

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        self.terminated = True
        return 0


def test_g2_background_service_closed_by_default_and_documented_in_all_6_languages():
    cfg = Settings()
    assert cfg.background_service is False
    for lang in i18n.SUPPORTED_LANGUAGES:
        bundle = i18n.TRANSLATIONS[lang]
        assert bundle.get("lbl_background_service"), f"Missing lbl_background_service in {lang}"
        assert bundle.get("lbl_background_service_hint"), f"Missing lbl_background_service_hint in {lang}"


def test_g2_background_service_budget_guard_autostart_single_instance_and_stop(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")

    fake_reg = _FakeRegistry()
    spawned: list[_FakeProc] = []

    def fake_spawner(_cmd: list[str]) -> _FakeProc:
        proc = _FakeProc(pid=5000 + len(spawned))
        spawned.append(proc)
        return proc

    state = AppState()
    state._bg_registry_backend = fake_reg
    state._bg_spawner = fake_spawner
    state._bg_pid_alive = lambda pid: any(p.pid == pid and not p.terminated for p in spawned)

    # 3. Budget == 0 -> refused with clear Greek message, no autostart, no process
    denied = state.set_preferences({"background_service": True})
    assert denied.get("error") and "όριο" in denied["error"].lower()
    assert RUN_REG_VALUE not in fake_reg.values
    assert spawned == []
    assert state.settings.background_service is False

    # 1. With daily_budget > 0 -> writes autostart and starts single background process
    enabled = state.set_preferences({"daily_budget": 1.5, "background_service": True})
    assert not enabled.get("error"), enabled
    assert state.settings.background_service is True
    assert RUN_REG_VALUE in fake_reg.values
    assert len(spawned) == 1
    assert enabled["background_service_status"]["running"] is True

    # 2. Second activation -> NO second service process spawned (single-instance lock/pid)
    again = state.set_preferences({"background_service": True})
    assert not again.get("error"), again
    assert len(spawned) == 1
    assert again["background_service_status"]["running"] is True

    # 4. Turning off -> deletes autostart and terminates the running service
    disabled = state.set_preferences({"background_service": False})
    assert not disabled.get("error"), disabled
    assert state.settings.background_service is False
    assert RUN_REG_VALUE not in fake_reg.values
    assert spawned[0].terminated is True
    assert disabled["background_service_status"]["running"] is False
    state.close()
