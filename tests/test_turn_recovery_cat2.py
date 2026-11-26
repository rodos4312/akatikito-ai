"""Category 2 tests: Turn auto-recovery (orphan tool calls, context overflow & image rejection)."""
from __future__ import annotations

import json
import pytest

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.model import ModelError, ScriptedModel
from akatikito.turn_recovery import (
    attempt_turn_recovery,
    classify_recoverable_error,
    close_interrupted_tool_sequence,
    strip_images_from_messages,
)


def test_close_interrupted_tool_sequence_heals_missing_and_stray_tool_messages():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "tool_call_id": "stray_before", "content": "orphan"},
        {"role": "user", "content": "do stuff", "_origin": "owner"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "list_files", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok1"},
        {"role": "tool", "tool_call_id": "c1", "content": "duplicate_c1"},
        # c2 was interrupted and never received a tool message!
        {"role": "user", "content": "continue", "_origin": "owner"},
    ]
    fixes = close_interrupted_tool_sequence(msgs)
    assert fixes == 3  # 1 stray before + 1 duplicate + 1 missing c2 synthesized
    # Strictly valid according to ContextWindow.groups()!
    ContextWindow.groups(msgs)
    tool_ids = [m["tool_call_id"] for m in msgs if m.get("role") == "tool"]
    assert tool_ids == ["c1", "c2"]
    assert "διακόπηκε" in [m["content"] for m in msgs if m.get("tool_call_id") == "c2"][0]
    # Second pass is a pure no-op.
    assert close_interrupted_tool_sequence(msgs) == 0


def test_agent_heals_interrupted_tool_calls_automatically_on_next_turn(tmp_path):
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([{"content": "Συνεχίζω καθαρά!"}]))
    agent.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first", "_origin": "owner"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c_interrupted", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}],
        },
    ]
    ans = agent.chat("Πάμε παρακάτω")
    assert "Συνεχίζω καθαρά!" in ans
    ContextWindow.groups(agent.messages)


def test_classify_and_one_shot_recovery_on_context_overflow(tmp_path):
    assert classify_recoverable_error(ModelError("400 maximum context length exceeded")) == "context_overflow"
    assert classify_recoverable_error(ModelError("400 invalid_image_url: corrupt image")) == "image_rejected"
    assert classify_recoverable_error(ModelError("400 tool_call_id missing")) == "orphan_tool_sequence"
    assert classify_recoverable_error(ModelError("401 unauthorized")) is None

    calls = {"n": 0}

    class OverflowOnceModel:
        def chat(self, messages, tools=None, cache_key=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ModelError("400 Bad Request: maximum context length exceeded")
            return {"content": "Ανάκαμψη επιτυχής!", "tool_calls": []}, {"prompt_tokens": 50, "completion_tokens": 10}

    agent = Akatikito(str(tmp_path), Config(auto_mode=False, auto_compact=False), OverflowOnceModel())
    agent.messages = [{"role": "system", "content": "sys"}]
    for i in range(1, 8):
        agent.messages.append({"role": "user", "content": f"Step {i}: " + ("abc " * 400), "_origin": "owner"})
        agent.messages.append({"role": "assistant", "content": f"Reply {i}: " + ("xyz " * 400)})

    ans = agent.chat("Συνέχισε")
    assert "Ανάκαμψη επιτυχής!" in ans
    assert calls["n"] == 2
    assert any(ev.kind == "recovery" and ev.data.get("recovery_kind") == "context_overflow" for ev in agent.events.log)


def test_one_shot_recovery_strips_rejected_image_and_never_loops(tmp_path):
    calls = {"n": 0}

    class RejectImageOnceModel:
        def chat(self, messages, tools=None, cache_key=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ModelError("400 Bad Request: model does not support image input")
            # Verify image_url block was stripped on retry!
            serialized = json.dumps(messages, ensure_ascii=False)
            assert "image_url" not in serialized
            return {"content": "Απαντώ χωρίς εικόνα.", "tool_calls": []}, {"prompt_tokens": 20, "completion_tokens": 10}

    agent = Akatikito(str(tmp_path), Config(auto_mode=False, supports_vision=True), RejectImageOnceModel())
    ans = agent.chat(
        "Τι βλέπεις;",
        attachments=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
    )
    assert "Απαντώ χωρίς εικόνα." in ans
    assert calls["n"] == 2
    assert any(ev.kind == "recovery" and ev.data.get("recovery_kind") == "image_rejected" for ev in agent.events.log)

    # Guard: a persistent error only attempts recovery ONCE and then raises cleanly.
    agent2 = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    agent2.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
    ]
    assert strip_images_from_messages(agent2.messages) == 1
    # Second attempt on same kind is rejected by attempted guard.
    assert attempt_turn_recovery(agent2, ModelError("invalid image"), {"image_rejected"}) == (False, "")

    class AlwaysOverflowModel:
        def chat(self, *a, **kw):
            raise ModelError("context_length_exceeded")

    agent3 = Akatikito(str(tmp_path), Config(auto_mode=False), AlwaysOverflowModel())
    with pytest.raises(ModelError):
        agent3.chat("hi")
