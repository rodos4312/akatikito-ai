"""First-run persistence and authenticated production routes; models are scripted."""
from pathlib import Path
import http.client
import json
import shutil
import subprocess
import sys
import threading
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito import settings,memory
from akatikito.app_api import AppState,start_server
from akatikito.model import ScriptedModel


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')
    monkeypatch.setattr(memory,'DIR',tmp_path/'memory')
    models=[]
    def factory(config):
        model=ScriptedModel([]);models.append(model);return model
    monkeypatch.setattr('akatikito.agent.OpenAIModel',factory)
    yield models
    assert all(model.calls==0 for model in models)


def connect(state,root):
    return state.connect('Custom','https://synthetic.invalid/v1','manual/model','synthetic-key',str(root),8,'')


def test_fresh_install_needs_setup_without_creating_settings():
    state=AppState()
    assert state.status()['onboarding']=={'complete':False}
    assert not settings.DEFAULT_PATH.exists()


def test_legacy_configured_install_is_not_forced_back_into_setup(tmp_path):
    original=json.dumps({'provider':'Custom','project':str(tmp_path),'model':'kept/model',
                         'keys':{'Custom':settings.mask_secret('KEEP')},'allowed_commands':['git status']}).encode()
    settings.DEFAULT_PATH.write_bytes(original)
    loaded=settings.Settings.load()
    assert loaded.onboarding_complete and loaded.key_for('Custom')=='KEEP'
    assert settings.DEFAULT_PATH.read_bytes()==original


def test_failed_project_connection_does_not_complete_or_save_setup(tmp_path):
    state=AppState();result=connect(state,tmp_path/'does-not-exist')
    assert result['error'] and not state.settings.onboarding_complete
    assert state.agent is None and not settings.DEFAULT_PATH.exists()


def test_successful_setup_is_persisted_without_a_chat_call(tmp_path):
    state=AppState();result=connect(state,tmp_path)
    assert result['connected'] and result['onboarding']['complete']
    loaded=settings.Settings.load();assert loaded.onboarding_complete and loaded.model=='manual/model'
    assert loaded.key_for('Custom')=='synthetic-key'
    assert 'synthetic-key' not in settings.DEFAULT_PATH.read_text()


def test_storage_failure_preserves_previous_connection_and_settings(tmp_path,monkeypatch):
    state=AppState();assert connect(state,tmp_path)['connected']
    previous_agent=state.agent;previous_settings=state.settings;before=settings.DEFAULT_PATH.read_bytes()
    def fail(*a,**k):raise OSError('synthetic disk failure')
    monkeypatch.setattr(settings.Settings,'save',fail)
    result=state.connect('Custom','https://synthetic.invalid/v1','other/model','new-key',str(tmp_path),8,'')
    assert result['error'] and state.agent is previous_agent and state.settings is previous_settings
    assert settings.DEFAULT_PATH.read_bytes()==before and state.settings.model=='manual/model'


def test_fresh_storage_failure_does_not_mark_onboarding_complete(tmp_path,monkeypatch):
    state=AppState()
    def fail(*a,**k):raise OSError('synthetic disk failure')
    monkeypatch.setattr(settings.Settings,'save',fail)
    assert connect(state,tmp_path)['error']
    assert not state.settings.onboarding_complete and state.agent is None


def test_http_wizard_assets_and_connection_authentication(tmp_path):
    state=AppState();_,server=start_server(state)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,body=None,authorized=True,extra=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        headers={'Content-Type':'application/json',**({'X-Akatikito-Token':server.auth_token} if authorized else {}),**(extra or {})}
        try:
            conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
            response=conn.getresponse();return response.status,response.read(),response.getheader('Content-Type')
        finally:conn.close()
    try:
        code,raw,content=request('/onboarding.js',authorized=False)
        assert code==200 and content.startswith('text/javascript') and b'finishSetup' in raw
        assert request('/onboarding.js',extra={'Host':'evil.invalid'})[0]==403
        payload={'provider':'Custom','base_url':'https://synthetic.invalid/v1','model':'m','project':str(tmp_path),'api_key':'synthetic-key'}
        assert request('/api/connect',payload,authorized=False)[0]==403
        assert not state.settings.onboarding_complete
        code,raw,_=request('/api/connect',payload)
        assert code==200 and json.loads(raw)['onboarding']['complete']
    finally:server.shutdown();server.server_close();thread.join(3)


def test_actual_wizard_frontend_states_and_no_automatic_network():
    node=shutil.which('node');assert node
    result=subprocess.run([node,str(Path(__file__).with_suffix('.cjs'))],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
