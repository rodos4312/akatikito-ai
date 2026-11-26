"""Characterization tests for Akatikito._execute_call dispatch & guards (Q4.7)."""
from __future__ import annotations

import json

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.modes import allowed_tools


class _DummyModel:
    def chat(self, messages, tools, cache_key=""):
        return {"content": "ok", "tool_calls": []}, {}


def _agent(tmp_path, mode="code"):
    ag = Akatikito(str(tmp_path), Config(verify_command="pytest", auto_mode=False), _DummyModel())
    ag._set_mode(mode, reason="test")
    ag._turn_tools = set(ag.allowed_tools())
    ag._learning_proposals = set()
    return ag


def _call(_tool_name, **args):
    return {"id": "c1", "name": _tool_name, "arguments": json.dumps(args)}


def test_execute_call_stop_and_invalid_args(tmp_path):
    ag = _agent(tmp_path)
    ag._stop = True
    res, checking = ag._execute_call(_call("list_files"), ag._turn_tools)
    assert res.startswith("ΑΠΟΡΡΙΨΗ") and checking is False

    ag._stop = False
    res, checking = ag._execute_call(_call("read_file"), ag._turn_tools)
    assert res.startswith("ΣΦΑΛΜΑ") and checking is False


def test_execute_call_set_mode_and_repair_restriction(tmp_path):
    ag = _agent(tmp_path, mode="code")
    turn_tools = set(allowed_tools("code"))

    res, _ = ag._execute_call(_call("set_mode", mode="ask"), turn_tools)
    assert res.startswith("Λειτουργία:") and ag.mode == "ask"

    # Switching back to code while repair pending blocks switching to ask
    ag._set_mode("code", reason="test")
    ag.repair.failed("AssertionError")
    res_blocked, _ = ag._execute_call(_call("set_mode", mode="ask"), turn_tools)
    assert res_blocked.startswith("ΑΠΟΡΡΙΨΗ")


def test_execute_call_architect_and_assistant_write_guards(tmp_path):
    arch = _agent(tmp_path, mode="architect")
    res_bad, _ = arch._execute_call(_call("write_file", path="code.py", content="x=1"), arch._turn_tools)
    assert "PLAN.md" in res_bad and res_bad.startswith("ΑΠΟΡΡΙΨΗ")

    asst = _agent(tmp_path, mode="assistant")
    res_py, _ = asst._execute_call(_call("write_file", path="script.py", content="x=1"), asst._turn_tools)
    assert res_py.startswith("ΑΠΟΡΡΙΨΗ")
    res_hidden, _ = asst._execute_call(_call("write_file", path=".secret/a.md", content="hi"), asst._turn_tools)
    assert res_hidden.startswith("ΑΠΟΡΡΙΨΗ")
    res_ok, _ = asst._execute_call(_call("write_file", path="notes.md", content="hi"), asst._turn_tools)
    assert res_ok.startswith("ΕΓΙΝΕ")


def test_execute_call_repair_blocks_unrelated_command(tmp_path):
    ag = _agent(tmp_path, mode="code")
    ag.repair.failed("AssertionError")

    res_other, _ = ag._execute_call(_call("run_command", command="echo hi"), ag._turn_tools)
    assert res_other.startswith("ΑΠΟΡΡΙΨΗ") and "καθορισμένο έλεγχο" in res_other

    res_repeat, _ = ag._execute_call(_call("run_command", command="pytest"), ag._turn_tools)
    assert res_repeat.startswith("ΑΠΟΡΡΙΨΗ") and "διάγνωση" in res_repeat


def test_execute_call_learning_quota(tmp_path):
    ag = _agent(tmp_path, mode="code")
    ag._learning_proposals = {("skill", "existing-skill")}
    res, _ = ag._execute_call(
        _call(
            "propose_skill",
            name="another-skill",
            description="desc",
            steps=["Step one inspect.", "Step two edit.", "Step three verify."],
        ),
        ag._turn_tools,
    )
    assert "όριο" in res or "καταγράφηκε" in res
