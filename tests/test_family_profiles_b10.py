"""B10 Family Profiles tests: island isolation (settings/memory.db), PBKDF2 PIN escalation, child limits."""
from __future__ import annotations

import pytest

from akatikito.family_profiles import FamilyProfiles
from akatikito.memory_store import MemoryStore
from akatikito.settings import Settings


def test_family_profiles_isolate_settings_memory_and_enforce_pin_and_child_limits(tmp_path):
    school = tmp_path / "School"
    accounting = tmp_path / "Accounting"
    school.mkdir()
    accounting.mkdir()

    mgr = FamilyProfiles(tmp_path / "data")
    parent = mgr.create_profile("parent", "Γονιός", role="admin", pin="2468", daily_budget=5.0)
    child = mgr.create_profile(
        "child",
        "Παιδί",
        role="restricted",
        daily_budget=0.20,
        allowed_projects=[str(school)],
    )
    assert parent["has_pin"] is True and child["allow_commands"] is False and child["allow_mobile_bridge"] is False

    # Plaintext PIN must NEVER appear on disk
    assert "2468" not in mgr.registry_path.read_text(encoding="utf-8")

    # Isolated islands: Parent's Settings & SQLite memory.db are invisible to Child
    p_paths = mgr.paths_for("parent")
    c_paths = mgr.paths_for("child")
    assert p_paths.memory_db != c_paths.memory_db
    assert p_paths.settings_file != c_paths.settings_file

    p_cfg = Settings(provider="OpenAI", model="gpt-4o", daily_budget=5.0)
    p_cfg.set_key("OpenAI", "sk-parent-only-key-123456")
    p_cfg.save(p_paths.settings_file)
    c_cfg = Settings(provider="DeepSeek", model="deepseek-chat", daily_budget=0.20)
    c_cfg.save(c_paths.settings_file)
    assert Settings.load(c_paths.settings_file).key_for("OpenAI") == ""

    MemoryStore(p_paths.memory_db).add("Parent tax ID 999888777")
    MemoryStore(c_paths.memory_db).add("Math homework chapter 3")
    child_notes = [row["text"] for row in MemoryStore(c_paths.memory_db).active()]
    assert any("Math homework" in t for t in child_notes)
    assert not any("999888777" in t for t in child_notes)

    # Switching from parent -> child requires no PIN; switching back child -> parent requires valid PIN
    mgr.switch_profile("child")
    assert mgr.active_profile()["id"] == "child"
    with pytest.raises(PermissionError, match="PIN"):
        mgr.switch_profile("parent", pin="")
    with pytest.raises(PermissionError, match="PIN"):
        mgr.switch_profile("parent", pin="0000")
    assert mgr.switch_profile("parent", pin="2468").profile_id == "parent"

    # Restricted child access boundaries
    assert mgr.check_access("child", tool_name="run_command")[0] is False
    assert mgr.check_access("child", mobile_bridge=True)[0] is False
    assert mgr.check_access("child", project_path=accounting)[0] is False
    assert mgr.check_access("child", project_path=school / "essay.md")[0] is True
    assert mgr.check_access("parent", tool_name="run_command", project_path=accounting, mobile_bridge=True)[0] is True
