"""Tests for token economy, stability and reliability improvements."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.tools import run_command
from akatikito.workspace import Workspace


def test_system_prompt_cap():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        # Γεμίζουμε τη μνήμη του project για να φουσκώσει το prompt.
        huge = "\n".join(f"σημείωση {i}: αυτό είναι ένα μεγάλο κείμενο για testing." * 20 for i in range(200))
        ws.memory_path().write_text(huge, encoding="utf-8")
        agent = Akatikito(tmp, config=Config(), model=ScriptedModel([{"content":"ok"}]))
        prompt = agent.system_prompt()
        assert len(prompt) <= 12000, f"prompt length {len(prompt)} exceeds cap"
        assert "Λειτουργία" in prompt
        assert "Project:" in prompt


def test_run_command_invalid_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        result = run_command(ws, "echo ok", timeout="not-a-number")
        assert result.startswith("returncode=0"), result
        assert "ok" in result


def test_run_command_negative_timeout_uses_default():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        result = run_command(ws, "echo ok", timeout=-5)
        assert result.startswith("returncode=0"), result


def test_model_retry_delay_capped(monkeypatch):
    """Το backoff delay δεν ξεπερνά τα 30s για να αποφεύγονται ατέρμονες αναμονές."""
    from akatikito import model as model_module

    calls = []
    original_sleep = model_module.time.sleep

    def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(model_module.time, "sleep", fake_sleep)

    class Boom(Exception):
        def __init__(self):
            self.status_code = 503

    config = Config(api_key="x", model_retry_attempts=5, model_retry_delay=10.0, model_retry_backoff=10.0)
    m = model_module.OpenAIModel(config)

    def fail(*args, **kwargs):
        raise Boom()

    monkeypatch.setattr(m._client.chat.completions, "create", fail)
    try:
        m.chat([{"role": "user", "content": "hi"}])
    except model_module.ModelError:
        pass
    assert calls, "expected retries"
    assert max(calls) <= 30.0, f"delay exceeded cap: {calls}"


def test_max_output_clip():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        # Δημιουργούμε μεγάλη έξοδο που θα κοπεί.
        big = "x" * 20000
        result = run_command(ws, 'python -c "print(\'x\' * 20000)"')
        assert result.startswith("returncode=0")
        assert "κόπηκε" in result or len(result) < len(big)
