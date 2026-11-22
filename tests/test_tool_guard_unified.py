"""Ο ενιαίος έλεγχος εργαλείων δίνει σταθερό μήνυμα και σέβεται το set_mode."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import akatikito.agent as engine
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel


def test_unified_tool_guard_message_is_consistent(tmp_path):
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), ScriptedModel([]))
    agent.set_mode('ask')
    ask_tools = set(engine.modes.allowed_tools('ask'))
    blocked = agent._tool_guard_error('edit_file', ask_tools)
    assert blocked is not None and 'δεν επιτρέπεται' in blocked
    assert agent._tool_guard_error('set_mode', ask_tools, allow_mode_change=True) is None
