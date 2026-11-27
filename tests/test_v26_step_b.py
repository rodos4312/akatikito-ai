"""Comprehensive deterministic tests for Step B (#5 Background Processes & #6 Context Pinning)."""
from __future__ import annotations

import re
import sys

from akatikito import modes
from akatikito.agent import Akatikito
from akatikito.bg_processes import (
    MAX_RUNNING_PROCESSES,
    get_registry,
    read_background_process,
    start_background_process,
    stop_all_background_processes,
    stop_background_process,
)
from akatikito.compaction import compact_conversation
from akatikito.config import Config
from akatikito.context_pin import MAX_PINNED_FILES
from akatikito.model import ScriptedModel
from akatikito.subtasks import CHILD_FORBIDDEN_TOOLS
from akatikito.tools import dispatch
from akatikito.workspace import Workspace


def _poll_until(predicate, timeout: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
    return predicate()


# ---------------------------------------------------------------------------
# 5. Background processes
# ---------------------------------------------------------------------------


def test_background_process_start_read_stop_and_redaction(tmp_path):
    ws = Workspace(tmp_path)
    cmd = f'"{sys.executable}" -u -c "import threading; print(\'READY_BG_OK sk-1234567890abcdefghijklmnop\', flush=True); threading.Event().wait(30)"'
    started = start_background_process(ws, cmd, label="demo-server")
    assert started.startswith("ΕΓΙΝΕ: proc_id=")
    m = re.search(r"proc_id=(bg-[a-f0-9]+)", started)
    assert m is not None
    pid_key = m.group(1)

    try:
        # Output arrives and secrets are redacted
        assert _poll_until(lambda: "READY_BG_OK" in read_background_process(ws, pid_key))
        snap = read_background_process(ws, pid_key)
        assert "status=running" in snap
        assert "READY_BG_OK" in snap
        assert "sk-1234567890abcdefghijklmnop" not in snap
    finally:
        stopped = stop_background_process(ws, pid_key)
        assert stopped.startswith("ΕΓΙΝΕ: σταμάτησε")
        assert get_registry(ws).running_count() == 0


def test_background_process_enforces_owner_guard_and_concurrency_cap(tmp_path):
    ws = Workspace(tmp_path)
    ws.command_guard = lambda command, risk: False
    denied = dispatch(ws, "start_background_process", {"command": f'"{sys.executable}" -c "print(1)"'})
    assert "ΑΠΟΡΡΙΨΗ [OWNER_DENIED]" in denied

    # Rejects dangerous command without explicit guard
    ws_no_guard = Workspace(tmp_path)
    assert "ΑΠΟΡΡΙΨΗ" in start_background_process(ws_no_guard, "rm -rf /")
    assert "ΑΠΟΡΡΙΨΗ" in start_background_process(ws_no_guard, "python ../escape.py")

    # Enforces MAX_RUNNING_PROCESSES cap and cleans up via agent.stop()
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    cmd = f'"{sys.executable}" -u -c "import threading; threading.Event().wait(30)"'
    try:
        for i in range(MAX_RUNNING_PROCESSES):
            res = start_background_process(agent.ws, cmd, label=f"srv-{i}")
            assert res.startswith("ΕΓΙΝΕ:")
        over = start_background_process(agent.ws, cmd, label="too-many")
        assert over.startswith("ΣΦΑΛΜΑ:") and "Όριο" in over
        assert get_registry(agent.ws).running_count() == MAX_RUNNING_PROCESSES

        # agent.stop() kills all running workspace background processes!
        agent.stop()
        assert get_registry(agent.ws).running_count() == 0
    finally:
        stop_all_background_processes(agent.ws)


def test_background_process_tools_mode_and_subtask_permissions():
    bg_tools = {"start_background_process", "read_background_process", "stop_background_process"}
    assert bg_tools <= set(modes.allowed_tools("code"))
    assert bg_tools <= set(modes.allowed_tools("debug"))
    for read_or_office in ("ask", "architect", "assistant"):
        assert not (bg_tools & set(modes.allowed_tools(read_or_office)))
    # Subtasks cannot spawn lingering background servers
    assert bg_tools <= CHILD_FORBIDDEN_TOOLS


# ---------------------------------------------------------------------------
# 6. Context Pinning
# ---------------------------------------------------------------------------


def test_pin_slash_commands_and_survival_across_compaction(tmp_path):
    spec = tmp_path / "PLAN.md"
    spec.write_text("CRITICAL_SPEC_V1: 7 vehicles and SLA rules.\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET_KEY=topsecret\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02\x03")

    spy_seen: list[list[dict]] = []

    class _SpyModel(ScriptedModel):
        def chat(self, messages, tools=None, cache_key=None):
            spy_seen.append(list(messages))
            return super().chat(messages, tools, cache_key)

    model = _SpyModel([{"content": "ok1"}, {"content": "ok2"}])
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), model)

    # Slash commands cost 0 model calls
    assert "Δεν υπάρχουν" in agent.chat("/pins")
    assert "ΑΠΟΡΡΙΨΗ" in agent.chat("/pin .env")
    assert "ΣΦΑΛΜΑ" in agent.chat("/pin bin.dat")
    assert "Καρφιτσώθηκε" in agent.chat("/pin PLAN.md")
    assert len(spy_seen) == 0

    # Turn 1 sees live PLAN.md content
    assert agent.chat("Τι λέει το πλάνο;") == "ok1"
    sent1 = "\n".join(str(m.get("content", "")) for m in spy_seen[-1])
    assert "CRITICAL_SPEC_V1" in sent1

    # Update PLAN.md on disk + run full compaction -> Turn 2 STILL sees FRESH updated PLAN.md!
    spec.write_text("CRITICAL_SPEC_V2: updated live after compaction!\n", encoding="utf-8")
    for i in range(6):
        agent.messages.append({"role": "user", "content": f"Old turn {i}: " + ("text " * 150), "_origin": "owner"})
        agent.messages.append({"role": "assistant", "content": f"Old reply {i}: " + ("more " * 150)})
    comp_res = compact_conversation(agent, use_llm=False, keep_tail_groups=2, reason="manual")
    assert comp_res["compacted"] is True

    assert agent.chat("Συνέχισε με το πλάνο.") == "ok2"
    sent2 = "\n".join(str(m.get("content", "")) for m in spy_seen[-1])
    assert "CRITICAL_SPEC_V2" in sent2

    # Unpin & reset clean up pins
    assert "Ξεκαρφιτσώθηκε" in agent.chat("/unpin PLAN.md")
    assert agent.pins.list_paths() == []


