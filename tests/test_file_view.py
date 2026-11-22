"""File previews, native picker, sessions and real loopback API without provider requests."""
import copy
import json
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS
import urllib.request
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito import file_view, memory
from akatikito.app_api import AppState, start_server
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call
from akatikito.sessions import SessionStore


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'global-memory')
    original=socket.socket.connect
    def local_only(sock, address):
        if address[0] not in ('127.0.0.1','::1'):
            raise AssertionError('External network forbidden in file tests')
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_only)


def state(root, script=None):
    app=AppState();app.settings.project=str(root)
    if script is not None:
        app.agent=Akatikito(str(root),Config(auto_mode=False),ScriptedModel(script))
        app.agent.events.subscribe(app.emit_event);app.store=SessionStore(root)
    return app


@pytest.mark.parametrize('encoding',['utf-8','utf-8-sig','utf-16','utf-32'])
def test_preview_decodes_text_without_changing_files(tmp_path,encoding):
    path=tmp_path/'ελληνικά.py';text='def hello():\n    return "Καλημέρα 🌍"\n'
    path.write_bytes(text.encode(encoding));before=(path.read_bytes(),path.stat().st_mtime_ns)
    result=file_view.preview(tmp_path,path.name)
    assert result['text']==text and result['path']==path.name and result['next'] is None
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before


def test_paging_keeps_every_character_including_long_lines_and_emoji(tmp_path):
    text='α🌍'*35000+'\nTHE END';(tmp_path/'long.txt').write_bytes(text.encode('utf-8'))
    offset=0;parts=[];version=''
    while True:
        result=file_view.preview(tmp_path,'long.txt',offset,version)
        assert len(result['text'])<=file_view.PAGE_CHARS
        version=result['version'];parts.append(result['text'])
        if result['next'] is None:break
        offset=result['next']
    assert ''.join(parts)==text and len(parts)>1


def test_empty_file_and_changed_page_version(tmp_path):
    path=tmp_path/'a.txt';path.write_text('')
    assert file_view.preview(tmp_path,'a.txt')['text']==''
    first=file_view.preview(tmp_path,'a.txt');path.write_text('new version')
    with pytest.raises(ValueError,match='άλλαξε'):file_view.preview(tmp_path,'a.txt',0,first['version'])


@pytest.mark.parametrize('offset',[-1,True,1.5,'1'])
def test_invalid_offsets_fail(tmp_path,offset):
    (tmp_path/'a.txt').write_text('hello')
    with pytest.raises(ValueError):file_view.preview(tmp_path,'a.txt',offset)


def test_binary_large_missing_directory_and_outside_are_not_read(tmp_path):
    (tmp_path/'binary.bin').write_bytes(b'a\0b')
    (tmp_path/'invalid.bin').write_bytes(b'\xff\xff')
    (tmp_path/'huge.txt').write_bytes(b'a'*(file_view.MAX_FILE_BYTES+1))
    for name in ('binary.bin','invalid.bin','huge.txt','missing.txt','.'):
        with pytest.raises((ValueError,OSError)):file_view.preview(tmp_path,name)
    outside=tmp_path.parent/'outside.txt';outside.write_text('PRIVATE')
    for name in ('../outside.txt',str(outside)):
        with pytest.raises(PermissionError):file_view.preview(tmp_path,name)


def test_preview_without_connection_does_not_call_model_or_create_chat(tmp_path):
    (tmp_path/'a.txt').write_text('CURRENT SOURCE')
    app=state(tmp_path);before=copy.deepcopy(app.transcript)
    result=app.open_file('a.txt')
    assert result['text']=='CURRENT SOURCE' and app.agent is None
    assert app.session_id is None and app.transcript==before
    assert app.status()['files']==[{'path':'a.txt','action':'άνοιγμα'}]
    assert app.status()['files_project']==str(tmp_path)


