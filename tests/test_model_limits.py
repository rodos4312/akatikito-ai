"""Tests for dynamic LLM context-window and output-limit detection & adaptive chunk sizing."""
from __future__ import annotations

import hashlib
import json

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import OpenAIModel
from akatikito.model_limits import (
    extract_item_limits,
    format_window_hint,
    lookup_known_limits,
    recommended_chunk_lines,
    resolve_cached_limits,
)


def test_extract_item_limits_supports_openrouter_and_nanogpt_shapes():
    openrouter_row = {
        "id": "particle-ai/particle-1",
        "context_length": 131072,
        "top_provider": {"context_length": 131072, "max_completion_tokens": 16384},
    }
    assert extract_item_limits(openrouter_row) == (131072, 16384)

    nanogpt_row = {
        "id": "deepseek-v3",
        "context_length": 128000,
        "max_output_tokens": 8192,
    }
    assert extract_item_limits(nanogpt_row) == (128000, 8192)

    class FakeSdkModel:
        id = "openrouter/model-x"
        model_extra = {"context_length": 200000, "top_provider": {"max_completion_tokens": 32768}}

    assert extract_item_limits(FakeSdkModel()) == (200000, 32768)


def test_known_family_fallback_and_adaptive_chunk_lines():
    lim = lookup_known_limits("google/gemini-2.5-pro")
    assert lim.context_tokens == 1_048_576
    assert lim.max_output_tokens == 65_536
    assert recommended_chunk_lines(lim.max_output_tokens) == (800, 1200)

    small_lo, small_hi = recommended_chunk_lines(4096)
    med_lo, med_hi = recommended_chunk_lines(8192)
    big_lo, big_hi = recommended_chunk_lines(16384)
    assert small_hi < med_lo < big_lo

    hint_big = format_window_hint(200_000, 16_384)
    assert "200k" in hint_big and "16k" in hint_big and "μεγάλο περιθώριο" in hint_big
    hint_small = format_window_hint(128_000, 4_096)
    assert "150–220" in hint_small


def test_openai_model_and_agent_loop_use_detected_openrouter_limits(tmp_path, monkeypatch):
    from akatikito import model as model_mod

    cache_path = tmp_path / "models.json"
    base_url = "https://openrouter.ai/api/v1"
    key = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:12]
    cache_path.write_text(
        json.dumps(
            {
                key: {
                    "at": 9999999999,
                    "detailed": False,
                    "schema": 3,
                    "models": [
                        {
                            "id": "particle-ai/particle-1",
                            "included": True,
                            "available": True,
                            "context_length": 131072,
                            "max_output_tokens": 16384,
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_mod, "_models_cache_path", lambda: cache_path)
    monkeypatch.setattr(model_mod, "list_models", lambda *a, **k: ["particle-ai/particle-1"])

    cfg = Config(base_url=base_url, api_key="test-key", model="particle-ai/particle-1")
    m = OpenAIModel(cfg)
    assert m.context_window_tokens == 131072
    assert m.max_output_tokens == 16384

    # Verify agent_loop injects dynamic window hint and configures context budget!
    seen_system = []

    class FakeModel:
        context_window_tokens = 200_000
        max_output_tokens = 16_384

        def chat(self, messages, tools=None, cache_key=None):
            seen_system.append(messages[0]["content"])
            return {"content": "Έτοιμο.", "tool_calls": []}, {"prompt_tokens": 20, "completion_tokens": 5}

    agent = Akatikito(str(tmp_path), Config(auto_mode=False), FakeModel())
    assert agent.chat("Δες τον κώδικα") == "Έτοιμο."
    assert agent.context.native_context_tokens == 200_000
    assert agent.context.active_max_bytes == int(200_000 * 0.6)
    assert any("ΠΑΡΑΘΥΡΟ ΜΟΝΤΕΛΟΥ" in s and "μεγάλο περιθώριο" in s for s in seen_system)
    assert resolve_cached_limits({}, "missing", "deepseek-chat").context_tokens == 128_000
