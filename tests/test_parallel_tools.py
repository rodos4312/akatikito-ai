"""Concurrency is proved by synchronization barriers, not elapsed-time guesses."""
import json
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import akatikito.agent as engine
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.model import ScriptedModel, tool_call
from akatikito.workspace import Workspace
from akatikito import memory


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DIR', tmp_path / 'memory')
    monkeypatch.setattr(engine.OpenAIModel, 'chat', lambda *a, **kw: pytest.fail('Real model request forbidden'))
    monkeypatch.setattr(engine.OpenAIModel, 'chat_stream', lambda *a, **kw: pytest.fail('Real model request forbidden'))


def agent_for(tmp_path, calls, workers=4):
    for name in ('a.py', 'b.py'):
        (tmp_path / name).write_text('value = 42\n', encoding='utf-8')
    agent = Akatikito(str(tmp_path), Config(auto_mode=False, parallel_tool_workers=workers),
                     ScriptedModel([{'tool_calls': calls}, {'content': 'done'}]))
    agent.set_mode('code')
    return agent


def tool_messages(agent):
    return [m for m in agent.messages if m['role'] == 'tool']


def test_reads_overlap_but_results_and_events_stay_in_request_order(tmp_path, monkeypatch):
    agent = agent_for(tmp_path, [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py')])
    barrier = threading.Barrier(2)
    second_done = threading.Event()
    threads = set()
    completed = []
    original = engine.dispatch
    def dispatch(ws, name, args):
        threads.add(threading.get_ident())
        barrier.wait(timeout=3)
        if args['path'] == 'a.py':
            assert second_done.wait(3)
        result = original(ws, name, args)
        completed.append(args['path'])
        if args['path'] == 'b.py':
            second_done.set()
        return result
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    event_threads = []
    agent.events.subscribe(lambda e: event_threads.append(threading.get_ident()))
    assert agent.chat('Διάβασε τα δύο ανεξάρτητα αρχεία.') == 'done'
    assert len(threads) == 2 and threading.get_ident() not in threads
    assert completed == ['b.py', 'a.py']
    results = tool_messages(agent)
    assert [m['tool_call_id'] for m in results] == ['call_0', 'call_1']
    assert results[0]['content'].startswith('a.py') and results[1]['content'].startswith('b.py')
    assert set(event_threads) == {threading.get_ident()}
    assert {'a.py', 'b.py'} <= agent.repair.read_paths


def test_write_is_a_barrier_and_following_read_sees_written_data(tmp_path, monkeypatch):
    calls = [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py'),
             tool_call('write_file', path='new.txt', content='new data'), tool_call('read_file', path='new.txt')]
    agent = agent_for(tmp_path, calls)
    gate = threading.Barrier(2)
    completed = set()
    original = engine.dispatch
    main = threading.get_ident()
    def dispatch(ws, name, args):
        if name == 'read_file' and args['path'] in ('a.py', 'b.py'):
            gate.wait(3)
            result = original(ws, name, args)
            completed.add(args['path'])
            return result
        assert completed == {'a.py', 'b.py'}
        assert threading.get_ident() == main
        return original(ws, name, args)
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    assert agent.chat('Διάβασε, γράψε και ξαναδιάβασε.') == 'done'
    assert 'new data' in tool_messages(agent)[-1]['content']


def test_duplicate_alias_reads_cannot_bypass_guard_in_one_batch(tmp_path, monkeypatch):
    calls = [tool_call('read_file', path=p) for p in ('a.py', './a.py', str(tmp_path / 'a.py'))]
    agent = agent_for(tmp_path, calls)
    original = engine.dispatch
    count = []
    def dispatch(*args):
        count.append(1)
        return original(*args)
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    assert agent.chat('Διάβασε.') == 'done'
    assert len(count) == 2
    assert tool_messages(agent)[-1]['content'].startswith('ΑΠΟΤΡΟΠΗ')


def test_worker_bound_is_honored(tmp_path, monkeypatch):
    calls = [tool_call('web_fetch', url=f'https://example.com/{n}') for n in range(5)]
    agent = agent_for(tmp_path, calls, workers=2)
    gate = threading.Barrier(2)
    active = 0
    maximum = 0
    lock = threading.Lock()
    def dispatch(ws, name, args):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
        if not args['url'].endswith('/4'):
            gate.wait(3)
        with lock:
            active -= 1
        return args['url']
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    assert agent.chat('Διάβασε πέντε σελίδες.') == 'done'
    assert maximum == 2
    assert [m['content'] for m in tool_messages(agent)] == [f'https://example.com/{n}' for n in range(5)]


def test_mode_switch_remains_a_barrier_and_denies_later_write(tmp_path):
    calls = [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py'),
             tool_call('set_mode', mode='ask'), tool_call('write_file', path='blocked.txt', content='x')]
    agent = agent_for(tmp_path, calls)
    assert agent.chat('Διάβασε και άλλαξε σε ερώτηση.') == 'done'
    assert agent.mode == 'ask' and not (tmp_path / 'blocked.txt').exists()
    assert 'δεν επιτρέπεται' in tool_messages(agent)[-1]['content']


def test_worker_exception_keeps_all_tool_response_ids(tmp_path, monkeypatch):
    agent = agent_for(tmp_path, [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py')])
    def dispatch(ws, name, args):
        if args['path'] == 'a.py':
            raise OSError('synthetic read failure')
        return 'b source'
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    assert agent.chat('Διάβασε.') == 'done'
    results = tool_messages(agent)
    assert len(results) == 2 and 'synthetic read failure' in results[0]['content']
    assert results[1]['content'] == 'b source'


def test_stop_after_reads_cancels_later_write_without_losing_protocol(tmp_path):
    calls = [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py'),
             tool_call('write_file', path='blocked.txt', content='x')]
    agent = agent_for(tmp_path, calls)
    agent.events.subscribe(lambda e: agent.stop() if e.kind == 'tool_result' else None)
    assert agent.chat('Διάβασε και γράψε.') == 'Σταμάτησα όπως ζήτησες.'
    assert not (tmp_path / 'blocked.txt').exists() and agent.model.calls == 1
    assert len(tool_messages(agent)) == 3


def test_parallel_archive_of_same_text_stays_exact_and_atomic(tmp_path):
    context = ContextWindow(Workspace(tmp_path))
    text = 'important source\n' * 3000
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(context.store, [text] * 48))
    assert len(set(keys)) == 1 and context._load(keys[0]) == text
    assert len(list(context.folder.glob('*.json'))) == 1
    assert not list(context.folder.glob('*.tmp'))


def test_single_worker_retains_serial_compatibility(tmp_path, monkeypatch):
    agent = agent_for(tmp_path, [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py')], workers=1)
    threads = []
    original = engine.dispatch
    def dispatch(*args):
        threads.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    assert agent.chat('Διάβασε.') == 'done'
    assert threads == [threading.get_ident()] * 2



def test_verification_command_waits_for_parallel_reads_and_runs_on_caller(tmp_path, monkeypatch):
    calls = [tool_call('read_file', path='a.py'), tool_call('read_file', path='b.py'), tool_call('run_command', command='verify')]
    agent = agent_for(tmp_path, calls)
    agent.config.verify_command = 'verify'
    gate = threading.Barrier(2)
    finished = set()
    original = engine.dispatch
    main = threading.get_ident()
    def dispatch(ws, name, args):
        if name == 'run_command':
            assert finished == {'a.py', 'b.py'} and threading.get_ident() == main
            return 'returncode=0\nTESTS PASSED'
        gate.wait(3)
        result = original(ws, name, args)
        finished.add(args['path'])
        return result
    monkeypatch.setattr(engine, 'dispatch', dispatch)
    assert agent.chat('Διάβασε και έλεγξε.') == 'done'
    assert [e.data['ok'] for e in agent.events.log if e.kind == 'verify'] == [True]
