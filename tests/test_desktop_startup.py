"""One desktop runtime replaces the retired UI, with actual local HTTP and stored sessions."""
import ast
import http.client
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito import settings
from akatikito.app_api import AppState
from akatikito.model import ScriptedModel


def test_desktop_boot_connect_session_and_shutdown(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')
    state=AppState();monkeypatch.setattr(app,'AppState',lambda:state)
    model=ScriptedModel([{'content':'Συνθετική απάντηση'}]);monkeypatch.setattr('akatikito.agent.OpenAIModel',lambda config:model)
    windows=[];address=[]
    def create(title,url,**options):windows.append((title,url,options))
    def start():
        from urllib.parse import urlsplit
        url=windows[0][1];parsed=urlsplit(url);token=parsed.fragment.split('=',1)[1];address.append(parsed.port)
        def call(path,data=None):
            c=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=4)
            headers={'X-Akatikito-Token':token}
            if data is not None:headers['Content-Type']='application/json'
            c.request('POST' if data is not None else 'GET',path,None if data is None else json.dumps(data),headers)
            r=c.getresponse();body=r.read();assert r.status==200;c.close();return body if path=='/' else json.loads(body)
        assert not call('/api/status')['connected']
        assert b'id="voice-record"' in call('/')
        connected=call('/api/connect',{'provider':'Custom','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic','model':'fake','project':str(tmp_path),'max_steps':5})
        assert connected['connected'] and connected['model']=='fake'
        assert call('/api/session/new',{})['transcript']==[]
        assert call('/api/send',{'text':'Τι μπορείς;'})['ok']
        deadline=time.monotonic()+5
        while state.busy and time.monotonic()<deadline:time.sleep(.02)
        assert not state.busy and not state.error
        assert call('/api/sessions')['sessions'] and state.transcript[-1]['text']=='Συνθετική απάντηση'
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(create_window=create,start=start))
    assert app.main()==0 and model.calls==1
    assert windows[0][2]['text_select'] is True and state.voice.closed
    assert not state.mcp.servers
    with pytest.raises(OSError):
        c=http.client.HTTPConnection('127.0.0.1',address[0],timeout=1);c.request('GET','/')


def test_runtime_no_longer_requires_legacy_framework():
    from akatikito.self_update import source_files
    from akatikito import verification
    root=Path(__file__).resolve().parents[1]
    assert not (root/'dashboard.py').exists() and not (root/'AkatikitoLegacy.cmd').exists() and not (root/'.streamlit').exists()
    source=source_files(root)
    assert 'app.py' in source and 'dashboard.py' not in source and 'AkatikitoLegacy.cmd' not in source
    assert "'streamlit'" not in Path(verification.__file__).read_text(encoding='utf-8')
    for file in [root/'app.py',*sorted((root/'akatikito').glob('*.py'))]:
        tree=ast.parse(file.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):assert all(n.name.split('.')[0]!='streamlit' for n in node.names)
            if isinstance(node,ast.ImportFrom):assert (node.module or '').split('.')[0]!='streamlit'
    for file in ('Akatikito.cmd','AkatikitoApp.cmd'):
        text=(root/file).read_text(encoding='utf-8-sig');assert 'app.py' in text and 'streamlit' not in text
