"""G3 real-entry-point tests: creating a profile requires primary admin PIN + 6-language UI labels."""
from __future__ import annotations

from akatikito import i18n, memory
from akatikito.app_api import AppState
from akatikito.memory_store import MemoryStore


def test_g3_new_profile_i18n_labels_present_in_all_6_languages():
    required = (
        "lbl_new_profile",
        "lbl_role_adult",
        "lbl_role_child",
        "btn_create_profile",
        "lbl_admin_pin",
    )
    for lang in i18n.SUPPORTED_LANGUAGES:
        bundle = i18n.TRANSLATIONS[lang]
        for key in required:
            assert bundle.get(key), f"Missing {key} in {lang}"


def test_g3_create_profile_refuses_without_primary_admin_pin_and_succeeds_with_pin(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mem_dir = data_dir / "memory"
    mem_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")
    monkeypatch.setattr(memory, "DIR", mem_dir)

    state = AppState()
    assert not state.family_profile_action({"action": "init_primary", "display_name": "Κύριο", "pin": "2468"}).get("error")
    MemoryStore(state.active_profile_paths().memory_db).add("Ιδιωτικό μυστικό κύριου προφίλ")

    # Attempt to create an adult profile WITHOUT primary PIN -> MUST be refused
    no_pin = state.family_profile_action(
        {"action": "create", "id": "rogue", "display_name": "Rogue", "role": "admin", "pin": "9999"}
    )
    assert no_pin.get("error") and "pin" in no_pin["error"].lower()
    assert all(p["id"] != "rogue" for p in state.family_profiles_view()["profiles"])

    # Attempt with WRONG primary PIN -> MUST be refused
    wrong_pin = state.family_profile_action(
        {"action": "create", "id": "rogue", "display_name": "Rogue", "role": "admin", "pin": "9999", "admin_pin": "0000"}
    )
    assert wrong_pin.get("error") and "pin" in wrong_pin["error"].lower()

    # With VALID primary PIN -> creates isolated new profile cleanly
    created = state.family_profile_action(
        {"action": "create", "id": "kid", "display_name": "Παιδί", "role": "restricted", "admin_pin": "2468"}
    )
    assert created.get("ok") is True and not created.get("error"), created
    assert any(p["id"] == "kid" for p in state.family_profiles_view()["profiles"])

    # Switch to new profile -> completely isolated from primary memory
    sw = state.family_profile_action({"action": "switch", "id": "kid"})
    assert sw.get("ok") is True
    assert MemoryStore(state.active_profile_paths().memory_db).active() == []

    # Child CANNOT create an adult profile without primary PIN
    child_escalation = state.family_profile_action(
        {"action": "create", "id": "adult2", "display_name": "Ενήλικας", "role": "admin", "pin": "5555"}
    )
    assert child_escalation.get("error") and "pin" in child_escalation["error"].lower()
    state.close()
