"""Cache contracts use the real adapter and local recording transport; no provider requests."""
import copy
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow, encoded
from akatikito.model import OpenAIModel, tool_call
from akatikito.sessions import SessionStore
from akatikito.workspace import Workspace
from akatikito import memory


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DIR', tmp_path / 'memory')
    def deny(*args, **kwargs):
        raise AssertionError('A cache contract test attempted a network connection')
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(socket.socket, 'connect', deny)


def adapter(root, script, *, thinking=False, stream=False, verify='', cap=0):
    sent = []
    items = iter(script)
    def create(**kwargs):
        sent.append(copy.deepcopy(kwargs))
        item = next(items)
        if callable(item):
            item = item()
        calls = [NS(id=f'call-{len(sent)}-{i}', type='function', function=NS(
            name=c['name'], arguments=json.dumps(c['arguments'], ensure_ascii=False)))
            for i, c in enumerate(item.get('tool_calls', []))]
        message = NS(content=item.get('content', ''), tool_calls=calls,
                     reasoning_content='  EXACT REASONING\n' if thinking else '')
        usage = NS(prompt_tokens=100, completion_tokens=5, prompt_cache_hit_tokens=64, prompt_cache_miss_tokens=36)
        if not kwargs.get('stream'):
            return NS(choices=[NS(message=message)], usage=usage)
        deltas = []
        if thinking:
            deltas.append({'choices':[{'delta':{'reasoning_content':message.reasoning_content}}]})
        if message.content:
            deltas.append({'choices':[{'delta':{'content':message.content}}]})
        for i, c in enumerate(calls):
            deltas.append({'choices':[{'delta':{'tool_calls':[{'index':i, 'id':c.id, 'type':'function',
                'function':{'name':c.function.name, 'arguments':c.function.arguments}}]}}]})
        deltas.append({'choices':[{'delta':{}, 'finish_reason':'tool_calls' if calls else 'stop'}]})
        deltas.append({'choices':[], 'usage':vars(usage)})
        return iter(deltas)
    config = Config(model='z-ai/glm-unit:thinking' if thinking else 'synthetic-cache-model',
                    base_url='https://unit.invalid', api_key='synthetic', auto_mode=False,
                    stream_responses=stream, verify_command=verify, context_active_max_bytes=cap,
                    max_steps=16, max_retries=5)
    model = OpenAIModel.__new__(OpenAIModel)
    model.config = config
    model._client = NS(chat=NS(completions=NS(create=create)))
    return Akatikito(str(root), config, model), sent


def assert_append(before, after, *, same_policy=True):
    assert before['tools'] == after['tools']
    if same_policy:
        assert after['messages'][:len(before['messages'])] == before['messages']
    else:
        assert after['messages'][1:len(before['messages'])] == before['messages'][1:]
    ContextWindow.groups(after['messages'])


@pytest.mark.parametrize('thinking', [False, True])
@pytest.mark.parametrize('stream', [False, True])
def test_reads_memory_and_streaming_only_append(thinking, stream, tmp_path):
    (tmp_path/'a.py').write_text('def first():\n    return 8877\n')
    (tmp_path/'b.py').write_text('def second():\n    return 42\n')
    script = [{'tool_calls':[tool_call('read_file', path='a.py')]},
              {'tool_calls':[tool_call('remember', note='KEEP PORT 8877')]},
              {'tool_calls':[tool_call('read_file', path='b.py')]}, {'content':'done'}]
    agent, sent = adapter(tmp_path, script, thinking=thinking, stream=stream)
    assert agent.chat('Διάβασε και εξήγησε τα αρχεία') == 'done'
    assert len(sent) == 4
    for before, after in zip(sent, sent[1:]):
        assert_append(before, after)
    assert all('τρέχων κώδικας' not in request['messages'][0]['content'] for request in sent)
    assert '8877' in encoded(sent[-1]['messages']) and 'return 42' in encoded(sent[-1]['messages'])
    assert 'KEEP PORT 8877' in encoded(sent[-1]['messages'])
    assert sum(m['role']=='user' for m in sent[-1]['messages']) == 1
    assert agent.tokens['cache_hit_tokens'] == 4*64
    assert all('remember' in {t['function']['name'] for t in r['tools']} for r in sent)
    if thinking:
        assert any(m.get('reasoning_content')=='  EXACT REASONING\n' for m in sent[-1]['messages'])