def test_pin_context_file_tool_enforces_max_cap_and_slim_ask_mode(tmp_path):
    for i in range(MAX_PINNED_FILES + 1):
        (tmp_path / f"doc_{i}.md").write_text(f"Doc {i}\n", encoding="utf-8")
    ws = Workspace(tmp_path)

    for i in range(MAX_PINNED_FILES):
        res = dispatch(ws, "pin_context_file", {"path": f"doc_{i}.md", "action": "pin"})
        assert "Καρφιτσώθηκε" in res
    over = dispatch(ws, "pin_context_file", {"path": f"doc_{MAX_PINNED_FILES}.md", "action": "pin"})
    assert over.startswith("ΣΦΑΛΜΑ:") and "όριο" in over
    assert "doc_0.md" in dispatch(ws, "pin_context_file", {"action": "list"})
    assert "Ξεκαρφιτσώθηκαν" in dispatch(ws, "pin_context_file", {"action": "unpin", "path": "clear"})

    # Stays out of slim ASK_CORE_TOOLS, unlocks on pin/καρφιτσ topic
    assert "pin_context_file" not in modes.ASK_CORE_TOOLS
    unlocked = modes.select_ask_tools("Καρφίτσωσε το PLAN.md", set(modes.allowed_tools("ask")), set())
    assert "pin_context_file" in unlocked
