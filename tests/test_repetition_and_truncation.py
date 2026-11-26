"""Tests for MEU-4: Repetition Guard & seamless pure-text length continuation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.repetition_guard import (
    REPETITION_ABORT_MESSAGE,
    is_repetition_dominated,
    stitch_continuation,
)
from akatikito.streaming import StreamAssemblyError, StreamReply


def _chunk(text="", finish=None, calls=None):
    delta = SimpleNamespace(content=text or None, tool_calls=calls, reasoning_content=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=None)


def test_repetition_guard_detects_loops_without_false_positives():
    # Degenerate repeated-line loop
    loop_lines = ("Αυτή είναι μια επαναλαμβανόμενη πρόταση χωρίς τέλος.\n") * 25
    assert is_repetition_dominated(loop_lines) is True

    # Degenerate inline phrase loop (no newlines)
    loop_phrase = ("The courier delivered the package to the exact same station again and again. ") * 18
    assert is_repetition_dominated(loop_phrase) is True

    # Normal distinct Greek / HTML / table rows must NOT trip
    normal = "\n".join(f"<tr><td>Περιοχή #{i}</td><td>Έσοδα {i * 125}€</td><td>Πληθυσμός {i * 410}</td></tr>" for i in range(1, 30))
    assert is_repetition_dominated(normal) is False
    assert is_repetition_dominated("Μικρή απάντηση.") is False


def test_stitch_continuation_deduplicates_seam_overlap():
    assert stitch_continuation(["Μέρος πρώτο με τέλος αβγδ", "τέλος αβγδ και συνέχεια."]) == "Μέρος πρώτο με τέλος αβγδ και συνέχεια."
    assert stitch_continuation(["Πρώτο κομμάτι. ", "Δεύτερο κομμάτι."]) == "Πρώτο κομμάτι. Δεύτερο κομμάτι."


def test_stream_reply_marks_pure_text_length_truncation_but_still_rejects_broken_tools():
    sr = StreamReply()
    sr.feed(_chunk(text="Αρχή μεγάλης απάντησης...", finish="length"))
    reply = sr.reply()
    assert reply["content"] == "Αρχή μεγάλης απάντησης..."
    assert reply["truncated_text"] is True
    assert reply["tool_calls"] == []

    # Truncated tool call MUST still raise StreamAssemblyError
    sr2 = StreamReply()
    call_part = SimpleNamespace(index=0, id="c1", type="function", function=SimpleNamespace(name="write_file", arguments='{"path":'))
    sr2.feed(_chunk(calls=[call_part], finish="length"))
    with pytest.raises(StreamAssemblyError):
        sr2.reply()


def test_agent_seamlessly_stitches_truncated_text_and_aborts_repetition_loop(tmp_path):
    class TwoPartModel:
        def __init__(self):
            self.n = 0

        def chat(self, messages, tools=None, cache_key=None):
            self.n += 1
            if self.n == 1:
                return {
                    "content": "Μέρος 1 της αναλυτικής εξήγησης — ",
                    "tool_calls": [],
                    "truncated_text": True,
                }, {"prompt_tokens": 40, "completion_tokens": 50}
            return {
                "content": "και εδώ ολοκληρώνεται καθαρά το Μέρος 2.",
                "tool_calls": [],
            }, {"prompt_tokens": 60, "completion_tokens": 30}

    agent = Akatikito(str(tmp_path), Config(auto_mode=False), TwoPartModel())
    ans = agent.chat("Εξήγησέ μου")
    assert ans == "Μέρος 1 της αναλυτικής εξήγησης — και εδώ ολοκληρώνεται καθαρά το Μέρος 2."
    assert any(ev.kind == "recovery" and ev.data.get("recovery_kind") == "text_continuation" for ev in agent.events.log)

    class LoopingModel:
        def chat(self, messages, tools=None, cache_key=None):
            return {
                "content": ("Αυτό επαναλαμβάνεται ασταμάτητα ξανά και ξανά χωρίς νόημα.\n") * 25,
                "tool_calls": [],
                "truncated_text": True,
            }, {"prompt_tokens": 20, "completion_tokens": 200}

    agent2 = Akatikito(str(tmp_path), Config(auto_mode=False), LoopingModel())
    ans2 = agent2.chat("Γράψε κάτι")
    assert ans2 == REPETITION_ABORT_MESSAGE
    assert any(ev.kind == "recovery" and ev.data.get("recovery_kind") == "repetition_abort" for ev in agent2.events.log)
