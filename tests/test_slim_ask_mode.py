"""Tests for Slim ask mode: compact prompt, zero dead state, core tools + session-sticky topic unlock."""
from __future__ import annotations

import json
from pathlib import Path

from akatikito import modes
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel


class _SpyModel(ScriptedModel):
    def __init__(self, replies):
        super().__init__(replies)
        self.sent: list[tuple[list[dict], list[dict]]] = []

    def chat(self, messages, tools=None, cache_key=None):
        self.sent.append((list(messages), list(tools or [])))
        return super().chat(messages, tools, cache_key)


def test_slim_ask_mode_cuts_prompt_state_and_tools_on_simple_question(tmp_path):
    (tmp_path / "RULES.md").write_text("KEEP ASK RULE 42", encoding="utf-8")
    spy = _SpyModel([{"content": "Σύντομη απάντηση."}])
    ag = Akatikito(str(tmp_path), Config(verify_command=" "), spy)

    ans = ag.chat("Τι είναι το REST API;")
    assert ans == "Σύντομη απάντηση."
    assert ag.mode == "ask"
    assert len(spy.sent) == 1

    msgs, tls = spy.sent[0]
    sys_text = msgs[0]["content"]
    assert "KEEP ASK RULE 42" in sys_text
    assert len(sys_text) < 2000
    assert "record_diagnosis" not in sys_text
    assert "propose_skill" not in sys_text

    # No dead-weight internal state messages injected on empty ask turn
    keys = {m.get("_context_key") for m in msgs if m.get("_context_key")}
    assert "λίστα εργασιών" not in keys
    assert "διαθέσιμες συνταγές" not in keys
    assert "επαναλαμβανόμενες διαδικασίες" not in keys

    # Only core 10 read-only tools advertised
    tool_names = {t["function"]["name"] for t in tls}
    assert tool_names == set(modes.ASK_CORE_TOOLS)
    assert len(json.dumps(tls, ensure_ascii=False)) < 5500


def test_ask_mode_excludes_orchestration_and_write_tools_while_other_modes_keep_them():
    ask_allowed = set(modes.allowed_tools("ask"))
    for removed in ("complete_task", "run_subtasks", "file_operation_history", "git_status_diff", "use_skill"):
        assert removed not in ask_allowed
    assert "run_subtask" not in modes.ASK_CORE_TOOLS

    # Other modes retain full capabilities untouched
    for mode_name in ("architect", "code", "debug", "assistant"):
        allowed = set(modes.allowed_tools(mode_name))
        assert {"complete_task", "run_subtask", "run_subtasks", "use_skill"} <= allowed
    for mode_name in ("code", "debug", "assistant"):
        assert "file_operation_history" in modes.allowed_tools(mode_name)


def test_ask_mode_unlocks_topic_tools_on_demand_and_stays_cache_sticky(tmp_path):
    # 1. Simple multi-turn ask keeps identical tools across turns (prompt cache friendly)
    spy1 = _SpyModel([{"content": "α1"}, {"content": "α2"}])
    ag1 = Akatikito(str(tmp_path), Config(verify_command=" "), spy1)
    ag1.chat("Τι είναι το Docker;")
    ag1.chat("Πώς δουλεύει ένα container;")
    assert ag1.mode == "ask"
    assert spy1.sent[0][1] == spy1.sent[1][1]

    # 2. Topic-specific question unlocks specialist read tools and stays sticky on follow-up
    spy2 = _SpyModel([{"content": "β1"}, {"content": "β2"}, {"content": "β3"}])
    ag2 = Akatikito(str(tmp_path), Config(verify_command=" "), spy2)
    ag2.chat("Διάβασε το README του Chromium στο GitHub")
    names_t1 = {t["function"]["name"] for t in spy2.sent[0][1]}
    assert {"github_read", "github_issues", "github_search"} <= names_t1
    assert "youtube_transcript" not in names_t1

    # Follow-up keeps identical tool list for KV-cache hits
    ag2.chat("Τι άλλο γράφει;")
    assert spy2.sent[0][1] == spy2.sent[1][1]

    # Reset clears unlocked specialist tools back to core 10
    ag2.reset()
    ag2.chat("Τι είναι το HTTP;")
    names_after_reset = {t["function"]["name"] for t in spy2.sent[2][1]}
    assert names_after_reset == set(modes.ASK_CORE_TOOLS)


def test_select_ask_tools_triggers_and_preserves_custom_mcp_tools():
    allowed = set(modes.allowed_tools("ask")) | {"mcp_custom_lookup"}

    # Custom MCP/third-party tools in ask are always preserved
    base = modes.select_ask_tools("Τι είναι το JSON;", allowed, set())
    assert "mcp_custom_lookup" in base
    assert "calculate_expression" not in base

    # Calculator trigger
    calc = modes.select_ask_tools("Πόσο κάνει 1850 + 24% ΦΠΑ;", allowed, set())
    assert "calculate_expression" in calc

    # Reminders trigger
    rem = modes.select_ask_tools("Τι ραντεβού έχω αύριο;", allowed, set())
    assert "list_reminders" in rem

    # YouTube + URL trigger
    yt = modes.select_ask_tools("Τι λέει το βίντεο https://youtu.be/xyz;", allowed, set())
    assert {"youtube_transcript", "browser_fetch"} <= yt

    # Diagnostics trigger
    diag = modes.select_ask_tools("Πόση RAM και CPU έχω;", allowed, set())
    assert "system_diagnostics" in diag

    # Past sessions trigger
    past = modes.select_ask_tools("Τι συζητήσαμε χθες;", allowed, set())
    assert "search_past_sessions" in past
