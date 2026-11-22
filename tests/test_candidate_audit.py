"""Actual application routing and trusted candidate checks with a synthetic model adapter."""
import json
from pathlib import Path
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from test_completion_audit import fixture as adapter_fixture, finish
from akatikito import app_api, memory, settings
from akatikito.app_api import AppState
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call
from akatikito.sessions import SessionStore
from akatikito.tools import dispatch


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'memory')
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')


def state_fixture(tmp_path,monkeypatch,script):
    root=tmp_path/'application';root.mkdir();(root/'akatikito').mkdir();(root/'tests').mkdir()
    (root/'app.py').write_text('# app\n',encoding='utf-8')
    (root/'akatikito/__init__.py').write_text('',encoding='utf-8')
    (root/'akatikito/ui.html').write_text('<script>const ok=true;</script>',encoding='utf-8')
    (root/'tests/test_base.py').write_text('def test_baseline():\n    assert True\n',encoding='utf-8')
    monkeypatch.setattr(app_api,'APP_ROOT',root)
    adapter,sent=adapter_fixture(root,script)
    created=[]
    def factory(config):
        created.append(config)
        adapter.model.config=config
        return adapter.model
    state=AppState();state.settings=settings.Settings(provider='DeepSeek',model='synthetic',project=str(root))
    state.agent=Akatikito(str(root),Config(api_key='synthetic',model='synthetic',stream_responses=False),ScriptedModel([]))
    state.agent.events.subscribe(state.emit_event);state.store=SessionStore(root)
    monkeypatch.setattr('akatikito.agent.OpenAIModel',factory)
    finished=threading.Event();push=state.push
    def notify(kind,**data):
        push(kind,**data)
        if kind=='done':finished.set()
    state.push=notify
    return root,state,sent,created,finished


@pytest.mark.parametrize('owner_request',[
    'αναβάθμισε τον εαυτό σου, μην αλλάξεις τα tests',
    '1 και 2',
    'Πρόσθεσε τα δύο χαρακτηριστικά',
])
def test_routing_selection_constraints_and_real_candidate_verification(tmp_path,monkeypatch,owner_request):
    script=[{'tool_calls':[
        tool_call('write_file',path='features.py',content='PARALLEL=True\nSTREAMING=True\n'),
        tool_call('write_file',path='tests/test_features.py',content='from features import PARALLEL, STREAMING\ndef test_features():\n    assert PARALLEL and STREAMING\n')]},
        {'tool_calls':[finish(evidence='features.py και tests/test_features.py')]}]
    root,state,sent,created,done=state_fixture(tmp_path,monkeypatch,script)
    state.agent.messages=[{'role':'system','content':state.agent.system_prompt()},
                          {'role':'assistant','content':'Ποια να υλοποιήσω;\n1. Parallel tools\n2. Streaming'}]
    before=(root/'tests/test_base.py').read_bytes()
    result=state.send(owner_request)
    assert result.get('conversation_changed') and done.wait(30)
    assert not state.error,state.error
    assert state.self_update.status()['ready'] and state.agent.task.status=='complete'
    assert (root/'tests/test_base.py').read_bytes()==before and not (root/'features.py').exists()
    assert len(created)==1 and len(sent)==2
    assert 'υποψήφιες' in state.transcript[-1]['text']
    saved=state.store.load(state.session_id)
    assert saved['task_state']['status']=='complete'
    if owner_request=='1 και 2':
        assert 'Parallel tools' in state.agent.task.goal and 'Streaming' in state.agent.task.goal
        assert any(m['role']=='user' and m['content']=='1 και 2' for m in saved['messages'])
        # Resolved owner intent is preserved at the tail; system stays policy-only.
        assert 'Parallel tools' not in sent[0]['messages'][0]['content']
        assert any('Parallel tools' in m.get('content','') and 'Streaming' in m.get('content','')
                   for m in sent[0]['messages'][1:])


def test_existing_candidate_test_is_protected_before_write(tmp_path,monkeypatch):
    root,state,sent,created,done=state_fixture(tmp_path,monkeypatch,[])
    assert not state.start_upgrade().get('error')
    target=state.agent.ws.root/'tests/test_base.py';before=target.read_bytes()
    result=dispatch(state.agent.ws,'edit_file',{'path':'tests/test_base.py','edits':[{'search':'assert True','replace':'assert False'}]})
    assert result.startswith('ΑΠΟΡΡΙΨΗ') and target.read_bytes()==before
    assert not list(state.agent.ws.root.glob('.agent_backups/*'))
    assert dispatch(state.agent.ws,'write_file',{'path':'tests/test_new.py','content':'def test_new():\n    assert True\n'}).startswith('ΕΓΙΝΕ')
    assert not sent


