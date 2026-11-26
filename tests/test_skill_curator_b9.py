"""B9 Skill Curator tests: duplicate, stale, and conflicting skills detected read-only."""
from __future__ import annotations

import os
import time

import pytest

from akatikito import memory
from akatikito.skill_curator import archive_skill, curate_skills


def _write_skill(folder, name: str, *, desc: str, triggers: str, tools: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = (
        f"---\nname: {name}\ndescription: {desc}\ncategory: office\n"
        f"triggers: {triggers}\ntools: {tools}\n---\n{body}\n"
    )
    (folder / f"{name}.md").write_text(text, encoding="utf-8")


def test_skill_curator_detects_duplicates_conflicts_and_stale_without_mutating(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DIR", tmp_path / "memory")
    project = tmp_path / "project"
    skills_dir = project / ".akatikito" / "skills"

    # Two near-duplicate photo skills
    _write_skill(
        skills_dir,
        "photo-organizer",
        desc="Οργάνωση φωτογραφιών ανά ημερομηνία και φάκελο",
        triggers="φωτογραφίες, οργάνωση εικόνων",
        tools="list_files, organize_files",
        body="1. Ταξινόμησε φωτογραφίες ανά έτος και μήνα στον φάκελο.",
    )
    _write_skill(
        skills_dir,
        "image-sorter",
        desc="Οργάνωση φωτογραφιών και ταξινόμηση εικόνων ανά φάκελο",
        triggers="φωτογραφίες, ταξινόμηση εικόνων",
        tools="list_files",
        body="1. Ταξινόμησε φωτογραφίες ανά έτος και μήνα στον φάκελο.",
    )
    # One stale unused skill (120 days old)
    _write_skill(
        skills_dir,
        "excel-2019-legacy",
        desc="Παλιά μακροεντολή για Excel 2019",
        triggers="excel2019",
        tools="read_document",
        body="1. Άνοιξε παλιό φύλλο.",
    )
    stale_path = skills_dir / "excel-2019-legacy.md"
    old_ts = time.time() - (120 * 86400)
    os.utime(stale_path, (old_ts, old_ts))

    before_files = sorted(p.name for p in skills_dir.glob("*.md"))
    report = curate_skills(project, stale_days=90)

    assert report["propose_only"] is True
    assert "επόμενη συζήτηση" in report["cache_note"]
    assert report["counts"]["duplicate"] >= 1
    assert report["counts"]["conflict"] >= 1
    assert report["counts"]["stale"] >= 1

    # Read-only guarantee: NO skill file was modified or deleted
    assert sorted(p.name for p in skills_dir.glob("*.md")) == before_files

    # Archiving requires explicit approval
    with pytest.raises(PermissionError):
        archive_skill(stale_path, user_approved=False)
    archived = archive_skill(stale_path, user_approved=True)
    assert archived.is_file() and archived.parent.name == "archived"
    assert not stale_path.exists()
