"""Comprehensive tests for the 4 final v2.6 gems:
1. Anti-blind-overwrite guard on write_file(overwrite=True) (activity.py)
2. /context token breakdown (context_breakdown.py + slash_commands.py)
3. /usage cost & usage insights (usage_insights.py + slash_commands.py)
4. Conservative escape-drift auto-correct in edit_file (editing.py)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context_breakdown import compute_context_breakdown, format_context_breakdown
from akatikito.editing import EditError, apply_edits
from akatikito.model import ScriptedModel, tool_call
from akatikito.usage_insights import build_usage_insights, format_usage_insights
from akatikito.usage_ledger import UsageLedger


# ---------------------------------------------------------------------------
# 1. Anti-blind-overwrite guard
# ---------------------------------------------------------------------------


def test_blind_overwrite_blocked_until_read_and_reblocked_if_file_changes_externally(tmp_path):
    target = tmp_path / "existing.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    # 1a. Blind overwrite WITHOUT reading -> blocked, original file untouched!
    model1 = ScriptedModel([
        {"tool_calls": [tool_call("write_file", path="existing.py", content="VALUE = 999\n", overwrite=True)]},
        {"content": "blocked"},
    ])
    ag1 = Akatikito(str(tmp_path), Config(auto_mode=False, verify_command=" "), model1)
    ag1.set_mode("code")
    ag1.chat("Overwrite existing.py")
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    tool_msgs = [m["content"] for m in ag1.messages if m.get("role") == "tool"]
    assert any("δεν έχει διαβαστεί" in txt for txt in tool_msgs)

    # 1b. Read first, then overwrite -> succeeds cleanly!
    model2 = ScriptedModel([
        {"tool_calls": [tool_call("read_file", path="existing.py")]},
        {"tool_calls": [tool_call("write_file", path="existing.py", content="VALUE = 2\n", overwrite=True)]},
        {"content": "updated"},
    ])
    ag2 = Akatikito(str(tmp_path), Config(auto_mode=False, verify_command=" "), model2)
    ag2.set_mode("code")
    ag2.chat("Read and update existing.py")
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"

    # 1c. Brand-new file creation (doesn't exist yet) is NEVER blocked
    model3 = ScriptedModel([
        {"tool_calls": [tool_call("write_file", path="brand_new.py", content="X = 1\n")]},
        {"content": "created"},
    ])
    ag3 = Akatikito(str(tmp_path), Config(auto_mode=False, verify_command=" "), model3)
    ag3.set_mode("code")
    ag3.chat("Create brand_new.py")
    assert (tmp_path / "brand_new.py").read_text(encoding="utf-8") == "X = 1\n"


def test_stale_overwrite_blocked_when_file_changed_on_disk_after_read(tmp_path):
    target = tmp_path / "live.py"
    target.write_text("A = 1\n", encoding="utf-8")
    ag = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    ag.activity.record("read_file", {"path": "live.py"}, "    1 | A = 1")
    # Same version -> allowed
    assert ag.activity.before("write_file", {"path": "live.py", "overwrite": True}) == ""
    # External change alters size/mtime -> blocked!
    target.write_text("A = 1\nB = 2  # user edited on disk\n", encoding="utf-8")
    blocked = ag.activity.before("write_file", {"path": "live.py", "overwrite": True})
    assert "δεν έχει διαβαστεί" in blocked


# ---------------------------------------------------------------------------
# 2. /context token breakdown
# ---------------------------------------------------------------------------


def test_context_slash_command_returns_breakdown_with_zero_model_calls(tmp_path):
    (tmp_path / "SPEC.md").write_text("PINNED_SPEC_CONTENT\n" * 40, encoding="utf-8")
    seen_calls = []

    class _Spy(ScriptedModel):
        def chat(self, messages, tools=None, cache_key=None):
            seen_calls.append(messages)
            return super().chat(messages, tools, cache_key)

    ag = Akatikito(str(tmp_path), Config(auto_mode=False), _Spy([]))
    ag.context.native_context_tokens = 128_000
    ag._last_prompt_tokens = 96_000  # 75% -> should show /compact hint
    ag.messages = [
        {"role": "system", "content": "System rules " * 50},
        {"role": "user", "content": "Large user log " * 300, "_origin": "owner"},
        {"role": "assistant", "content": "Short reply"},
    ]
    ag.chat("/pin SPEC.md")

    report = ag.chat("/context")
    assert len(seen_calls) == 0
    assert "Ανάλυση Context" in report
    assert "75.0%" in report
    assert "Καρφιτσωμένα (1)" in report
    assert "/compact" in report

    data = compute_context_breakdown(ag)
    assert data["pins_tokens"] > 0
    assert data["conversation_tokens"] > 0
    assert len(data["heaviest"]) >= 1
    assert "Ανάλυση Context" in format_context_breakdown(ag)


# ---------------------------------------------------------------------------
# 3. /usage insights
# ---------------------------------------------------------------------------


def test_usage_insights_aggregates_windows_and_preserves_subcent_precision(tmp_path):
    ledger_file = tmp_path / "usage.json"
    ledger = UsageLedger(ledger_file)
    ref = date(2026, 11, 27)

    # Today: sub-cent live cost ($0.0028) + high cache hit
    ledger.record(
        "s1",
        {"prompt_tokens": 10000, "completion_tokens": 500, "cache_hit_tokens": 8000, "provider_cost": 0.0028},
        day=ref.isoformat(),
        model="particle-1",
        project="demo-app",
    )
    # 5 days ago (inside 7d & 30d)
    ledger.record(
        "s2",
        {"prompt_tokens": 20000, "completion_tokens": 1000, "cache_hit_tokens": 10000, "provider_cost": 0.0100},
        day=(ref - timedelta(days=5)).isoformat(),
        model="deepseek-chat",
        project="demo-app",
    )
    # 20 days ago (inside 30d only)
    ledger.record(
        "s3",
        {"prompt_tokens": 5000, "completion_tokens": 200, "cache_hit_tokens": 0, "provider_cost": 0.0050},
        day=(ref - timedelta(days=20)).isoformat(),
        model="deepseek-chat",
        project="other-proj",
    )

    rep = build_usage_insights(ledger_path=ledger_file, today=ref)
    assert rep["today"]["cost"] == pytest.approx(0.0028)
    assert rep["today"]["cache_hit_pct"] == 80.0
    assert rep["days_7"]["cost"] == pytest.approx(0.0128)
    assert rep["days_30"]["cost"] == pytest.approx(0.0178)

    text = format_usage_insights(
        ledger_path=ledger_file,
        session_tokens={"prompt_tokens": 1000, "completion_tokens": 100, "cache_hit_tokens": 700, "provider_cost": 0.0012},
        today=ref,
    )
    assert "$0.0028" in text  # 4-decimal sub-cent precision (never "$0.00")
    assert "$0.0128" in text
    assert "$0.0178" in text
    assert "particle-1" in text
    assert "demo-app" in text

    # /usage slash command executes with 0 LLM tokens
    ag = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    slash_out = ag.chat("/usage")
    assert "Αναφορά Κόστους & Χρήσης" in slash_out and "0 tokens" in slash_out


# ---------------------------------------------------------------------------
# 4. Escape-drift auto-correct in edit_file
# ---------------------------------------------------------------------------


def test_escape_drift_halves_doubled_backslashes_safely_and_rejects_ambiguous_replace():
    # File has single-backslash regex/Windows path; LLM doubles backslashes in search, replace is backslash-free
    src = r'PAT = r"\d+_\w+"' + "\n"
    updated = apply_edits(src, [{"search": r'PAT = r"\\d+_\\w+"', "replace": 'PAT = "digits"'}])
    assert updated == 'PAT = "digits"\n'

    # If replacement ALSO has backslashes, refuse loudly instead of guessing!
    with pytest.raises(EditError, match="διπλό escaping"):
        apply_edits(src, [{"search": r'PAT = r"\\d+_\\w+"', "replace": r'PAT = r"\\s+"'}])

    # Odd backslash runs are NOT halved (normal not-found error)
    with pytest.raises(EditError, match="δεν βρέθηκε"):
        apply_edits(src, [{"search": r'PAT = r"\\\d+"', "replace": 'PAT = "x"'}])
