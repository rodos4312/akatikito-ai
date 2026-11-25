"""MEMORY-PLAN M6: the memory screen corrects and forgets memories where they live, without resurrection."""
import json

import pytest

from akatikito import memory, memory_central, memory_screen
from akatikito.memory_import import project_scope
from akatikito.memory_store import MemoryRefused
from akatikito.profile import Profile
from akatikito.workspace import AGENT_DIR, MEMORY_FILE


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "shop"
    (root / AGENT_DIR).mkdir(parents=True)
    (root / AGENT_DIR / MEMORY_FILE).write_bytes("- Η βάση είναι PostgreSQL 16.\r\n".encode("utf-8"))
    return root


@pytest.fixture
def store(project):
    (memory.DIR / "MEMORY.md").write_bytes(
        "# Σημειώσεις\r\n- Τα commits στα αγγλικά.\r\n- Ο σκύλος λέγεται Ρεξ.\r\n".encode("utf-8"))
    with memory_central.opened() as s:
        memory_central.refresh(s, project)
        yield s


def find(s, text):
    return next(r for r in s.active(limit=200) if r["text"] == text)


def test_view_lists_global_and_project_and_searches_across_spellings(store, project):
    view = memory_screen.view(store, project)
    assert {r["text"] for r in view["rows"]} == {"Τα commits στα αγγλικά.", "Ο σκύλος λέγεται Ρεξ.",
                                                  "Η βάση είναι PostgreSQL 16."}
    assert {r["scope"] for r in memory_screen.view(store, project, scope="project")["rows"]} == {"project"}
    found = memory_screen.view(store, project, query="skylos")["rows"]
    assert found[0]["text"] == "Ο σκύλος λέγεται Ρεξ." and found[0]["matched"]


def test_editing_a_file_note_rewrites_only_its_line(store, project):
    memory_screen.edit(store, project, find(store, "Ο σκύλος λέγεται Ρεξ.")["id"], "Ο σκύλος λέγεται Μαξ.")
    raw = (memory.DIR / "MEMORY.md").read_bytes()
    assert raw == "# Σημειώσεις\r\n- Τα commits στα αγγλικά.\r\n- Ο σκύλος λέγεται Μαξ.\r\n".encode("utf-8")
    texts = {r["text"] for r in store.active(limit=50)}
    assert "Ο σκύλος λέγεται Μαξ." in texts and "Ο σκύλος λέγεται Ρεξ." not in texts


def test_a_forgotten_file_note_does_not_come_back(store, project):
    memory_screen.forget(store, project, find(store, "Τα commits στα αγγλικά.")["id"])
    with (memory.DIR / "MEMORY.md").open("a", encoding="utf-8") as f:
        f.write("- Νέα σημείωση.\n")
    memory_central.refresh(store, project)
    texts = {r["text"] for r in store.active(limit=50)}
    assert "Τα commits στα αγγλικά." not in texts and "Νέα σημείωση." in texts
    assert "αγγλικά" not in (memory.DIR / "MEMORY.md").read_text(encoding="utf-8")


def test_project_notes_change_in_the_project_file(store, project):
    memory_screen.edit(store, project, find(store, "Η βάση είναι PostgreSQL 16.")["id"], "Η βάση είναι PostgreSQL 17.")
    assert "PostgreSQL 17" in (project / AGENT_DIR / MEMORY_FILE).read_text(encoding="utf-8")
    other = store.add("Σημείωση άλλου project.", scope=project_scope(project.parent / "other"), source="MEMORY.md")
    with pytest.raises(MemoryRefused, match="άλλο project"):
        memory_screen.edit(store, project, other, "αλλαγή")


def _profile_with(topic, text):
    profile = Profile()
    profile.propose(topic, text, kind="fact", status="confirmed")
    pending = profile.listing()["proposals"][0]
    profile.answer(pending["id"], True, expected=pending["review_hash"])


def test_profile_items_are_revised_and_erased_in_the_profile(store, project):
    _profile_with("Διεύθυνση", "Μένει στην Οδό Πρώτη 1.")
    memory_central.refresh(store, project)
    memory_screen.edit(store, project, find(store, "Μένει στην Οδό Πρώτη 1.")["id"], "Μένει στην Οδό Δεύτερη 2.")
    assert [e["text"] for e in Profile().listing()["entries"]] == ["Μένει στην Οδό Δεύτερη 2."]
    row = find(store, "Μένει στην Οδό Δεύτερη 2.")
    memory_screen.forget(store, project, row["id"], hard=True)
    assert Profile().listing()["entries"] == []
    raw = (memory.DIR / "profile.json").read_text(encoding="utf-8")
    assert "Δεύτερη" not in raw  # gone from the profile's own revision history too
    assert "Δεύτερη" not in json.dumps(store.history(row["id"]), ensure_ascii=False)


def test_memories_born_in_the_store_change_in_place(store, project):
    own = store.add("Σημείωση από την οθόνη.", source="learned")
    new = memory_screen.edit(store, project, own, "Διορθωμένη σημείωση.")["id"]
    assert store.get(own)["superseded_by"] == new
    assert [h["action"] for h in memory_screen.history(store, new)] == ["Προστέθηκε", "Διορθώθηκε"]
    memory_screen.feedback(store, new, helpful=True)
    memory_screen.forget(store, project, new)
    assert store.get(new)["forgotten"] == 1 and store.get(new)["helpful"] == 1


def test_suggestions_wait_and_an_approved_revision_replaces_its_memory(store, project):
    old = store.add("Ο πάροχος είναι το DeepSeek.", source="learned")
    first = store.add_proposal({"text": "Ο πάροχος είναι το OpenRouter.", "kind": "fact", "status": "confirmed",
                                "scope": "global", "source": "learned", "replaces": old, "state": "pending"})
    second = store.add_proposal({"text": "Του αρέσει το τσάι.", "kind": "fact", "status": "confirmed",
                                 "scope": "global", "source": "learned", "state": "pending"})
    assert [p["id"] for p in memory_screen.view(store, project)["proposals"]] == [first, second]
    memory_screen.answer_proposal(store, first, True)
    memory_screen.answer_proposal(store, second, False)
    texts = {r["text"] for r in store.active(limit=50)}
    assert "Ο πάροχος είναι το OpenRouter." in texts and "Ο πάροχος είναι το DeepSeek." not in texts
    assert "Του αρέσει το τσάι." not in texts and memory_screen.view(store, project)["proposals"] == []
    with pytest.raises(MemoryRefused):
        memory_screen.answer_proposal(store, first, True)


def test_the_app_api_serves_the_screen(project):
    from akatikito.app_api import AppState
    state = AppState()
    state.settings.project = str(project)
    view = state.memory_view({"query": ""})
    assert view["project"] == str(project.resolve()) and view["total"] >= 1
    assert "error" in state.memory_action({"project": "elsewhere", "action": "forget", "id": 1})
