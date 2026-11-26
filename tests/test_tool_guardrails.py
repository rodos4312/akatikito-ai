"""Tests for MEU-2: Multi-step tool cycle detection, result deduplication & run_command burst guard."""
from __future__ import annotations

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.tool_guardrails import IDENTICAL_RESULT_STUB_MIN_CHARS, ToolLoopGuardrails


def test_identical_large_result_is_deduplicated_but_errors_and_small_stay_full():
    guard = ToolLoopGuardrails()
    big = "line\n" * 200
    assert len(big) > IDENTICAL_RESULT_STUB_MIN_CHARS

    first = guard.observe("read_file", {"path": "a.py"}, big)
    assert not first.deduplicated and first.content == big

    second = guard.observe("read_file", {"path": "a.py"}, big)
    assert second.deduplicated
    assert "παραλείφθηκαν" in second.content
    assert len(second.content) < 220

    # Errors are never deduplicated (LLM needs the full error trace)
    err = "ΣΦΑΛΜΑ: " + ("trace " * 150)
    assert not guard.observe("run_command", {"command": "pytest"}, err).deduplicated
    assert not guard.observe("run_command", {"command": "pytest"}, err).deduplicated

    # Small outputs (<512 chars) stay intact
    small = "ok small"
    assert not guard.observe("list_files", {}, small).deduplicated
    assert not guard.observe("list_files", {}, small).deduplicated


def test_multi_step_cycle_detection_catches_ab_ab_ab_loop():
    guard = ToolLoopGuardrails()
    outcomes = []
    for _ in range(3):
        outcomes.append(guard.observe("read_file", {"path": "x.py"}, "content x"))
        outcomes.append(guard.observe("search_in_file", {"path": "x.py", "pattern": "foo"}, "hit 1"))
    assert outcomes[-1].kind == "cycle_warning"
    assert "read_file → search_in_file" in outcomes[-1].notice

    # Warning fires once per cycle signature (doesn't spam)
    outcomes.append(guard.observe("read_file", {"path": "x.py"}, "content x"))
    outcomes.append(guard.observe("search_in_file", {"path": "x.py", "pattern": "foo"}, "hit 1"))
    assert outcomes[-1].notice == ""


def test_run_command_burst_warning_and_agent_dedup_integration(tmp_path):
    big_file = tmp_path / "big.txt"
    big_file.write_text("Αυτό είναι μεγάλο περιεχόμενο.\n" * 40, encoding="utf-8")

    guard = ToolLoopGuardrails(run_cmd_warn_at=3)
    assert guard.observe("run_command", {"command": "c1"}, "returncode=0\n1").notice == ""
    assert guard.observe("run_command", {"command": "c2"}, "returncode=0\n2").notice == ""
    third = guard.observe("run_command", {"command": "c3"}, "returncode=0\n3")
    assert third.kind == "run_command_burst"
    assert "ΣΥΜΒΟΥΛΗ ΑΠΟΔΟΣΗΣ" in third.notice

    # End-to-end integration: second identical read_file in same turn is stubbed in agent.messages
    class TwoReadsModel:
        def __init__(self):
            self.n = 0

        def chat(self, messages, tools=None, cache_key=None):
            self.n += 1
            if self.n <= 2:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": f"r{self.n}", "name": "read_file", "arguments": '{"path": "big.txt"}'}
                    ],
                }, {"prompt_tokens": 20, "completion_tokens": 5}
            return {"content": "Ολοκληρώθηκε.", "tool_calls": []}, {"prompt_tokens": 20, "completion_tokens": 5}

    agent = Akatikito(str(tmp_path), Config(auto_mode=False, max_steps=6), TwoReadsModel())
    assert agent.chat("Διάβασε το big.txt") == "Ολοκληρώθηκε."
    tool_msgs = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert len(tool_msgs[0]) > IDENTICAL_RESULT_STUB_MIN_CHARS
    assert "παραλείφθηκαν" in tool_msgs[1]
