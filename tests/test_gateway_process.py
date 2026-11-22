"""Real loopback Telegram + detached gateway + authenticated Desktop API; zero external requests."""
import json,threading,time,http.client
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from akatikito.settings import Settings
from akatikito.channel_plugins import Plugins
from akatikito.channel_config import ChannelConfig
from akatikito.gateway_control import GatewayControl


def test_detached_gateway_with_loopback_telegram_survives_window_close(tmp_path,monkeypatch):
    from akatikito import settings
    directory=tmp_path/'data';directory.mkdir();project=tmp_path/'project';project.mkdir()
    monkeypatch.setattr(settings,'DEFAULT_PATH',directory/'settings.json')
    monkeypatch.setenv('AKATIKITO_SETTINGS_FILE',str(directory/'settings.json'))
    monkeypatch.setenv('AKATIKITO_MEMORY_DIR',str(directory/'memory'))
    Settings(provider='Custom',base_url='http://127.0.0.1:9/v1',model='offline-only',project=str(project),checkpoints=False).save()
    sent=[];paths=[]
    update={'update_id':1,'message':{'from':{'id':10},'chat':{'id':10,'type':'private'},'text':'/help'}}
    class FakeTelegram(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));paths.append(self.path)
            if self.path=='/getMe':body={'ok':True,'result':{'id':100,'is_bot':True}}
            elif self.path=='/getUpdates':body={'ok':True,'result':[update,update] if data.get('offset',0)<=1 else []}
            elif self.path=='/sendMessage':sent.append(data);body={'ok':True,'result':{'message_id':99}}
            else:body={'error':'unexpected endpoint'}
            raw=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    telegram=ThreadingHTTPServer(('127.0.0.1',0),FakeTelegram);thread=threading.Thread(target=telegram.serve_forever,daemon=True);thread.start()
    plugins=Plugins(directory/'plugins');manifest={'id':'telegram','name':'Loopback Telegram','version':'test','kind':'channel','api_version':1,'entry':'adapter.py','files':['adapter.py'],'permissions':['loopback synthetic Telegram']}
    entry='from akatikito.channel_bundles.telegram.adapter import Telegram\nfrom akatikito.channel_sdk import HTTP\ndef create(config):\n return Telegram(config,http=HTTP("http://127.0.0.1:'+str(telegram.server_port)+'"))\n'
    parts={'akatikito-plugin.json':json.dumps(manifest),'adapter.py':entry}
    monkeypatch.setattr(plugins,'_github',lambda url:(parts.__getitem__,'https://github.com/synthetic/test/tree/'+'a'*40,'a'*40))
    draft=plugins.preview('https://github.com/synthetic/test');plugins.install(draft['id'],draft['review'])
    ChannelConfig(directory/'channels.json').save('telegram',{'token':'synthetic-only','allowed_users':['10'],'allowed_chats':[],'project':str(project),'mode':'ask','enabled':True},plugins.installed())
    control=GatewayControl(directory)
    try:
        assert control.action({'action':'start'})['ok']
        deadline=time.monotonic()+12
        while len(sent)<1 and time.monotonic()<deadline:time.sleep(.1)
        assert len(sent)==1 and sent[0]['chat_id']=='10' and '/new' in sent[0]['text']
        status=control.status(fresh=True);assert status['queue']['counts']=={'done':1}
        assert set(paths)<={'/getMe','/getUpdates','/sendMessage'}
        desc=json.loads((directory/'gateway/control.json').read_text(encoding='utf-8'))
        port=int(desc['url'].rsplit(':',1)[1])
        for headers in ({},{'X-Akatikito-Gateway':desc['token'],'Origin':'https://evil.example'}, {'X-Akatikito-Gateway':desc['token'],'Host':'evil.example'}):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);c.request('GET','/status',headers=headers);r=c.getresponse();assert r.status==403;r.read();c.close()
        from akatikito.app_api import AppState
        desktop=AppState();desktop.close()
        assert control.status(fresh=True)['running']
        with pytest.raises(ValueError):control.action({'action':'preview','source':'catalog:discord'})
    finally:
        control.action({'action':'stop'})
        deadline=time.monotonic()+10
        while (directory/'gateway/control.json').exists() and time.monotonic()<deadline:time.sleep(.1)
        telegram.shutdown();telegram.server_close();thread.join(timeout=2)
    assert not control.status(fresh=True)['running']


def test_plugin_api_is_authenticated_and_install_is_separate_from_preview(tmp_path,monkeypatch):
    from akatikito import settings
    from akatikito.app_api import AppState,start_server
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')
    state=AppState();url,server=start_server(state);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def call(path,data=None,token=server.auth_token):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);headers={'X-Akatikito-Token':token}
        if data is not None:headers['Content-Type']='application/json'
        c.request('POST' if data is not None else 'GET',path,None if data is None else json.dumps(data),headers);r=c.getresponse();body=json.loads(r.read());status=r.status;c.close();return status,body
    try:
        assert call('/api/plugins',token='invalid')[0]==403
        assert call('/api/plugins/action',{'action':'preview','source':'catalog:telegram'},token='invalid')[0]==403
        status,draft=call('/api/plugins/action',{'action':'preview','source':'catalog:discord'});assert status==200 and draft['manifest']['id']=='discord'
        assert call('/api/plugins')[1]['installed']==[]
        installed=call('/api/plugins/action',{'action':'install','id':draft['id'],'review':draft['review']})[1];assert installed['ok']
        assert call('/api/plugins')[1]['installed'][0]['id']=='discord'
        assert not state.gateway.status()['running']
    finally:state.close();server.shutdown();server.server_close();thread.join(timeout=2)
