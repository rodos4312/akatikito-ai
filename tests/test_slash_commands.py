"""Tests for developer slash commands (/diff, /test, /review, /pin, /unpin, /pins)."""
from __future__ import annotations

import shutil
import subprocess
import sys

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel


def test_diff_and_test_slash_commands_run_with_zero_model_tokens(tmp_path):
    seen_calls: list[list[dict]] = []

    class _SpyModel(ScriptedModel):
        def chat(self, messages, tools=None, cache_key=None):
            seen_calls.append(list(messages))
            return super().chat(messages, tools, cache_key)

    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    git = shutil.which("git")
    if git:
        subprocess.run([git, "init"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run([git, "config", "user.email", "t@example.com"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run([git, "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run([git, "add", "hello.py"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run([git, "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=False)
        (tmp_path / "hello.py").write_text("x = 2\n", encoding="utf-8")

    verify_ok = f'"{sys.executable}" -c "print(\'ALL_GREEN_42\')"'
    agent = Akatikito(str(tmp_path), Config(auto_mode=False, verify_command=verify_ok), _SpyModel([]))

    # /diff executes locally with 0 LLM calls
    diff_out = agent.chat("/diff")
    assert "Git Diff" in diff_out and "0 tokens" in diff_out
    if git:
        assert "hello.py" in diff_out

    # /test runs verify_command locally with 0 LLM calls
    test_out = agent.chat("/test")
    assert "Tests πέρασαν" in test_out and "ALL_GREEN_42" in test_out and "0 tokens" in test_out
    assert len(seen_calls) == 0

    # /test respects owner command_guard denial
    agent.ws.command_guard = lambda cmd, risk: False
    denied_out = agent.chat("/test")
    assert "OWNER_DENIED" in denied_out and "Tests απέτυχαν" in denied_out
    assert len(seen_calls) == 0


def test_review_slash_command_expands_to_structured_review_prompt(tmp_path):
    seen_calls: list[list[dict]] = []

    class _SpyModel(ScriptedModel):
        def chat(self, messages, tools=None, cache_key=None):
            seen_calls.append(list(messages))
            return super().chat(messages, tools, cache_key)

    agent = Akatikito(str(tmp_path), Config(auto_mode=False), _SpyModel([{"content": "Review OK"}]))
    ans = agent.chat("/review auth.py")
    assert ans == "Review OK"
    assert len(seen_calls) == 1
    user_blob = "\n".join(str(m.get("content", "")) for m in seen_calls[0] if m.get("role") == "user")
    assert "CODE REVIEW /review" in user_blob
    assert "auth.py" in user_blob
    assert "git_status_diff" in user_blob
