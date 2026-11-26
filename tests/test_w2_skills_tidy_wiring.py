"""W2 real-entry-point wiring test for B9 Skill Curator inside AppState.learning_action."""
from __future__ import annotations

import os
import time

from akatikito.app_api import AppState


def _write_skill(folder, name: str, *, desc: str, triggers: str, tools: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = (
        f"---\nname: {name}\ndescription: {desc}\ncategory: office\n"
        f"triggers: {triggers}\ntools: {tools}\n---\n{body}\n"
    )
    (folder / f"{name}.md").write_text(text, encoding="utf-8")


def test_w2_learning_action_skills_tidy_proposes_and_archive_requires_approval(tmp_path):
    project = tmp_path / "proj"
    skills_dir = project / ".akatikito" / "skills"
    _write_skill(
        skills_dir,
        "photo-organizer",
        desc="Οργάνωση φωτογραφιών ανά ημερομηνία και φάκελο",
        triggers="φωτογραφίες, εικόνες",
        tools="list_files, organize_files",
        body="1. Ταξινόμησε φωτογραφίες ανά έτος και μήνα στον φάκελο.",
    )
    _write_skill(
        skills_dir,
        "image-sorter",
        desc="Οργάνωση φωτογραφιών και ταξινόμηση εικόνων ανά φάκελο",
        triggers="φωτογραφίες, εικόνες",
        tools="list_files",
        body="1. Ταξινόμησε φωτογραφίες ανά έτος και μήνα στον φάκελο.",
    )
    _write_skill(
        skills_dir,
        "old-macro",
        desc="Παλιά διαδικασία",
        triggers="legacy",
        tools="read_document",
        body="1. Παλιά βήματα.",
    )
    old_file = skills_dir / "old-macro.md"
    old_ts = time.time() - (120 * 86400)
    os.utime(old_file, (old_ts, old_ts))

    state = AppState()
    state.settings.project = str(project)
    root_str = str(state._files_root())

    res = state.learning_action({"action": "skills_tidy", "project": root_str})
    assert not res.get("error"), res
    assert res["propose_only"] is True
    assert res["counts"]["duplicate"] >= 1
    assert res["counts"]["stale"] >= 1
    # Read-only guarantee: nothing moved
    assert old_file.is_file()

    # Archive without explicit user_approved=True is refused
    denied = state.learning_action({"action": "skill_archive", "project": root_str, "name": "old-macro", "user_approved": False})
    assert denied.get("error")
    assert old_file.is_file()

    # Archive WITH explicit user_approved=True moves skill into archived/
    ok = state.learning_action({"action": "skill_archive", "project": root_str, "name": "old-macro", "user_approved": True})
    assert ok.get("ok") is True and not old_file.exists()
    assert (skills_dir / "archived" / "old-macro.md").is_file()
    state.close()
