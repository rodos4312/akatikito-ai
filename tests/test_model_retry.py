"""Έλεγχοι για retry/backoff στο OpenAIModel."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from akatikito.config import Config
from akatikito.model import ModelError, OpenAIModel


class _RetryableError(Exception):
    """Σφάλμα με status_code ώστε να κρίνεται retryable."""

    def __init__(self, msg: str, status: int):
        super().__init__(msg)
        self.status_code = status


class _FlakyCompletions:
    def __init__(self, client):
        self.client = client

    def create(self, **kwargs):
        self.client.calls += 1
        if self.client.calls <= self.client.fails:
            if self.client.status:
                raise _RetryableError(f"simulated failure {self.client.calls}", self.client.status)
            raise Exception(f"simulated failure {self.client.calls}")
        return _fake_response()


class FlakyClient:
    """Προσομοίωση πελάτη που αποτυγχάνει τις πρώτες N κλήσεις."""

    def __init__(self, fails: int = 0, status: int | None = None) -> None:
        self.fails = fails
        self.status = status
        self.calls = 0
        self.chat = type("Chat", (), {"completions": _FlakyCompletions(self)})()


def _fake_response():
    class Choice:
        message = type("Message", (), {"content": "ok", "tool_calls": None, "reasoning_content": ""})()

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5
        prompt_cache_hit_tokens = 0
        prompt_cache_miss_tokens = 0

    return type("Response", (), {"choices": [Choice()], "usage": Usage()})()


def main():
    cfg = Config(model="test", base_url="http://x", api_key="k", model_retry_attempts=3, model_retry_delay=0)
    model = OpenAIModel(cfg)
    assert model._client.max_retries == 0, "Το SDK δεν πρέπει να πολλαπλασιάζει τα retries του agent"
    print("OK retries owned by one layer")

    # 1. Επιτυχία από την πρώτη φορά.
    model._client = FlakyClient(fails=0)
    reply, usage = model.chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "ok"
    assert model._client.calls == 1
    print("OK immediate success")

    # 2. Επιτυχία μετά από 2 αποτυχίες (status 503 -> retryable).
    model._client = FlakyClient(fails=2, status=503)
    reply, usage = model.chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "ok"
    assert model._client.calls == 3
    print("OK retry recovers from 503")

    # 3. Αποτυχία μετά από εξάντληση retries.
    model._client = FlakyClient(fails=5, status=503)
    try:
        model.chat([{"role": "user", "content": "hi"}])
    except ModelError as exc:
        assert "simulated failure" in str(exc)
        assert model._client.calls == 3
        print("OK gives up after max retries")
    else:
        raise AssertionError("expected ModelError")

    # 4. Μη-retryable σφάλμα (π.χ. 400) αποτυγχάνει αμέσως.
    model._client = FlakyClient(fails=5, status=400)
    try:
        model.chat([{"role": "user", "content": "hi"}])
    except ModelError:
        assert model._client.calls == 1
        print("OK non-retryable error fails immediately")
    else:
        raise AssertionError("expected ModelError")

    # 5. Το προσωρινό rate limit ανακάμπτει μέσα στο ίδιο όριο.
    model._client = FlakyClient(fails=1, status=429)
    reply, usage = model.chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "ok" and model._client.calls == 2
    assert usage["prompt_tokens"] == 10 and usage["completion_tokens"] == 5
    print("OK rate-limit recovery preserves successful response and usage")

    print("MODEL RETRY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
