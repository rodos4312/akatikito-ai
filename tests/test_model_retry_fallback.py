"""Tests for smart error classification, Retry-After backoff, and explicit fallback provider (PRO-PLAN B3)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from akatikito.config import Config
from akatikito.model import ModelError, OpenAIModel
from akatikito.model_retry import build_fallback_config, classify, extract_retry_after


class _StatusErr(Exception):
    def __init__(self, msg: str, status: int, retry_after: str | None = None):
        super().__init__(msg)
        self.status_code = status
        if retry_after is not None:
            self.response = SimpleNamespace(headers={"Retry-After": retry_after})


def test_classify_verdicts_for_auth_balance_bad_request_ratelimit_and_transient():
    v401 = classify(401, None, 1)
    assert not v401.retry and not v401.fallback and "κλειδί" in v401.message.lower()

    v403 = classify(403, None, 1)
    assert not v403.retry and not v403.fallback

    v402 = classify(402, None, 1)
    assert not v402.retry and v402.fallback and "υπόλοιπο" in v402.message.lower()

    v400 = classify(400, None, 1)
    assert not v400.retry and not v400.fallback

    v429_mid = classify(429, None, 1, max_attempts=3, base_delay=1.0, retry_after=7.5)
    assert v429_mid.retry and not v429_mid.fallback and v429_mid.wait == 7.5

    v429_end = classify(429, None, 3, max_attempts=3, base_delay=1.0)
    assert not v429_end.retry and v429_end.fallback

    v503_mid = classify(503, None, 1, max_attempts=2, base_delay=0.0)
    assert v503_mid.retry and not v503_mid.fallback and v503_mid.wait == 0.0

    v503_end = classify(503, None, 2, max_attempts=2, base_delay=0.0)
    assert not v503_end.retry and v503_end.fallback

    assert extract_retry_after(_StatusErr("limit", 429, "12")) == 12.0
    assert extract_retry_after(_StatusErr("limit", 429, "999")) == 30.0


def test_build_fallback_config_blocks_cross_host_key_leak():
    # Different host without its own fallback_api_key -> refused (no key leak!)
    cfg_unsafe = Config(
        model="primary-m",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-primary-secret",
        fallback_model="backup-m",
        fallback_base_url="https://openrouter.ai/api/v1",
        fallback_api_key="",
    )
    assert build_fallback_config(cfg_unsafe) is None

    # Different host with its own explicit fallback_api_key -> allowed & isolated
    cfg_safe = Config(
        model="primary-m",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-primary-secret",
        fallback_model="backup-m",
        fallback_base_url="https://openrouter.ai/api/v1",
        fallback_api_key="sk-fallback-only",
    )
    fb = build_fallback_config(cfg_safe)
    assert fb is not None and fb.model == "backup-m" and fb.api_key == "sk-fallback-only" and fb.fallback_model == ""


def test_openai_model_switches_to_explicit_fallback_on_503_but_never_on_401():
    cfg = Config(
        model="primary-m",
        base_url="http://127.0.0.1:1/v1",
        api_key="k1",
        model_retry_attempts=2,
        model_retry_delay=0.0,
        fallback_model="backup-m",
        fallback_base_url="http://127.0.0.1:2/v1",
        fallback_api_key="k2",
    )
    primary = OpenAIModel(cfg)

    class _Fail503:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            self.calls += 1
            raise _StatusErr("outage", 503)

    class _FakeFallback:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, cache_key=None):
            self.calls += 1
            return {"content": "from fallback", "tool_calls": [], "reasoning": ""}, {"prompt_tokens": 5, "completion_tokens": 2}

    fail_client = _Fail503()
    fb_stub = _FakeFallback()
    notices = []
    primary._client = fail_client
    primary._fallback_model = fb_stub
    primary.on_fallback = lambda m, msg: notices.append((m, msg))

    reply, usage = primary.chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "from fallback"
    assert fail_client.calls == 2 and fb_stub.calls == 1
    assert notices and notices[0][0] == "backup-m"

    # 401 auth error: 0 retries, NEVER calls fallback!
    class _Fail401:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            self.calls += 1
            raise _StatusErr("invalid key", 401)

    auth_client = _Fail401()
    primary._client = auth_client
    with pytest.raises(ModelError, match="κλειδί"):
        primary.chat([{"role": "user", "content": "hi"}])
    assert auth_client.calls == 1 and fb_stub.calls == 1


def test_chat_stream_uses_fallback_before_first_chunk_and_skips_retries_on_hard_quota():
    cfg = Config(
        model="primary-m",
        base_url="http://127.0.0.1:1/v1",
        api_key="k1",
        model_retry_attempts=3,
        model_retry_delay=0.0,
        fallback_model="backup-m",
        fallback_base_url="http://127.0.0.1:2/v1",
        fallback_api_key="k2",
    )
    primary = OpenAIModel(cfg)
    calls = {"primary": 0, "fallback": 0}

    def fail_quota(**kw):
        calls["primary"] += 1
        raise _StatusErr("weekly included input token limit exceeded", 429)

    class _StreamFallback:
        def chat_stream(self, messages, tools=None, cache_key=None, on_delta=None, should_stop=None):
            calls["fallback"] += 1
            if on_delta:
                on_delta("from stream fallback")
            return {"content": "from stream fallback", "tool_calls": [], "reasoning": ""}, {"prompt_tokens": 3, "completion_tokens": 3}

    primary._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail_quota)))
    primary._fallback_model = _StreamFallback()
    deltas = []
    reply, _ = primary.chat_stream([{"role": "user", "content": "hi"}], on_delta=deltas.append)
    assert reply["content"] == "from stream fallback"
    assert deltas == ["from stream fallback"]
    # Hard quota 429 must NOT retry primary (1 attempt only), then switch straight to explicit fallback!
    assert calls["primary"] == 1 and calls["fallback"] == 1
