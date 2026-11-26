"""Regression tests for live reasoning stream, smart scroll/UI visibility, 50-step default, and 1-click autonomy."""
from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path
from types import SimpleNamespace

from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.config import Config
from akatikito.model import OpenAIModel
from akatikito.settings import Settings
from akatikito.streaming import StreamReply, usage_of

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_settings_defaults_autonomous_and_50_steps(tmp_path):
    st = Settings()
    assert st.autonomous is True
    assert st.max_steps == 50

    # Missing autonomous key in older/partial JSON defaults to True; explicit false is preserved.
    p1 = tmp_path / "missing.json"
    p1.write_text('{"provider": "DeepSeek"}', encoding="utf-8")
    assert Settings.load(p1).autonomous is True
    assert Settings.load(p1).max_steps == 50

    p2 = tmp_path / "strict.json"
    p2.write_text('{"provider": "DeepSeek", "autonomous": false, "max_steps": 80}', encoding="utf-8")
    loaded = Settings.load(p2)
    assert loaded.autonomous is False
    assert loaded.max_steps == 80


def test_ui_fallbacks_have_no_hardcoded_25_steps_and_simple_view_keeps_live_telemetry():
    settings_js = (REPO_ROOT / "akatikito" / "ui" / "settings.js").read_text(encoding="utf-8")
    onboarding_js = (REPO_ROOT / "akatikito" / "onboarding.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "akatikito" / "ui" / "style.css").read_text(encoding="utf-8")

    assert "r.max_steps || 50" in settings_js
    assert "r.max_steps || 25" not in settings_js
    assert "value||50" in onboarding_js
    assert "value||25" not in onboarding_js

    # Simple view must NOT hide live .status bar, #tokens, or .live-step (reasoning & tool calls).
    simple_rule = [line for line in style_css.splitlines() if "body.simple-view" in line][0]
    assert "#chat>.status" not in simple_rule
    assert "#tokens" not in simple_rule
    assert ":not(.live-step)" in simple_rule


def test_stream_reply_and_chat_stream_emit_live_reasoning_deltas():
    reply = StreamReply()
    c1 = {"choices": [{"index": 0, "delta": {"reasoning_content": "Step 1: check file. "}}]}
    c2 = {"choices": [{"index": 0, "delta": {"reasoning_content": "Step 2: respond."}}]}
    c3 = {"choices": [{"index": 0, "delta": {"content": "Hello!"}, "finish_reason": "stop"}]}

    assert reply.feed(c1) == "" and reply.last_reasoning == "Step 1: check file. "
    assert reply.feed(c2) == "" and reply.last_reasoning == "Step 2: respond."
    assert reply.feed(c3) == "Hello!" and reply.last_reasoning == ""
    assert reply.reply()["reasoning"] == "Step 1: check file. Step 2: respond."

    # Also verify OpenAIModel._consume_stream forwards reasoning deltas live.
    seen_r: list[str] = []
    seen_c: list[str] = []
    sr = StreamReply()
    flag = [False]
    OpenAIModel._consume_stream([c1, c2, c3], sr, seen_c.append, seen_r.append, lambda: False, flag)
    assert flag[0] is True
    assert "".join(seen_r) == "Step 1: check file. Step 2: respond."
    assert "".join(seen_c) == "Hello!"


def test_app_state_streaming_accumulates_reasoning_events(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    try:
        state.emit_event(SimpleNamespace(kind="stream_start", data={"stream_id": "s1", "model": "reasoner"}))
        state.emit_event(SimpleNamespace(kind="stream_reasoning", data={"stream_id": "s1", "text": "Thinking..."}))
        state.emit_event(SimpleNamespace(kind="stream_delta", data={"stream_id": "s1", "text": "Answer"}))
        assert state.streaming["reasoning"] == "Thinking..."
        assert state.streaming["text"] == "Answer"
    finally:
        state.close()


def test_answer_approval_autonomous_enables_only_for_non_dangerous_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    state = AppState()
    ready = threading.Event()
    orig_notify = state.approvals.notify

    def _on_notify():
        orig_notify()
        if state.approvals.pending():
            ready.set()

    state.approvals.notify = _on_notify
    try:
        state.settings.autonomous = False

        # 1. Dangerous command with autonomous=True MUST NOT flip settings.autonomous!
        ready.clear()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            fut = pool.submit(
                state.approvals.request, tmp_path, "command", {"command": "rm -rf foo"}, "dangerous", False
            )
            assert ready.wait(timeout=3)
            ticket = state.approvals.pending()[0]["id"]
            res = state.answer_approval(ticket, allow=True, always=False, autonomous=True)
            assert res["ok"] is True
            assert fut.result(timeout=3) is True
            assert state.settings.autonomous is False

        # 2. Normal command with autonomous=True DOES enable settings.autonomous!
        ready.clear()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            fut = pool.submit(
                state.approvals.request, tmp_path, "command", {"command": "python -c 'print(1)'"}, "normal", False
            )
            assert ready.wait(timeout=3)
            ticket = state.approvals.pending()[0]["id"]
            res = state.answer_approval(ticket, allow=True, always=False, autonomous=True)
            assert res["ok"] is True
            assert fut.result(timeout=3) is True
            assert state.settings.autonomous is True
    finally:
        state.close()


def test_agent_model_reply_streams_reasoning_events(tmp_path):
    class ReasoningStreamModel:
        _supports_reasoning_stream = True

        def chat_stream(self, messages, tools=None, cache_key=None, on_delta=None, should_stop=None, on_reasoning=None):
            if on_reasoning:
                on_reasoning("Analyzing file...")
            if on_delta:
                on_delta("Done.")
            return {"content": "Done.", "reasoning": "Analyzing file...", "tool_calls": []}, {"prompt_tokens": 10, "completion_tokens": 5}

    agent = Akatikito(str(tmp_path), Config(stream_responses=True, auto_mode=False), ReasoningStreamModel())
    reply, _ = agent._model_reply([{"role": "user", "content": "hi"}], None, 1)
    assert reply["content"] == "Done."
    kinds = [ev.kind for ev in agent.events.log]
    assert "stream_reasoning" in kinds
    assert "stream_delta" in kinds


def test_usage_of_reads_openrouter_openai_deepseek_and_anthropic_cache_fields():
    # OpenAI / OpenRouter / Gemini dict shape
    u_or = usage_of({"prompt_tokens": 10000, "completion_tokens": 500, "prompt_tokens_details": {"cached_tokens": 8000}})
    assert u_or == {"prompt_tokens": 10000, "completion_tokens": 500, "cache_hit_tokens": 8000, "cache_miss_tokens": 2000}

    # OpenAI SDK object shape
    obj = SimpleNamespace(
        prompt_tokens=6000,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=4500),
    )
    u_obj = usage_of(obj)
    assert u_obj["cache_hit_tokens"] == 4500 and u_obj["cache_miss_tokens"] == 1500

    # DeepSeek shape
    u_ds = usage_of({"prompt_tokens": 5000, "completion_tokens": 100, "prompt_cache_hit_tokens": 3000, "prompt_cache_miss_tokens": 2000})
    assert u_ds["cache_hit_tokens"] == 3000 and u_ds["cache_miss_tokens"] == 2000

    # Anthropic-compat shape
    u_an = usage_of({"prompt_tokens": 4000, "completion_tokens": 80, "cache_read_input_tokens": 2500, "cache_creation_input_tokens": 1500})
    assert u_an["cache_hit_tokens"] == 2500 and u_an["cache_miss_tokens"] == 1500

