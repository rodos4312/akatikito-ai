"""Thorough tests for Step A v2.6 foundations:
1. Primary rate-limit cooldown & deterministic empty-response guard (model_guards + model.py)
2. Cache-friendly batch image eviction on outbound views (image_eviction + context.py)
3. Thread-safe single-flight TTL cache for web_search & web_fetch (web_cache + network_tools.py)
4. Unicode punctuation normalization fallback in edit_file (editing.py)
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from akatikito import network_tools as net
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.editing import EditError, apply_edits
from akatikito.image_eviction import EVICTED_IMAGE_PLACEHOLDER, evict_old_outbound_images
from akatikito.model import OpenAIModel
from akatikito.model_guards import EmptyResponseTracker, RateLimitCooldown, is_zero_output_empty
from akatikito.web_cache import SingleFlightTTLCache, clear_web_cache
from akatikito.workspace import Workspace


# ---------------------------------------------------------------------------
# 1. Rate-limit cooldown & deterministic empty-response guard
# ---------------------------------------------------------------------------


def test_rate_limit_cooldown_retry_after_and_exponential_backoff():
    cd = RateLimitCooldown(base_seconds=60.0, max_seconds=300.0)
    assert cd.active(now=100.0) is False

    # Honors explicit Retry-After
    d1 = cd.arm(retry_after=15.0, now=100.0)
    assert d1 == 15.0
    assert cd.active(now=110.0) is True
    assert cd.remaining(now=110.0) == pytest.approx(5.0)
    assert cd.active(now=116.0) is False

    # Falls back to exponential backoff when retry_after is absent (level already 1 -> 120s, then 240s, capped at 300s)
    d2 = cd.arm(None, now=200.0)
    assert d2 == 120.0
    d3 = cd.arm(None, now=400.0)
    assert d3 == 240.0
    d4 = cd.arm(None, now=700.0)
    assert d4 == 300.0

    # Reset clears both timer and backoff level
    cd.reset()
    assert cd.active(now=701.0) is False
    assert cd.arm(None, now=800.0) == 60.0


def test_empty_response_tracker_only_triggers_on_consecutive_zero_token_empties():
    tracker = EmptyResponseTracker(trigger_streak=2)
    ident = ("https://api.example/v1", "test-model")

    # Non-empty or non-zero completion tokens never trigger
    assert is_zero_output_empty({"content": "hi", "tool_calls": []}, {"completion_tokens": 0}) is False
    assert is_zero_output_empty({"content": "", "tool_calls": [{"id": "1"}]}, {"completion_tokens": 0}) is False
    assert is_zero_output_empty({"content": "", "tool_calls": [], "reasoning": "thinking"}, {"completion_tokens": 0}) is False
    assert is_zero_output_empty({"content": "", "tool_calls": []}, {"completion_tokens": 12}) is False

    # 1st empty does not trigger; 2nd consecutive triggers
    assert tracker.record(ident, {"content": "", "tool_calls": []}, {"completion_tokens": 0}) is False
    assert tracker.streak == 1
    assert tracker.record(ident, {"content": "", "tool_calls": []}, {"completion_tokens": 0}) is True
    assert tracker.streak == 2

    # Different model identity resets streak
    assert tracker.record(("https://api.example/v1", "other-model"), {"content": "", "tool_calls": []}, {"completion_tokens": 0}) is False
    assert tracker.streak == 1


def _fake_completion(content: str, comp_tokens: int):
    msg = SimpleNamespace(content=content, tool_calls=[], reasoning_content=None, model_extra={})
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=comp_tokens)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


class _RateLimitError(Exception):
    def __init__(self, retry_after: str = "45"):
        super().__init__("rate limit exceeded")
        self.status_code = 429
        self.response = SimpleNamespace(status_code=429, headers={"Retry-After": retry_after})


def test_openai_model_arms_429_cooldown_and_skips_primary_ping_pong():
    cfg = Config(
        model="primary-m",
        base_url="https://api.deepseek.com/v1",
        api_key="k1",
        model_retry_attempts=1,
        model_retry_delay=0.0,
        fallback_model="fallback-m",
        fallback_base_url="https://api.deepseek.com/v1",
        fallback_api_key="k1",
    )
    primary = OpenAIModel(cfg)
    primary_calls = [0]
    fb_calls = [0]

    def fail_429(**_kw):
        primary_calls[0] += 1
        raise _RateLimitError(retry_after="30")

    class FakeFallback:
        def chat(self, messages, tools=None, cache_key=None):
            fb_calls[0] += 1
            return {"content": f"fb-{fb_calls[0]}", "tool_calls": [], "reasoning": ""}, {"prompt_tokens": 10, "completion_tokens": 5}

    primary._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail_429)))
    primary._fallback_model = FakeFallback()
    primary._fallback_identity = (cfg.fallback_base_url, cfg.fallback_model, cfg.fallback_api_key)

    # Call 1 hits primary -> 429 -> falls back and arms cooldown
    r1, _ = primary.chat([{"role": "user", "content": "hi"}])
    assert r1["content"] == "fb-1"
    assert primary_calls[0] == 1 and fb_calls[0] == 1
    assert primary._cooldown.active() is True

    # Call 2 immediately goes to fallback WITHOUT hitting primary again (no 429 ping-pong!)
    r2, _ = primary.chat([{"role": "user", "content": "next"}])
    assert r2["content"] == "fb-2"
    assert primary_calls[0] == 1 and fb_calls[0] == 2


def test_openai_model_routes_to_fallback_on_two_consecutive_empty_replies():
    cfg = Config(
        model="primary-m",
        base_url="https://api.deepseek.com/v1",
        api_key="k1",
        model_retry_attempts=1,
        model_retry_delay=0.0,
        fallback_model="fallback-m",
        fallback_base_url="https://api.deepseek.com/v1",
        fallback_api_key="k1",
    )
    primary = OpenAIModel(cfg)
    fb_calls = [0]

    class FakeFallback:
        def chat(self, messages, tools=None, cache_key=None):
            fb_calls[0] += 1
            return {"content": "rescued-by-fallback", "tool_calls": [], "reasoning": ""}, {"prompt_tokens": 10, "completion_tokens": 8}

    primary._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: _fake_completion("", 0)))
    )
    primary._fallback_model = FakeFallback()
    primary._fallback_identity = (cfg.fallback_base_url, cfg.fallback_model, cfg.fallback_api_key)

    # 1st empty returns as-is (might be transient)
    r1, _ = primary.chat([{"role": "user", "content": "one"}])
    assert r1["content"] == "" and fb_calls[0] == 0

    # 2nd consecutive 0-token empty automatically fails over to fallback!
    r2, _ = primary.chat([{"role": "user", "content": "two"}])
    assert r2["content"] == "rescued-by-fallback" and fb_calls[0] == 1


# ---------------------------------------------------------------------------
# 2. Cache-friendly batch image eviction
# ---------------------------------------------------------------------------


def _img_msg(idx: int, payload_bytes: int = 100) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"shot {idx}"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + ("A" * payload_bytes)}},
        ],
        "_origin": "owner",
    }


def test_image_eviction_noop_below_threshold_and_batch_evicts_above_without_mutating_source():
    # Below limit (12) -> exact no-op preserves prompt cache prefix
    small = [_img_msg(i, 50) for i in range(6)]
    out_small, evicted_small = evict_old_outbound_images(small, max_images=12, batch_size=6, keep_recent=2)
    assert evicted_small == 0 and out_small is small

    # Above count limit (14 images > 12) -> evicts oldest batch of 6, preserves newest 8 & never mutates original
    many = [_img_msg(i, 50) for i in range(14)]
    out_many, evicted_many = evict_old_outbound_images(many, max_images=12, batch_size=6, keep_recent=2)
    assert evicted_many == 6
    # Original history untouched!
    assert many[0]["content"][1]["type"] == "image_url"
    # First 6 replaced with placeholder, last 8 still image_url
    for i in range(6):
        assert out_many[i]["content"][1] == {"type": "text", "text": EVICTED_IMAGE_PLACEHOLDER}
    for i in range(6, 14):
        assert out_many[i]["content"][1]["type"] == "image_url"


def test_image_eviction_by_byte_budget_and_context_window_integration(tmp_path):
    # Byte budget exceeded with only 5 big images -> evicts oldest while preserving keep_recent=2
    big = [{"role": "system", "content": "rules"}] + [_img_msg(i, 5000) for i in range(5)]
    out, evicted = evict_old_outbound_images(big, max_images=12, max_bytes=12000, batch_size=2, keep_recent=2)
    assert evicted == 3
    remaining = sum(1 for m in out if isinstance(m.get("content"), list) and any(b.get("type") == "image_url" for b in m["content"]))
    assert remaining == 2

    # End-to-end through ContextWindow.prepare (15 images > default 12)
    ctx = ContextWindow(Workspace(tmp_path), max_chars=200000)
    msgs = [{"role": "system", "content": "rules"}] + [_img_msg(i, 40) for i in range(15)]
    view = ctx.prepare(msgs)
    assert ctx.metrics["evicted_images"] == 6
    # Raw history untouched
    assert msgs[1]["content"][1]["type"] == "image_url"
    assert any(
        isinstance(m.get("content"), list) and any(b.get("text") == EVICTED_IMAGE_PLACEHOLDER for b in m["content"])
        for m in view
    )


# ---------------------------------------------------------------------------
# 3. Single-flight TTL web cache
# ---------------------------------------------------------------------------


def test_single_flight_ttl_cache_coalesces_concurrent_requests_and_skips_errors():
    cache = SingleFlightTTLCache(ttl_seconds=60.0, max_entries=16)
    calls = [0]
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    def slow_fetch():
        calls[0] += 1
        entered.set()
        release.wait(timeout=3.0)
        return {"ok": True, "val": calls[0]}

    results: list[tuple[dict, bool]] = []
    lock = threading.Lock()

    def worker(is_second: bool):
        if is_second:
            entered.wait(timeout=3.0)
            second_started.set()
        res = cache.get_or_fetch(("k",), slow_fetch)
        with lock:
            results.append(res)

    t1 = threading.Thread(target=worker, args=(False,))
    t2 = threading.Thread(target=worker, args=(True,))
    t1.start()
    t2.start()
    second_started.wait(timeout=3.0)
    # Wait until t2 is blocked on the per-key flight lock, then release t1 cleanly
    release.set()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    # Exactly ONE fetch executed; both threads got identical value, one was a cache hit
    assert calls[0] == 1
    assert len(results) == 2
    assert {r[1] for r in results} == {False, True}
    assert all(r[0]["val"] == 1 for r in results)

    # Errors are NEVER cached
    err_calls = [0]

    def failing():
        err_calls[0] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        cache.get_or_fetch(("err",), failing)
    with pytest.raises(RuntimeError):
        cache.get_or_fetch(("err",), failing)
    assert err_calls[0] == 2


def test_web_fetch_and_web_search_reuse_ttl_cache(tmp_path, monkeypatch):
    clear_web_cache()
    ws = Workspace(tmp_path)
    get_calls = [0]

    def fake_get(url, accept="text/html", api_only=False):
        get_calls[0] += 1
        if "duckduckgo.com" in url:
            body = b'<a class="result__a" href="https://example.org/docs">Example Docs</a><span class="result__snippet">Fast docs</span>'
            return net.Response(url, body, {"content-type": "text/html"})
        return net.Response(url, b"<html><body><p>Hello cached world!</p></body></html>", {"content-type": "text/html"})

    monkeypatch.setattr(net, "_get", fake_get)

    # Repeated web_fetch hits cache (only 1 _get call!)
    f1 = net.web_fetch(ws, "https://example.org/page")
    f2 = net.web_fetch(ws, "https://example.org/page")
    assert "Hello cached world!" in f1 and f1 == f2
    assert get_calls[0] == 1

    # Repeated web_search with whitespace/case variation hits cache (only 1 additional _get call!)
    s1 = net.web_search(ws, "  Python   Caching ", max_results=1)
    s2 = net.web_search(ws, "python caching", max_results=1)
    assert "Example Docs" in s1 and "Example Docs" in s2
    assert get_calls[0] == 2


# ---------------------------------------------------------------------------
# 4. Unicode punctuation normalization fallback in edit_file
# ---------------------------------------------------------------------------


def test_apply_edits_unicode_punctuation_fallback_and_ambiguity_guard():
    # File has curly quotes, em-dash, and NBSP; LLM sends plain ASCII quotes/dashes/spaces
    original = "def greet():\n    return \u201cHello\u00a0world\u2014ready\u201d\n"
    updated = apply_edits(original, [{"search": '    return "Hello world--ready"', "replace": '    return "Updated!"'}])
    assert '    return "Updated!"' in updated

    # Reverse: file has ASCII quotes, LLM emits smart quotes
    ascii_src = "msg = 'don't panic'\n"
    updated2 = apply_edits(ascii_src, [{"search": "msg = \u2018don\u2019t panic\u2019", "replace": "msg = 'ok'"}])
    assert updated2 == "msg = 'ok'\n"

    # Ambiguous Unicode match must STILL be rejected safely!
    dup = "x = \u201chi\u201d\nx = \u201chi\u201d\n"
    with pytest.raises(EditError, match="2 φορές"):
        apply_edits(dup, [{"search": 'x = "hi"', "replace": 'x = "bye"'}])
