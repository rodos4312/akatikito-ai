"""Category 1 tests: Stale tool-output demotion, auto/manual compaction, lossless recall & /api/compact."""
from __future__ import annotations

import json
import re

from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.compaction import (
    compact_conversation,
    demote_stale_tool_outputs,
    maybe_auto_compact,
)
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.model import ScriptedModel


def _make_long_messages(num_turns: int = 8, payload_chars: int = 2400) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "System policy rules."}]
    for i in range(1, num_turns + 1):
        msgs.append({"role": "user", "content": f"Task {i}: inspect module_{i}.py", "_origin": "owner"})
        call_id = f"call_{i}"
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": f"module_{i}.py"})},
            }],
        })
        body = f"# module_{i}.py unique_marker_{i}\n" + ("x = 1\n" * (payload_chars // 6))
        msgs.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": body,
            "_written_paths": [f"module_{i}.py"] if i % 2 == 0 else [],
        })
        msgs.append({"role": "assistant", "content": f"Completed analysis of module_{i}.py with clear findings."})
    return msgs


def test_demote_stale_tool_outputs_preserves_tail_and_lossless_recall(tmp_path):
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    msgs = _make_long_messages(num_turns=6, payload_chars=2400)

    demoted, count = demote_stale_tool_outputs(msgs, agent.context, keep_recent_groups=3, min_chars=1200)
    assert count >= 4
    # Tool call / tool result pairing remains strictly valid.
    ContextWindow.groups(demoted)

    # Old tool outputs are stubbed with recall_context id=... and exact content is recoverable.
    old_tool = [m for m in demoted if m.get("tool_call_id") == "call_1"][0]
    assert old_tool["content"].startswith("[Συμπιεσμένη έξοδος εργαλείου read_file (module_1.py)")
    m = re.search(r"recall_context id=([a-f0-9]{20})", old_tool["content"])
    assert m is not None
    recalled = agent.context.recall(m.group(1))
    assert "unique_marker_1" in recalled["text"]

    # Most recent tool output inside keep_recent_groups stays verbatim.
    latest_tool = [m for m in demoted if m.get("tool_call_id") == "call_6"][0]
    assert "unique_marker_6" in latest_tool["content"]


def test_compact_conversation_deterministic_shrinks_and_preserves_invariants(tmp_path):
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    agent.messages = _make_long_messages(num_turns=7, payload_chars=2400)

    res = compact_conversation(agent, use_llm=False, keep_tail_groups=3, reason="manual")
    assert res["ok"] is True and res["compacted"] is True
    assert res["method"] == "deterministic"
    assert res["after_chars"] < res["before_chars"]
    assert res["saved_pct"] >= 50.0
    assert re.fullmatch(r"[a-f0-9]{20}", res["archive_id"])

    # System prompt stays first, summary follows, tail is preserved, groups invariant holds.
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1].get("_compaction_id") == res["archive_id"]
    assert "module_2.py" in agent.messages[1]["content"]
    ContextWindow.groups(agent.messages)

    # Full archived middle history is losslessly retrievable.
    recalled = agent.context.recall(res["archive_id"])
    assert "unique_marker_1" in recalled["text"]
    assert any(ev.kind == "compacted" for ev in agent.events.log)


def test_compact_conversation_uses_llm_and_falls_back_cleanly_on_error(tmp_path):
    # 1. LLM path succeeds
    llm_summary = (
        "1. Στόχος χρήστη: Έλεγχος όλων των modules του έργου.\n"
        "2. Αλλαγμένα αρχεία: module_2.py, module_4.py.\n"
        "3. Ευρήματα: Όλα τα modules πέρασαν τον έλεγχο.\n"
        "4. Τι απομένει: Τελική παράδοση."
    )
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([{"content": llm_summary}]))
    agent.messages = _make_long_messages(num_turns=7, payload_chars=2400)
    res = compact_conversation(agent, use_llm=True, keep_tail_groups=3, reason="manual")
    assert res["compacted"] is True and res["method"] == "llm"
    assert "Έλεγχος όλων των modules" in agent.messages[1]["content"]

    # 2. LLM raises -> seamless deterministic fallback, zero exception
    class FailingModel:
        def chat(self, *a, **kw):
            raise RuntimeError("provider 503 down")

    agent2 = Akatikito(str(tmp_path), Config(auto_mode=False), FailingModel())
    agent2.messages = _make_long_messages(num_turns=7, payload_chars=2400)
    res2 = compact_conversation(agent2, use_llm=True, keep_tail_groups=3, reason="manual")
    assert res2["compacted"] is True and res2["method"] == "deterministic"
    ContextWindow.groups(agent2.messages)


