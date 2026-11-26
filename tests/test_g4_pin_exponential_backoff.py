"""G4 real-entry-point test: exponential PIN lockout backoff (30s, 60s, 120s ... max 3600s) + reset on valid PIN."""
from __future__ import annotations

import pytest

from akatikito.app_api import AppState


def _trigger_lockout(state: AppState, clock: dict) -> float:
    for _ in range(5):
        bad = state.family_profile_action({"action": "switch", "id": "primary", "pin": "0000"})
        assert bad.get("error")
    locked = state.family_profile_action({"action": "switch", "id": "primary", "pin": "2468"})
    assert locked.get("error") and locked.get("retry_after", 0) > 0
    return float(locked["retry_after"])


def test_g4_pin_lockout_doubles_30_60_120_caps_at_one_hour_and_resets_after_valid_pin(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")

    clock = {"t": 1000.0}
    state = AppState()
    state._family_clock = lambda: clock["t"]

    assert not state.family_profile_action({"action": "init_primary", "display_name": "Κύριο", "pin": "2468"}).get("error")
    assert not state.family_profile_action(
        {"action": "create", "id": "child", "display_name": "Παιδί", "role": "restricted", "admin_pin": "2468"}
    ).get("error")
    assert not state.family_profile_action({"action": "switch", "id": "child"}).get("error")

    # 3 consecutive lockouts -> 30s, 60s, 120s
    observed: list[float] = []
    for expected in (30.0, 60.0, 120.0):
        delay = _trigger_lockout(state, clock)
        observed.append(delay)
        assert delay == pytest.approx(expected, abs=0.5)
        clock["t"] += delay + 0.1

    # Continue escalating until cap -> never exceeds 3600s (1 hour)
    for expected in (240.0, 480.0, 960.0, 1920.0, 3600.0, 3600.0):
        delay = _trigger_lockout(state, clock)
        assert delay == pytest.approx(expected, abs=0.5)
        clock["t"] += delay + 0.1

    # Valid PIN succeeds and RESETS backoff back to 30s
    ok = state.family_profile_action({"action": "switch", "id": "primary", "pin": "2468"})
    assert ok.get("ok") is True and not ok.get("error"), ok

    assert not state.family_profile_action({"action": "switch", "id": "child"}).get("error")
    after_reset = _trigger_lockout(state, clock)
    assert after_reset == pytest.approx(30.0, abs=0.5)
    state.close()
