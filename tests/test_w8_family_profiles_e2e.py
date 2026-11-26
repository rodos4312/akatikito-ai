"""W8 real-entry-point E2E wiring test for B10 Family Profiles inside AppState."""
from __future__ import annotations

import hashlib

from akatikito import memory
from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.config import Config
from akatikito.memory_store import MemoryStore
from akatikito.model import ScriptedModel
from akatikito.settings import Settings
from akatikito.usage_ledger import UsageLedger


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_w8_primary_profile_migration_is_byte_for_byte_exact(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mem_dir = data_dir / "memory"
    mem_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")
    monkeypatch.setattr(memory, "DIR", mem_dir)

    orig_cfg = Settings(provider="OpenAI", model="gpt-4o", daily_budget=2.5)
    orig_cfg.set_key("OpenAI", "sk-existing-owner-key-12345")
    orig_cfg.save(data_dir / "settings.json")
    MemoryStore(mem_dir / "memory.db").add("Υπάρχουσα μνήμη ιδιοκτήτη")
    before_settings_sha = _sha(data_dir / "settings.json")
    before_mem_sha = _sha(mem_dir / "memory.db")

    state = AppState()
    migrated = state.family_profile_action({"action": "init_primary", "pin": "4321"})
    assert not migrated.get("error"), migrated
    paths = state.active_profile_paths()
    assert paths is not None
    # Byte-for-byte preservation: primary profile points directly to existing data without lossy copy
    assert _sha(paths.settings_file) == before_settings_sha
    assert _sha(paths.memory_db) == before_mem_sha
    assert state.settings.key_for("OpenAI") == "sk-existing-owner-key-12345"
    state.close()


def test_w8_two_profiles_in_same_appstate_isolate_all_data_and_enforce_pin_backoff_and_child_limits(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mem_dir = data_dir / "memory"
    school = tmp_path / "School"
    accounting = tmp_path / "Accounting"
    for d in (mem_dir, school, accounting):
        d.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")
    monkeypatch.setattr(memory, "DIR", mem_dir)

    clock = {"t": 1000.0}
    state = AppState()
    state._family_clock = lambda: clock["t"]

    # Create primary admin profile (Parent) and restricted profile (Child)
    r_parent = state.family_profile_action({"action": "init_primary", "display_name": "Κύριο", "pin": "2468"})
    assert not r_parent.get("error"), r_parent
    state.settings.set_key("OpenAI", "sk-parent-secret-99999")
    state.settings.save(state._settings_path())
    MemoryStore(state.active_profile_paths().memory_db).add("Φορολογικά γονιού 12345")
    UsageLedger(state.active_profile_paths().usage_file).record(
        "parent-s1",
        {"prompt_tokens": 1000, "completion_tokens": 200, "cache_hit_tokens": 0},
        {"input": 1.0, "cached": 0.1, "output": 2.0},
    )

    r_child = state.family_profile_action(
        {
            "action": "create",
            "id": "child",
            "display_name": "Παιδί",
            "role": "restricted",
            "daily_budget": 0.20,
            "allowed_projects": [str(school)],
            "admin_pin": "2468",
        }
    )
    assert not r_child.get("error"), r_child

    # Switch to Child -> completely isolated settings, keys, memory, usage
    sw_child = state.family_profile_action({"action": "switch", "id": "child"})
    assert not sw_child.get("error"), sw_child
    assert state.settings.key_for("OpenAI") == ""
    assert state.settings.daily_budget == 0.20
    assert MemoryStore(state.active_profile_paths().memory_db).active() == []
    assert UsageLedger(state.active_profile_paths().usage_file).view("parent-s1")["day"] == {}

    # Child CANNOT enable Advanced features
    adv_denied = state.set_preferences({"advanced_features": ["git"]})
    assert adv_denied.get("error") and "προφίλ" in adv_denied["error"].lower()

    # Child CANNOT run commands or write outside allowed project
    state.agent = Akatikito(str(accounting), Config(model="m", api_key="k", checkpoints=False), model=ScriptedModel([]))
    assert state.check_profile_access(tool_name="run_command", project_path=school)[0] is False
    assert state.check_profile_access(tool_name="write_file", project_path=accounting)[0] is False
    assert state.check_profile_access(tool_name="write_file", project_path=school)[0] is True

    # Wrong PIN 5 times -> triggers lockout delay (retry_after > 0)
    for _ in range(5):
        bad = state.family_profile_action({"action": "switch", "id": "primary", "pin": "0000"})
        assert bad.get("error")
    locked = state.family_profile_action({"action": "switch", "id": "primary", "pin": "2468"})
    assert locked.get("error") and locked.get("retry_after", 0) > 0

    # After cooldown expires, correct PIN switches back to Parent and restores Parent data
    clock["t"] += locked["retry_after"] + 1.0
    sw_parent = state.family_profile_action({"action": "switch", "id": "primary", "pin": "2468"})
    assert not sw_parent.get("error"), sw_parent
    assert state.settings.key_for("OpenAI") == "sk-parent-secret-99999"
    assert any("Φορολογικά" in row["text"] for row in MemoryStore(state.active_profile_paths().memory_db).active())
    state.close()
