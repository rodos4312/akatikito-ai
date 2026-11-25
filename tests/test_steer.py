"""Tests for mid-flight owner steering (PRO-PLAN B7)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.config import Config


def test_steer_injects_at_next_step_and_preserves_kv_cache_prefix(tmp_path):
    (tmp_path / "notes.txt").write_text("hello world\n", encoding="utf-8")
    seen_requests: list[list[dict]] = []
    agent_ref: list[Akatikito] = []

    class ScriptedModel:
        keeps_active_history = True

        def chat(self, messages, tools=None, cache_key=None):
            snapshot = json.loads(json.dumps(messages))
            seen_requests.append(snapshot)
            if len(seen_requests) == 1:
                # Step 1: call read_file, then user sends mid-flight steer before Step 2
                agent_ref[0].steer("μην αγγίξεις τον φάκελο Γάμος")
                return {
                    "content": "",
                    "tool_calls": [{"id": "c1", "name": "read_file", "arguments": '{"path":"notes.txt"}'}],
                    "reasoning": "",
                }, {"prompt_tokens": 20, "completion_tokens": 5}
            return {"content": "Έλαβα την οδηγία σου και την ακολούθησα.", "tool_calls": [], "reasoning": ""}, {
                "prompt_tokens": 30,
                "completion_tokens": 10,
            }

    agent = Akatikito(str(tmp_path), Config(model="scripted", auto_mode=False), ScriptedModel())
    agent_ref.append(agent)

    answer = agent.chat("Διάβασε το notes.txt")
    assert "οδηγία" in answer
    assert len(seen_requests) == 2

    req1, req2 = seen_requests
    # Prefix must be byte-for-byte identical so provider KV cache stays intact!
    assert json.dumps(req2[: len(req1)], sort_keys=True) == json.dumps(req1, sort_keys=True)
    # Steer instruction arrives as the final user message right after the tool response
    assert req2[-1]["role"] == "user"
    assert "[Οδηγία του χρήστη κατά την εκτέλεση]" in req2[-1]["content"]
    assert "μην αγγίξεις τον φάκελο Γάμος" in req2[-1]["content"]


def test_app_state_steer_requires_busy_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    try:
        assert "error" in state.steer("stop touching x")
        received = []
        state.agent = SimpleNamespace(steer=lambda t: received.append(t) or True, stop=lambda: None)
        state.busy = False
        assert "error" in state.steer("wait")
        state.busy = True
        assert "error" in state.steer("   ")
        res = state.steer("κράτα το παλιό αρχείο")
        assert res == {"ok": True}
        assert received == ["κράτα το παλιό αρχείο"]
        assert state.transcript[-1]["text"] == "🧭 κράτα το παλιό αρχείο"
    finally:
        state.close()