def test_manual_architect_stays_plan_only_after_routing(tmp_path,monkeypatch):
    script=[{'tool_calls':[tool_call('write_file',path='bad.py',content='x=1'),tool_call('write_file',path='PLAN.md',content='Σχέδιο')]},{'content':'Το πλάνο είναι έτοιμο.'}]
    root,state,sent,created,done=state_fixture(tmp_path,monkeypatch,script)
    state.agent.set_mode('architect')
    assert state.send('σχεδίασε πλάνο').get('conversation_changed') and done.wait(5)
    assert not state.error and state.agent.mode=='architect'
    assert (state.agent.ws.root/'PLAN.md').exists() and not (state.agent.ws.root/'bad.py').exists()
    assert not (root/'PLAN.md').exists()


def test_manual_question_selection_never_routes_into_write_permissions(tmp_path,monkeypatch):
    root,state,sent,created,done=state_fixture(tmp_path,monkeypatch,[])
    state.agent.messages=[{'role':'assistant','content':'Ποια να υλοποιήσω;\n1. Parallel\n2. Streaming'}]
    state.agent.set_mode('ask')
    assert state.send('1 και 2').get('error') and state.self_update is None
    assert not created and not sent


def test_validator_environment_blocker_stops_without_extra_model_calls_and_allows_retry(tmp_path):
    missing='Environment: {"missing":["webview"]}\nΛείπουν εξαρτήσεις από την Python ελέγχων.'
    agent,sent=adapter_fixture(tmp_path,[{'tool_calls':[tool_call('run_command',command='verify')]}],verify='verify')
    agent.ws.update_validator=lambda:(False,missing)
    result=agent.chat('Διόρθωσε και έλεγξε το feature')
    assert len(sent)==1 and 'Λείπουν εξαρτήσεις' in result
    assert agent.repair.failure['external_blocker'] and agent.repair.can_verify()
    replacement,retry_sent=adapter_fixture(tmp_path,[{'tool_calls':[tool_call('run_command',command='verify')]},{'content':'Οι έλεγχοι πέρασαν.'}],verify='verify')
    agent.model=replacement.model;agent.ws.update_validator=lambda:(True,'OK: environment fixed')
    assert agent.chat('τρέξε verify')=='Οι έλεγχοι πέρασαν.'
    assert not agent.repair.pending and len(retry_sent)==2


def test_check_button_clears_fixed_external_failure_and_does_not_advertise_empty_apply(tmp_path,monkeypatch):
    root,state,sent,created,done=state_fixture(tmp_path,monkeypatch,[])
    state.start_upgrade()
    state.agent.repair.failed('Λείπουν εξαρτήσεις από την Python ελέγχων.')
    state.agent.repair.failure['external_blocker']=True
    monkeypatch.setattr(state.self_update,'validate',lambda:(True,'OK'))
    state.check_upgrade();assert done.wait(5)
    assert not state.agent.repair.pending and 'Δεν υπάρχουν αλλαγές' in state.notice
    assert not sent


def test_valid_progress_and_goal_are_saved_while_next_model_response_is_pending(tmp_path):
    script=[{'tool_calls':[tool_call('write_file',path='new.txt',content='ok')]},{'tool_calls':[finish()]}]
    agent,sent=adapter_fixture(tmp_path,script)
    state=AppState();state.agent=agent;state.store=SessionStore(tmp_path)
    agent.events.subscribe(state.emit_event)
    pending=threading.Event();release=threading.Event();done=threading.Event()
    create=agent.model._client.chat.completions.create
    def blocked(**kwargs):
        if len(sent)==1:
            pending.set()
            assert release.wait(5)
        return create(**kwargs)
    agent.model._client.chat.completions.create=blocked
    push=state.push
    def notify(kind,**data):
        push(kind,**data)
        if kind=='done':done.set()
    state.push=notify
    try:
        assert state.send('Υλοποίησε το νέο αρχείο')['ok']
        assert pending.wait(3) and state.busy
        saved=state.store.load(state.session_id)
        assert saved['task_state']['status']=='active'
        assert 'νέο αρχείο' in saved['task_state']['goal']
        assert saved['messages'][-1]['role']=='tool'
        assert saved['messages'][-1]['content'].startswith('ΕΓΙΝΕ')
        from akatikito.context import ContextWindow
        ContextWindow.groups(saved['messages'])
        release.set();assert done.wait(3)
        assert not state.error and state.agent.task.status=='complete' and len(sent)==2
    finally:
        release.set()
