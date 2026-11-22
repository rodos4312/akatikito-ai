"""Ο βρόχος chat εκπέμπει ορατά βήματα φάσης: plan/act/verify/report."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import akatikito.agent as engine
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call
from akatikito import memory


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DIR', tmp_path / 'memory')
    monkeypatch.setattr(engine.OpenAIModel, 'chat', lambda *a, **kw: pytest.fail('Real model request forbidden'))
    monkeypatch.setattr(engine.OpenAIModel, 'chat_stream', lambda *a, **kw: pytest.fail('Real model request forbidden'))


def test_chat_emits_phase_steps(tmp_path):
    (tmp_path / 'a.py').write_text('value = 42\n', encoding='utf-8')
    agent = Akatikito(str(tmp_path), Config(auto_mode=False),
                      ScriptedModel([{'tool_calls': [tool_call('read_file', path='a.py')]}, {'content': 'done'}]))
    agent.set_mode('code')
    agent.chat('Διάβασε το a.py.')
    kinds = [e.kind for e in agent.events.log]
    assert 'plan_step' in kinds
    assert 'act_step' in kinds
    assert 'report_step' in kinds