def test_follow_up_turns_preserve_prefix_and_add_current_goal(tmp_path):
    agent, sent = adapter(tmp_path, [{'content':'πρώτη απάντηση'}, {'content':'δεύτερη απάντηση'}])
    agent.chat('Πώς δουλεύει το πρώτο;')
    agent.chat('Και το δεύτερο;')
    assert_append(sent[0], sent[1])
    assert 'Και το δεύτερο;' in encoded(sent[1]['messages'])
    assert agent.context.metrics['protocol_checkpoints'] == 0


def test_changed_goal_and_preferences_are_tail_observations(tmp_path):
    agent, sent = adapter(tmp_path, [{'content':'answer'}, {'content':'answer'}])
    agent.task.begin('Υλοποίησε feature A', [])
    agent.ws.append_memory('KEEP USER DATA')
    agent.chat('Πώς λειτουργεί;')
    memory.append('user', 'ANSWER IN GREEK')
    agent.task.begin('Υλοποίησε feature B', [])
    agent.chat('Εξήγησε το επόμενο')
    assert_append(sent[0], sent[1])
    assert 'feature A' not in sent[0]['messages'][0]['content']
    text = encoded(sent[1]['messages'][len(sent[0]['messages']):])
    assert 'feature B' in text and 'ANSWER IN GREEK' in text
    assert 'KEEP USER DATA' in encoded(sent[1]['messages'])


def test_remember_repetition_guard_survives_stable_schema(tmp_path):
    agent, sent = adapter(tmp_path, [
        {'tool_calls':[tool_call('remember', note='KEEP PORT 8877')]},
        {'tool_calls':[tool_call('remember', note='KEEP PORT 8877 again')]}, {'content':'done'}])
    assert agent.chat('Κράτα τη χρήσιμη προτίμηση')=='done'
    assert_append(sent[0], sent[1]); assert_append(sent[1], sent[2])
    assert agent.ws.read_memory().count('KEEP PORT') == 1
    assert any('ΑΠΟΤΡΟΠΗ ΕΠΑΝΑΛΗΨΗΣ' in m.get('content','') for m in sent[-1]['messages'])


def test_recall_schema_stays_stable_and_exact_archive_is_available(tmp_path):
    script = []
    agent, sent = adapter(tmp_path, script)
    key = agent.context.store('EXACT DECISION: preserve port 8877 and all data')
    script.extend([{'tool_calls':[tool_call('recall_context', id=key)]}, {'content':'done'}])
    # The iterator uses this list, so script additions remain visible.
    assert agent.chat('Ποια ήταν η παλιά απόφαση;')=='done'
    assert_append(sent[0], sent[1])
    assert 'EXACT DECISION' in encoded(sent[-1]['messages'])
    assert any(t['function']['name']=='recall_context' for t in sent[0]['tools'])


def test_failure_diagnosis_fix_and_recheck_keep_evidence(tmp_path):
    (tmp_path/'calc.py').write_text('value=1\n')
    (tmp_path/'check.py').write_text('from calc import value\nassert value==2, "WRONG VALUE"\n')
    command=f'"{sys.executable}" -B check.py'
    script = [
        {'tool_calls':[tool_call('run_command', command=command)]},
        {'tool_calls':[tool_call('read_file', path='calc.py')]},
        {'tool_calls':[tool_call('record_diagnosis', cause='value is 1 instead of 2',
                                evidence='assert value==2 and calc.py has value=1', paths=['calc.py'])]},
        {'tool_calls':[tool_call('edit_file', path='calc.py', edits=[{'search':'value=1','replace':'value=2'}])]},
        {'tool_calls':[tool_call('complete_task', outcome='completed', summary='Διορθώθηκε',
                                evidence='calc.py value=2; check.py passed')]}]
    agent, sent = adapter(tmp_path, script, verify=command)
    result = agent.chat('Διόρθωσε το bug στο calc.py')
    assert 'Διορθώθηκε' in result and not agent.repair.pending
    assert (tmp_path/'calc.py').read_text()=='value=2\n'
    assert [e.data['ok'] for e in agent.events.log if e.kind=='verify']==[False, True]
    # Code -> Debug is a genuine policy change; history still stays exact.
    assert_append(sent[0], sent[1], same_policy=False)
    for before, after in zip(sent[1:], sent[2:]):
        assert_append(before, after)
    assert 'WRONG VALUE' in encoded(sent[2]['messages'])
    checks=[m['content'] for m in agent.messages if m.get('_context_key')=='έλεγχοι']
    assert 'Εκκρεμεί' in checks[0] and 'Δεν υπάρχει ενεργό' in checks[-1]
    assert json.loads(checks[0].split('Εκκρεμεί διόρθωση: ', 1)[1])['command']==command


