"""Runtime interruption reports must reach the next owner turn and saved session."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import OpenAIModel, ScriptedModel, tool_call
from akatikito.sessions import SessionStore
from akatikito import memory


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DIR', tmp_path / 'private-memory')
    def forbidden(*a, **kw):
        raise AssertionError('A real model request is forbidden')
    monkeypatch.setattr(OpenAIModel, 'chat', forbidden)
    (tmp_path / 'code.py').write_text('value = 42\n', encoding='utf-8')
    repeated = {'tool_calls': [tool_call('read_file', path='code.py')]}
    result = Akatikito(str(tmp_path), Config(auto_mode=False, max_steps=20), ScriptedModel([repeated]))
    result.set_mode('code')
    return result


def test_loop_report_is_saved_and_visible_to_next_question(agent):
    answer = agent.chat('Πρόσθεσε τις δύο αλλαγές που επέλεξα.')
    assert agent.activity.stalled and agent.model.calls == 5
    assert 'Δεν ολοκληρώθηκε' in answer and 'Δεν έγινε αλλαγή αρχείου' in answer
    assert agent.messages[-1] == {'role': 'assistant', 'content': answer}
    agent.model = ScriptedModel([{'content': 'Η προηγούμενη εργασία σταμάτησε χωρίς αλλαγή.'}])
    agent.chat('Διόρθωσε αυτό το λάθος πρώτα· βρες την αιτία.')
    request = agent.model.seen[0]
    assert any(m.get('role') == 'assistant' and m.get('content') == answer for m in request)
    assert agent.model.calls == 1  # No hidden recovery calls added by this fix.


def test_limit_report_survives_session_roundtrip(agent):
    agent.config.max_steps = 1
    answer = agent.chat('Διάβασε και ολοκλήρωσε τη δουλειά.')
    assert 'όριο των 1' in answer and agent.messages[-1]['content'] == answer
    store = SessionStore(agent.ws.root)
    sid = store.create()
    store.save(sid, messages=agent.messages)
    restored = store.load(sid)['messages']
    assert restored[-1]['content'] == answer and restored[-1]['role'] == 'assistant'
    assert sum(m.get('content') == answer for m in restored) == 1


def test_normal_answer_is_not_duplicated_or_marked_as_interruption(agent):
    agent.model = ScriptedModel([{'content': 'Απλή απάντηση.'}])
    assert agent.chat('Πες μου μια απλή απάντηση.') == 'Απλή απάντηση.'
    replies = [m for m in agent.messages if m['role'] == 'assistant']
    assert len(replies) == 1 and replies[0]['content'] == 'Απλή απάντηση.'


def test_passing_baseline_check_does_not_hide_incomplete_work(agent):
    (agent.ws.root / 'check.py').write_text('print("BASELINE PASSED")\n', encoding='utf-8')
    agent.config.verify_command = f'"{sys.executable}" -B check.py'
    agent.config.max_steps = 1
    agent.model = ScriptedModel([{'tool_calls': [tool_call('run_command', command=agent.config.verify_command)]}])
    answer = agent.chat('Υλοποίησε το ζητούμενο χαρακτηριστικό.')
    assert 'Ο τελευταίος έλεγχος πέρασε' in answer
    assert 'Δεν ολοκληρώθηκε' in answer and 'Δεν έγινε αλλαγή αρχείου' in answer
    assert agent.messages[-1]['content'] == answer
