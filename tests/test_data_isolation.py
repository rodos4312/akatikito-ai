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


def test_requirements_have_no_unbounded_lower_pins():
    """Phase 7.2: every >= constraint in requirements*.txt must also specify an upper bound (<)."""
    for req_file in sorted(ROOT.glob("requirements*.txt")):
        for lineno, raw in enumerate(req_file.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ">=" in line:
                assert "<" in line, f"{req_file.name}:{lineno} has unbounded '>=' without '<': {line}"
