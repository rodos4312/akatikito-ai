"""Ενιαίο token accounting: το chat() επιστρέφει usage μέσω usage_of() όπως και το streaming."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from akatikito.config import Config
from akatikito.model import OpenAIModel


class _Message:
    content = "ok"
    tool_calls = None
    reasoning_content = ""


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    prompt_cache_hit_tokens = 3
    prompt_cache_miss_tokens = 2


class _Response:
    def __init__(self, usage):
        self.choices = [type("Choice", (), {"message": _Message()})()]
        self.usage = usage


class _Completions:
    def __init__(self, usage):
        self.usage = usage

    def create(self, **kwargs):
        return _Response(self.usage)


def _model(usage):
    model = OpenAIModel.__new__(OpenAIModel)
    model.config = Config(api_key="synthetic", base_url="https://unit.invalid", model_retry_delay=0)
    model._client = type("Client", (), {"chat": type("Chat", (), {"completions": _Completions(usage)})()})()
    return model


def test_chat_usage_uses_usage_of_for_sdk_object():
    reply, usage = _model(_Usage()).chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "ok"
    assert usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cache_hit_tokens": 3,
        "cache_miss_tokens": 2,
    }


def test_chat_usage_without_provider_usage_is_zeroed():
    reply, usage = _model(None).chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "ok"
    assert usage == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
    }


if __name__ == "__main__":
    test_chat_usage_uses_usage_of_for_sdk_object()
    test_chat_usage_without_provider_usage_is_zeroed()
    print("USAGE ACCOUNTING OK")