def test_open_is_read_only_and_failed_attempts_do_not_enter_sidebar(tmp_path):
    (tmp_path/'a.txt').write_text('source')
    app=state(tmp_path,[{'content':'unused'}]);before=copy.deepcopy(app.agent.messages)
    app.open_file('a.txt');app.open_file('./a.txt')
    assert len(app.files)==1 and app.agent.messages==before and app.agent.model.calls==0
    assert app.open_file('../outside.txt').get('error') and len(app.files)==1
    assert app.open_file('a.txt',project=str(tmp_path/'other')).get('error')
    assert app.open_file('a.txt',session_id='stale').get('error')


def test_only_successful_agent_writes_are_listed_and_opening_keeps_change_badge(tmp_path):
    app=state(tmp_path,[{'tool_calls':[tool_call('write_file',path='good.txt',content='ok'),
                       tool_call('edit_file',path='missing.txt',edits=[{'search':'old','replace':'new'}])]}, {'content':'done'}])
    assert app.agent.chat('Φτιάξε το αρχείο')=='done'
    assert app.files==[{'path':'good.txt','action':'νέο'}]
    app.open_file('good.txt')
    assert app.files==[{'path':'good.txt','action':'νέο'}]


def test_files_survive_session_reopen_and_reset(tmp_path):
    (tmp_path/'a.txt').write_text('source')
    app=state(tmp_path,[{'content':'hello'}]);app.agent.chat('ερώτηση')
    sid=app.session_id;app.open_file('a.txt')
    assert app.store.load(sid)['files']==app.files
    app.new_session();assert app.files==[]
    app.open_session(sid,str(tmp_path));assert app.files==[{'path':'a.txt','action':'άνοιγμα'}]
    assert app.agent.model.calls==1


def test_old_sessions_recover_successful_changes_and_ignore_unsafe_rows(tmp_path):
    app=state(tmp_path,[{'content':'unused'}]);sid=app.store.create()
    messages=[{'role':'assistant','content':'','tool_calls':[{'id':'c','type':'function','function':{
        'name':'write_file','arguments':json.dumps({'path':'older.txt','content':'old'})}}]},
        {'role':'tool','tool_call_id':'c','content':'ΕΓΙΝΕ: older.txt'}]
    app.store.save(sid,messages=messages)
    app.open_session(sid,str(tmp_path));assert app.files==[{'path':'older.txt','action':'νέο'}]
    app.store.save(sid,files=[{'path':'../outside','action':'νέο'}, {'path':'','action':'άνοιγμα'},
                              {'path':'older.txt','action':'νέο'}])
    app.open_session(sid,str(tmp_path));assert app.files==[{'path':'older.txt','action':'νέο'}]


def test_native_picker_cancel_error_and_project_switch(tmp_path,monkeypatch):
    (tmp_path/'a.txt').write_text('source');app=state(tmp_path);seen=[]
    def dialog(kind,**kwargs):seen.append((kind,kwargs));return [str(tmp_path/'a.txt')]
    window=NS(create_file_dialog=dialog);module=NS(windows=[window],FileDialog=NS(OPEN='open'))
    monkeypatch.setitem(sys.modules,'webview',module)
    assert app.pick_file()['text']=='source'
    assert seen==[('open',{'directory':str(tmp_path),'allow_multiple':False})]
    window.create_file_dialog=lambda *args,**kwargs:None
    assert app.pick_file()=={'cancelled':True}
    module.windows=[];assert app.pick_file().get('error')
    def switch(*args,**kwargs):app.settings.project=str(tmp_path.parent);return [str(tmp_path/'a.txt')]
    module.windows=[NS(create_file_dialog=switch)]
    assert app.pick_file().get('error')


def test_real_loopback_route_does_not_send_chat_or_change_tokens(tmp_path):
    (tmp_path/'a.txt').write_text('source');app=state(tmp_path,[{'content':'unused'}])
    url,server=start_server(app);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    def post(payload):
        request=urllib.request.Request(url+'/api/files/open',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Akatikito-Token':server.auth_token})
        with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
    try:
        assert post({'path':'a.txt','project':str(tmp_path),'session_id':''})['text']=='source'
        assert post({'path':'../outside.txt'}).get('error')
        assert app.agent.model.calls==0 and app.agent.tokens['prompt_tokens']==0
        assert app.transcript==[] and app.agent.messages==[]
    finally:
        server.shutdown();server.server_close();worker.join(5)