def test_policy_change_is_applied_without_rewriting_tool_evidence(tmp_path):
    (tmp_path/'a.py').write_text('value=42\n')
    def change_rules():
        (tmp_path/'RULES.md').write_text('NEW RULE: KEEP PORT 8877')
        return {'tool_calls':[tool_call('read_file', path='a.py')]}
    agent, sent = adapter(tmp_path, [change_rules, {'content':'done'}])
    agent.chat('Εξήγησε το αρχείο')
    assert 'NEW RULE' not in sent[0]['messages'][0]['content']
    assert 'NEW RULE: KEEP PORT 8877' in sent[1]['messages'][0]['content']
    assert_append(sent[0], sent[1], same_policy=False)


def test_manual_read_only_mode_never_inherits_cached_write_tools(tmp_path):
    agent, sent = adapter(tmp_path, [{'content':'first'},
        {'tool_calls':[tool_call('write_file', path='forbidden.txt', content='bad')]}, {'content':'done'}])
    agent.chat('Εξήγησε τον κώδικα')
    assert any(t['function']['name']=='write_file' for t in sent[0]['tools'])
    agent.set_mode('ask')
    agent.chat('Τι κάνει;')
    assert not (tmp_path/'forbidden.txt').exists()
    assert all(t['function']['name']!='write_file' for t in sent[1]['tools'])
    assert 'Ερώτηση' in sent[1]['messages'][0]['content']


def test_explicit_no_changes_still_restricts_tools(tmp_path):
    agent, sent = adapter(tmp_path, [{'content':'first'}, {'content':'second'}])
    agent.chat('Εξήγησε τον κώδικα')
    agent.chat('Μόνο ανάλυση, χωρίς αλλαγές')
    assert any(t['function']['name']=='edit_file' for t in sent[0]['tools'])
    assert all(t['function']['name'] not in ('write_file','edit_file') for t in sent[1]['tools'])


def exchange(n, text):
    return [{'role':'assistant', 'content':'', 'reasoning_content':f'opaque-{n}',
             '_reasoning_identity':['model','unit'], 'tool_calls':[{'id':f'c{n}', 'type':'function',
             'function':{'name':'read_file','arguments':json.dumps({'path':f'f{n}.py'})}}]},
            {'role':'tool','tool_call_id':f'c{n}', 'content':text}]


def test_checkpoint_preserves_constraints_latest_state_and_exact_archive(tmp_path):
    context = ContextWindow(Workspace(tmp_path), max_chars=8000)
    context.active_max_bytes=18000; context.reasoning_identity=('model','unit')
    messages=[{'role':'system','content':'KEEP RULE 8877'},
              {'role':'user','content':'Preserve all user data', '_origin':'owner'}]
    for n in range(5):
        messages += exchange(n, (f'EXACT SOURCE {n}\n' + 'code '*1800))
        context.observe(messages, {'εργασία':'Goal: implement parser', 'έλεγχοι':'AssertionError NEED FIX'})
    before = copy.deepcopy(messages)
    view=context.prepare(messages, continuous=True)
    assert context.metrics['protocol_checkpoints']==1
    assert 'KEEP RULE 8877' in encoded(view) and 'Preserve all user data' in encoded(view)
    assert 'Goal: implement parser' in encoded(view) and 'AssertionError NEED FIX' in encoded(view)
    assert messages==before
    found=False
    for path in context.folder.glob('*.json'):
        page=context.recall(path.stem, query='EXACT SOURCE 0')
        if page.get('found') is not False and 'EXACT SOURCE 0' in page.get('text',''):
            found=True
    assert found
    frozen=copy.deepcopy(view)
    context.begin_turn()
    messages += [{'role':'user','content':'Continue the same goal', '_origin':'owner'}]
    second=context.prepare(messages, continuous=True)
    assert second[:len(frozen)]==frozen
    ContextWindow.groups(second)


