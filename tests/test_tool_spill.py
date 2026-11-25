"""Tests for spilling oversized tool outputs to .agent/outputs/ (PRO-PLAN B8)."""
from __future__ import annotations

from akatikito.tool_spill import spill_large_output


def test_small_output_and_read_file_are_never_spilled(tmp_path):
    small = "line 1\nline 2"
    assert spill_large_output(tmp_path, "run_command", small) == small
    big_read = "\n".join(f"line {i}" for i in range(500))
    assert spill_large_output(tmp_path, "read_file", big_read) == big_read
    assert not (tmp_path / ".agent" / "outputs").exists()


def test_large_run_command_spills_redacted_file_and_keeps_returncode_prefix(tmp_path):
    lines = ["returncode=0"] + [f"row {i:04d} secret=sk-proj-1234567890abcdef1234567890" for i in range(450)]
    raw = "\n".join(lines)

    summary = spill_large_output(tmp_path, "run_command", raw)
    assert summary.startswith("returncode=0\n")
    assert ".agent/outputs/run_command-" in summary
    assert "Πρώτες 40 γραμμές:" in summary
    assert "Τελευταίες 20 γραμμές:" in summary
    assert "sk-proj-1234567890abcdef1234567890" not in summary
    assert len(summary) < len(raw) // 2

    files = list((tmp_path / ".agent" / "outputs").glob("run_command-*.txt"))
    assert len(files) == 1
    saved = files[0].read_text(encoding="utf-8")
    assert "sk-proj-1234567890abcdef1234567890" not in saved
    assert "row 0449" in saved
