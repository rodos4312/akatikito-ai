"""L5 Obsidian/Markdown notes vault tests: 1,000-note <1s search, strict confinement, previewed edits."""
from __future__ import annotations

import time

import pytest

from akatikito.notes_vault import NotesVault


def test_search_1000_markdown_notes_under_one_second(tmp_path):
    vault_dir = tmp_path / "obsidian"
    vault_dir.mkdir()
    for i in range(1000):
        sub = vault_dir / f"folder_{i % 10}"
        sub.mkdir(exist_ok=True)
        if i == 777:
            body = "# Κουζίνα & Συνταγές\nΗ γιαγιά έβαζε θυμάρι στο παστίτσιο. [[Ελληνική Κουζίνα]] #συνταγές\n"
        else:
            body = f"# Note {i}\nRoutine archive entry number {i} for local benchmark testing.\n"
        (sub / f"note_{i:04d}.md").write_text(body, encoding="utf-8")

    vault = NotesVault(vault_dir)
    stats = vault.sync_index()
    assert stats["total"] == 1000 and stats["indexed"] == 1000

    # Timed query across all 1,000 indexed notes must complete in < 1.0 second
    t0 = time.perf_counter()
    hits = vault.search("παστίτσιο θυμάρι", limit=5, sync=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"Search took too long: {elapsed:.3f}s"
    assert hits and hits[0]["path"].endswith("note_0777.md")
    assert "συνταγές" in hits[0]["tags"]
    assert "Ελληνική Κουζίνα" in hits[0]["links"]


def test_create_note_confined_to_vault_and_edits_require_preview_hash(tmp_path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    vault = NotesVault(vault_dir)

    # Escape attempts are blocked
    for bad in ("../escape.md", "/etc/passwd.md", "C:/Windows/evil.md", "sub/../../escape.md"):
        with pytest.raises(ValueError):
            vault.create_note(bad, "should fail")

    # Valid note creation inside vault
    created = vault.create_note("ideas/project-x", "# Ιδέα X\nΠρώτη έκδοση.", tags=("work", "ideas"))
    assert created["ok"] is True and created["path"] == "ideas/project-x.md"
    assert (vault_dir / "ideas" / "project-x.md").is_file()

    # Overwriting via create_note is refused
    with pytest.raises(FileExistsError):
        vault.create_note("ideas/project-x", "overwrite")

    # Editing without matching preview hash is refused
    with pytest.raises(PermissionError):
        vault.apply_edit("ideas/project-x", "# Ιδέα X\nΔεύτερη έκδοση.", approved_hash="wrong-hash")

    # Preview diff -> approve exact hash -> atomic edit succeeds
    preview = vault.preview_edit("ideas/project-x", "# Ιδέα X\nΔεύτερη έκδοση.")
    assert preview["changed"] is True
    assert "-Πρώτη έκδοση." in preview["diff"]
    assert "+Δεύτερη έκδοση." in preview["diff"]
    applied = vault.apply_edit("ideas/project-x", "# Ιδέα X\nΔεύτερη έκδοση.", approved_hash=preview["preview_hash"])
    assert applied["ok"] is True
    assert "Δεύτερη έκδοση." in (vault_dir / "ideas" / "project-x.md").read_text(encoding="utf-8")