def test_maybe_auto_compact_respects_threshold_and_runs_automatically_on_turn(tmp_path):
    cfg = Config(auto_mode=False, context_active_max_bytes=12000, auto_compact=True, auto_compact_threshold=0.80)
    agent = Akatikito(str(tmp_path), cfg, ScriptedModel([{"content": "Συνεχίζω κανονικά."}]))

    # Below threshold -> no compaction, messages untouched.
    agent.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi", "_origin": "owner"},
        {"role": "assistant", "content": "hello"},
    ]
    assert maybe_auto_compact(agent) is None
    assert len(agent.messages) == 3

    # Above threshold with bulky tool outputs -> Stage 1 auto-demotion shrinks bytes with 0 LLM calls!
    agent.messages = _make_long_messages(num_turns=7, payload_chars=2400)
    answer = agent.chat("Τι κάναμε ως τώρα;")
    assert "Συνεχίζω κανονικά." in answer
    auto_evs = [ev for ev in agent.events.log if ev.kind == "compacted" and ev.data.get("reason") == "auto"]
    assert auto_evs and auto_evs[-1].data["method"] == "demote"
    assert auto_evs[-1].data["after_chars"] < auto_evs[-1].data["before_chars"]
    assert auto_evs[-1].data["saved_pct"] >= 40.0

    # Above threshold with long prose (no bulky tools) -> Stage 2 full auto-compaction shrinks message list!
    prose_agent = Akatikito(str(tmp_path), cfg, ScriptedModel([{"content": "Σύνοψη LLM με όλα τα κύρια σημεία της συζήτησης."}, {"content": "ΟΚ!"}]))
    prose_agent.messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Turn {i}: " + ("λεπτομέρεια " * 220), **({"_origin": "owner"} if i % 2 == 0 else {})}
        for i in range(10)
    ]
    before_count = len(prose_agent.messages)
    ans2 = prose_agent.chat("Συνέχισε.")
    assert "ΟΚ!" in ans2
    assert len(prose_agent.messages) < before_count
    assert any(ev.kind == "compacted" and ev.data.get("method") == "llm" for ev in prose_agent.events.log)


def test_app_state_compact_session_api_and_busy_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    try:
        assert "error" in state.compact_session()

        agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
        agent.messages = _make_long_messages(num_turns=7, payload_chars=2400)
        state.agent = agent

        # Busy guard
        state.busy = True
        assert "error" in state.compact_session()
        state.busy = False

        res = state.compact_session(use_llm=False)
        assert not res.get("error")
        assert res["compaction"]["compacted"] is True
        assert res["compaction"]["saved_pct"] >= 50.0
        assert "Συμπιέστηκε" in res["notice"]
    finally:
        state.close()


def test_real_prompt_tokens_calibration_triggers_compaction_and_resets(tmp_path):
    cfg = Config(auto_mode=False, auto_compact=True, auto_compact_threshold=0.80, context_active_max_bytes=200000)
    agent = Akatikito(str(tmp_path), cfg, ScriptedModel([]))
    agent.messages = _make_long_messages(num_turns=7, payload_chars=1200)
    # Byte footprint is well below 80% of 200k bytes, so byte check alone wouldn't fire.
    agent.context.native_context_tokens = 128_000
    agent._last_prompt_tokens = 105_000  # 82% of real 128k window -> must trigger!

    res = maybe_auto_compact(agent)
    assert res is not None and res["compacted"] is True
    assert agent._last_prompt_tokens == 0  # Reset after compaction so next step doesn't loop!
    assert maybe_auto_compact(agent) is None

