"""Phase 7.1 regression test: verify tests NEVER touch the real repo data/ directory."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_real_data_dir_is_isolated_from_settings_agenda_memory_and_appstate():
    from akatikito import agenda, doctor, memory, settings
    from akatikito.app_api import AppState

    real_data = (ROOT / "data").resolve()

    for p in (settings.DEFAULT_PATH, agenda.DEFAULT_PATH, doctor.DEFAULT_PATH, memory.DIR):
        resolved = Path(p).resolve()
        assert real_data not in resolved.parents and resolved != real_data, (
            f"Test path {resolved} points inside real data/ directory!"
        )

    # Saving settings and instantiating AppState must write ONLY to the isolated tmp_path
    st = settings.Settings(daily_budget=3.25)
    st.save()
    assert settings.DEFAULT_PATH.is_file()

    app = AppState()
    assert app.settings.daily_budget == 3.25
    assert real_data not in Path(app.mobile_bridge.store_path).resolve().parents
