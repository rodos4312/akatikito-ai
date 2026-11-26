"""Tests for original smart_read.py: wide full-line reading, exact continuation & similar_files."""
from __future__ import annotations

from pathlib import Path

from akatikito.smart_read import render_wide_read, suggest_similar_files
from akatikito.tools import dispatch
from akatikito.workspace import Workspace


def test_wide_read_returns_1200_line_game_in_single_call(tmp_path: Path):
    game = tmp_path / "courier_v1.html"
    game.write_text("\n".join(f"const row_{i} = {i}; // game code" for i in range(1, 1201)), encoding="utf-8")
    ws = Workspace(tmp_path)

    out = dispatch(ws, "read_file", {"path": "courier_v1.html", "full": True})
    assert "γραμμές 1-1200 από 1200" in out
    assert "const row_1 = 1;" in out
    assert "const row_1200 = 1200;" in out
    assert "Περικόπηκε" not in out


def test_wide_read_trims_cleanly_at_complete_line_boundary_with_exact_next_start():
    lines = [f"payload line {i:03d}: " + ("A" * 180) for i in range(1, 51)]
    out = render_wide_read("big.js", lines, start=1, end=50, max_lines=50, max_chars=2500)
    assert "Περικόπηκε καθαρά σε πλήρη γραμμή" in out
    # Extract the last shown line number from header "big.js — γραμμές 1-N από 50:"
    head = out.splitlines()[0]
    last_shown = int(head.split("γραμμές 1-")[1].split(" από")[0])
    assert 1 <= last_shown < 50
    assert f"start={last_shown + 1}" in out
    # Last code line before note must be COMPLETE (ends with full 'A' run, never chopped mid-line)
    code_lines = [ln for ln in out.splitlines()[1:] if not ln.startswith("[Περικόπηκε")]
    assert code_lines[-1].endswith("A" * 180)


def test_missing_file_suggests_closest_workspace_files(tmp_path: Path):
    (tmp_path / "courier_v1.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "_courier_test.js").write_text("console.log(1);", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    ws = Workspace(tmp_path)

    hits = suggest_similar_files(ws, "corier_v1.html")
    assert hits and hits[0] == "courier_v1.html"

    err = dispatch(ws, "read_file", {"path": "corier_v1.html"})
    assert "ΣΦΑΛΜΑ" in err
    assert "Μήπως εννοούσες:" in err
    assert "courier_v1.html" in err
