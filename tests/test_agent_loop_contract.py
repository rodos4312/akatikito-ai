"""Characterization tests for Akatikito._chat turn loop phases (Q4.9)."""
from __future__ import annotations

import json

from akatikito.agent import Akatikito
from akatikito.config import Config


class _ScriptedModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def chat(self, messages, tools, cache_key=""):
        self.requests.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if self.replies else {"content": "done", "tool_calls": []}
        return reply, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _tc(cid, name, **args):
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def test_chat_loop_direct_answer_and_tokens(tmp_path):
    model = _ScriptedModel([{"content": "Γεια σου!", "tool_calls": []}])
    ag = Akatikito(str(tmp_path), Config(verify_command=" ", auto_mode=False), model)
    ans = ag.chat("Πες γεια")
    assert ans == "Γεια σου!"
    assert ag.turn_usage["model_calls"] == 1
    assert ag.tokens["prompt_tokens"] == 10
    assert any(e.kind == "answer" for e in ag.events.log)


def test_chat_loop_slash_learn_transforms_prompt(tmp_path):
    model = _ScriptedModel([{"content": "Έτοιμο.", "tool_calls": []}])
    ag = Akatikito(str(tmp_path), Config(verify_command=" ", auto_mode=True), model)
    ag.chat("/learn deploy flow")
    assert ag.ws.learn_requested is True
    assert ag.mode == "assistant"
    user_msgs = [m["content"] for m in ag.messages if m.get("role") == "user"]
    assert any("[ΕΝΤΟΛΗ ΕΚΜΑΘΗΣΗΣ /learn]" in str(c) and "deploy flow" in str(c) for c in user_msgs)


def test_chat_loop_write_then_finish_marks_complete(tmp_path):
    model = _ScriptedModel([
        {"content": "Γράφω το αρχείο", "tool_calls": [_tc("c1", "write_file", path="hello.txt", content="hi")]},
        {"content": "Ολοκληρώθηκε.", "tool_calls": []},
    ])
    ag = Akatikito(str(tmp_path), Config(verify_command=" ", auto_mode=False), model)
    ag._set_mode("code", reason="test")
    ans = ag.chat("Φτιάξε hello.txt")
    assert ans == "Ολοκληρώθηκε."
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert ag.task.status == "complete"
    assert "hello.txt" in ag._task_writes


def test_chat_loop_max_steps_and_before_model_hook(tmp_path):
    # Max steps limit reached
    loop_model = _ScriptedModel([
        {"content": "", "tool_calls": [_tc("c1", "list_files")]},
        {"content": "", "tool_calls": [_tc("c2", "list_files")]},
    ])
    ag = Akatikito(str(tmp_path), Config(verify_command=" ", max_steps=1, auto_mode=False), loop_model)
    out = ag.chat("Δες αρχεία")
    assert "όριο των 1 βημάτων" in out

    # before_model returning a blocker stops before calling the model
    blocked_model = _ScriptedModel([{"content": "should not run", "tool_calls": []}])
    ag2 = Akatikito(str(tmp_path), Config(verify_command=" ", auto_mode=False), blocked_model)
    ag2.before_model = lambda: "Custom policy gate"
    out2 = ag2.chat("Κάνε κάτι")
    assert "Custom policy gate" in out2
    assert len(blocked_model.requests) == 0