def test_checkpoint_transport_round_trip_and_tamper_rejection(tmp_path):
    context=ContextWindow(Workspace(tmp_path), max_chars=8000)
    context.reasoning_identity=('model','unit')
    messages=[{'role':'system','content':'RULE'}, {'role':'user','content':'goal','_origin':'owner'}]
    messages += exchange(1, 'source '*2600)
    first=context.prepare(messages, continuous=True)
    saved=json.loads(json.dumps(context.snapshot_transport(messages)))
    restored=ContextWindow(Workspace(tmp_path), max_chars=8000)
    restored.reasoning_identity=('model','unit')
    restored.restore_transport(saved, messages, ('model','unit'))
    messages += exchange(2, 'fresh source')
    second=restored.prepare(messages, continuous=True)
    assert second[:len(first)]==first
    assert restored.metrics['protocol_checkpoints']==1
    changed=copy.deepcopy(messages); changed[1]['content']='DIFFERENT GOAL'
    rejected=ContextWindow(Workspace(tmp_path))
    rejected.restore_transport(saved, changed, ('model','unit'))
    assert rejected._continuous_prefix is None
    rejected.restore_transport(saved, messages, ('different-model','unit'))
    assert rejected._continuous_prefix is None


def test_session_reopen_restores_cache_key_and_request_prefix(tmp_path):
    from akatikito.app_api import AppState
    agent, first = adapter(tmp_path, [{'content':'first answer'}])
    agent.chat('Πρώτη ερώτηση')
    state=AppState(); state.agent=agent; state.store=SessionStore(tmp_path)
    state._save_session(); sid=state.session_id
    saved=state.store.load(sid)
    assert saved['request_cache']['key']==agent.cache_id
    resumed, second=adapter(tmp_path, [{'content':'second answer'}])
    state.agent=resumed
    state.open_session(sid, str(tmp_path))
    assert resumed.cache_id==agent.cache_id
    resumed.chat('Συνέχεια ερώτησης')
    assert_append(first[0], second[0])


def test_context_economy_disabled_still_uses_stable_policy_and_full_history(tmp_path):
    (tmp_path/'source.py').write_text('CURRENT_SOURCE=8877\n')
    agent, sent=adapter(tmp_path, [{'tool_calls':[tool_call('read_file',path='source.py')]}, {'content':'done'}])
    agent.context.enabled=False
    agent.chat('Διάβασε το αρχείο')
    assert_append(sent[0], sent[1])
    assert 'CURRENT_SOURCE=8877' in encoded(sent[1]['messages'])


def test_new_session_discards_transport_and_keeps_memory(tmp_path):
    agent, sent=adapter(tmp_path,[{'content':'first'}, {'content':'second'}])
    agent.ws.append_memory('KEEP PORT 8877')
    agent.chat('Old question')
    previous_key=agent.cache_id
    agent.reset(); agent.chat('New question')
    assert agent.cache_id != previous_key
    assert 'Old question' not in encoded(sent[1]['messages'])
    assert 'KEEP PORT 8877' in encoded(sent[1]['messages'])


def test_identical_state_is_recorded_once_and_current_state_is_latest(tmp_path):
    messages=[{'role':'user','content':'goal'}]
    ContextWindow.observe(messages, {'έλεγχοι':'failed', 'μνήμη':'KEEP DATA'})
    original=copy.deepcopy(messages)
    ContextWindow.observe(messages, {'έλεγχοι':'failed', 'μνήμη':'KEEP DATA'})
    assert messages==original
    ContextWindow.observe(messages, {'έλεγχοι':''})
    assert len(messages)==len(original)+1 and messages[-1]['_context_key']=='έλεγχοι'


@pytest.mark.parametrize('text', ['Μόνο ανάλυση, χωρίς αλλαγές', 'xoris allages', 'analysis without changes'])
def test_no_changes_constraint_is_not_lost_to_cache_policy(text, tmp_path):
    from akatikito.intent import read_only
    assert read_only(text)


@pytest.mark.parametrize('text', ['Πρόσθεσε web χωρίς αλλαγές στα tests', 'Implement web without changes to the tests'])
def test_a_local_test_constraint_does_not_revoke_all_implementation(text):
    from akatikito.intent import read_only
    assert not read_only(text)


def test_new_owner_turn_clears_current_read_evidence_without_losing_source(tmp_path):
    (tmp_path/'source.py').write_text('EXACT_BODY=8877\n')
    agent, sent=adapter(tmp_path,[{'tool_calls':[tool_call('read_file',path='source.py')]},
                               {'content':'first'}, {'content':'second'}])
    agent.chat('Διάβασε τον κώδικα')
    agent.chat('Εξήγησε το επόμενο')
    assert_append(sent[1], sent[2])
    notes=[m['content'] for m in agent.messages if m.get('_context_key')=='τρέχων κώδικας']
    assert 'Δεν υπάρχει ενεργό' in notes[-1]
    assert 'EXACT_BODY=8877' in encoded(sent[-1]['messages'])
