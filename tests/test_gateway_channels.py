"""Offline contract tests for plugin review/install, transports, identity and durable execution."""
import copy,json,sqlite3,threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from akatikito.channel_sdk import Inbound,ChannelError,RetryAfter,HTTP
from akatikito.channel_plugins import Plugins,PluginError,manifest,fingerprint
from akatikito.channel_config import ChannelConfig
from akatikito.gateway_store import GatewayStore
from akatikito.gateway import Gateway


def event(**changes):
    values=dict(platform='telegram',bot='100',chat='10',user='10',update='1',text='γεια')
    values.update(changes);return Inbound(**values)

def config(project):return {'token':'synthetic-token','allowed_users':['10','11'],'allowed_chats':['-20'],'project':str(project),'mode':'ask','enabled':True}

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    from akatikito import settings,memory,app_api
    directory=tmp_path/'data';directory.mkdir();project=tmp_path/'project';project.mkdir()
    monkeypatch.setattr(settings,'DEFAULT_PATH',directory/'settings.json');monkeypatch.setattr(memory,'DIR',directory/'memory')
    owner=settings.Settings(provider='Custom',base_url='http://127.0.0.1:9/v1',model='scripted',project=str(project),checkpoints=False)
    owner.save();return directory,project,owner

@pytest.mark.parametrize('name',['../escape.py','/escape.py','C:/escape.py','a\\escape.py','a//b.py','.hidden/x.py','a/../b.py','adapter.exe'])
def test_manifest_refuses_unsafe_files(name):
    info={'id':'test','name':'Test','version':'1','kind':'channel','api_version':1,'entry':name,'files':[name],'permissions':['network']}
    with pytest.raises(PluginError):manifest(json.dumps(info))

def test_review_is_required_and_exact_bytes_are_bound(tmp_path):
    plugins=Plugins(tmp_path/'plugins')
    draft=plugins.preview('catalog:telegram')
    assert plugins.installed()=={}
    assert draft['manifest']['kind']=='channel' and 'token' not in draft
    with pytest.raises(PluginError):plugins.install(draft['id'],'wrong-review')
    installed=plugins.install(draft['id'],draft['review'])
    assert installed['source']=='catalog:telegram' and installed['review']==draft['review']
    loaded=plugins.load('telegram',{'token':'synthetic'})
    loaded.close()
    entry=tmp_path/'plugins/packages/telegram'/draft['review']/'adapter.py'
    entry.write_text(entry.read_text(encoding='utf-8')+'\n# unreviewed edit',encoding='utf-8')
    with pytest.raises(PluginError,match='άλλαξε'):plugins.load('telegram',{'token':'synthetic'})
    plugins.remove('telegram');assert plugins.installed()=={}


def test_tampered_draft_and_incompatible_package_are_rejected(tmp_path):
    plugins=Plugins(tmp_path/'plugins');draft=plugins.preview('catalog:discord')
    p=tmp_path/'plugins/drafts'/(draft['id']+'.json');value=json.loads(p.read_text(encoding='utf-8'))
    value['parts']['adapter.py']='raise RuntimeError("must not execute")';p.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(PluginError):plugins.install(draft['id'],draft['review'])
    with pytest.raises(PluginError):manifest('{"name":"hermes-plugin","kind":"platform"}')


def test_github_review_resolves_commit_and_executes_only_after_install(tmp_path,monkeypatch):
    import httpx,base64
    calls=[];sha='a'*40
    info={'id':'example','name':'Example','version':'1','kind':'channel','api_version':1,'entry':'adapter.py','files':['adapter.py'],'permissions':['example network']}
    parts={'akatikito-plugin.json':json.dumps(info),'adapter.py':'def create(config):\n    raise RuntimeError("executed only by load")\n'}
    def handler(request):
        calls.append(str(request.url));assert 'authorization' not in request.headers
        if request.url.path.endswith('/commits/main'):return httpx.Response(200,json={'sha':sha})
        name=request.url.path.rsplit('/',1)[-1];assert request.url.params['ref']==sha
        return httpx.Response(200,json={'type':'file','encoding':'base64','size':len(parts[name]),'content':base64.b64encode(parts[name].encode()).decode()})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    p=Plugins(tmp_path/'plugins');draft=p.preview('https://github.com/example/project/tree/main/package')
    assert sha in draft['source'] and len(calls)==3
    p.install(draft['id'],draft['review'])
    with pytest.raises(RuntimeError,match='executed only'):p.load('example',{})
    for url in ('http://github.com/a/b','https://evil.example/a/b','https://github.com/a/b?token=secret','https://github.com/a/b/tree/main/../x'):
        with pytest.raises(PluginError):p.preview(url)


def test_config_keeps_token_private_and_requires_owner_ids(isolated):
    directory,project,owner=isolated;p=Plugins(directory/'plugins');d=p.preview('catalog:telegram');p.install(d['id'],d['review'])
    cfg=ChannelConfig(directory/'channels.json');values=config(project)
    with pytest.raises(ValueError):cfg.save('telegram',{**values,'allowed_users':[]},p.installed())
    cfg.save('telegram',values,p.installed());assert 'synthetic-token' not in cfg.path.read_text(encoding='utf-8')
    view=cfg.load();assert view['telegram']['has_token'] and 'token' not in view['telegram']
    cfg.save('telegram',{**values,'token':''},p.installed());assert cfg.load(True)['telegram']['token']=='synthetic-token'

@pytest.mark.parametrize('changes',[
 {'chat':'11'},{'platform':'discord'},{'bot':'101'},{'user':'11'},
 {'group':True,'chat':'-20'},{'thread':'9'}])
def test_every_identity_dimension_isolates_history(changes):assert event().key()!=event(**changes).key()


def test_allowlist_and_explicit_groups(isolated):
    _,project,_=isolated;cfg=config(project)
    assert Gateway.authorized(event(),cfg)
    assert not Gateway.authorized(event(user='99'),cfg)
    assert not Gateway.authorized(event(group=True,chat='-99'),cfg)
    assert Gateway.authorized(event(group=True,chat='-20'),cfg)
    assert not Gateway.authorized(event(text=''),cfg)


def test_admission_dedup_cursor_and_crash_recovery(tmp_path):
    store=GatewayStore(tmp_path/'queue.db');first=event();store.admit('telegram:100','2',[first,first])
    row=store.next();assert row and store.next() is None and store.cursor('telegram:100')=='2'
    sid=store.route(first);assert GatewayStore(store.path).route(first)==sid
    store.recover();assert store.summary()['counts']['unknown']==1
    store.admit('telegram:100','4',[event(update='2',text='old follower'),event(update='3',text='/new')])
    reset=store.next();assert json.loads(reset['payload'])['text']=='/new'
    new=store.route(Inbound(**json.loads(reset['payload'])),reset=True,before=reset['id'])
    assert new!=sid and store.next() is None
    store.finish(reset['id'],'new session');store.delivery(reset['id'],'sending');store.recover()
    assert store.summary()['issues'][0]['delivery']=='unknown'
    assert store.outbox()==[]


def test_delivery_failure_does_not_repeat_agent(isolated):
    directory,project,owner=isolated;ChannelConfig(directory/'channels.json').save('telegram',config(project),{'telegram':{}})
    g=Gateway(directory,owner);g.receive('telegram','100','2',[event(text='/help')]);row=g.store.next();g.process(row)
    class Broken:
        def send(self,*args):raise TimeoutError('network unknown')
    g.adapters['telegram']=Broken();g.deliver(g.store.outbox()[0]);g.store.recover()
    assert not g.store.outbox() and not g.store.next()
    assert g.store.summary()['issues'][0]['delivery']=='failed'


def test_real_agent_sessions_resume_without_cross_chat_history(isolated):
    from akatikito.agent import Akatikito
    from akatikito.model import ScriptedModel
    from akatikito.sessions import SessionStore
    directory,project,owner=isolated;ChannelConfig(directory/'channels.json').save('telegram',config(project),{'telegram':{}})
    seen=[]
    class Model(ScriptedModel):
        def chat(self,messages,tools=None,**kwargs):
            seen.append(copy.deepcopy(messages));return {'content':'τοπική απάντηση','tool_calls':[]},{'prompt_tokens':3,'completion_tokens':2}
    def factory(root,config):return Akatikito(root,config,model=Model([]))
    g=Gateway(directory,owner,agent_factory=factory)
    for e in (event(text='secret alpha'),event(chat='11',user='11',update='2',text='secret beta'),event(update='3',text='continue alpha')):
        g.receive('telegram','100',str(int(e.update)+1),[e]);g.process(g.store.next())
    assert len(seen)==3 and g.store.summary()['counts']=={'done':3}
    users=lambda rows:[m['content'] for m in rows if m.get('role')=='user' and not str(m.get('content','')).startswith('[ΚΑΤΑΣΤΑΣΗ ΕΦΑΡΜΟΓΗΣ:')]
    assert users(seen[0])==['secret alpha'] and users(seen[1])==['secret beta']
    assert users(seen[2])==['secret alpha','continue alpha']
    rows=SessionStore(project).list();assert len(rows)==2 and all(r['source']['platform']=='telegram' for r in rows)
    g2=Gateway(directory,owner,agent_factory=factory);g2.receive('telegram','100','5',[event(update='4',text='after restart')]);g2.process(g2.store.next())
    assert users(seen[-1])==['secret alpha','continue alpha','after restart']
    assert g2.store.summary()['counts']=={'done':4}
    from akatikito.app_api import AppState
    desktop=AppState();before=(desktop.agent,desktop.session_id)
    preview=desktop.channel_session(rows[0]['id'],str(project));assert preview['readonly'] and preview['transcript']
    assert (desktop.agent,desktop.session_id)==before
    assert desktop.open_session(rows[0]['id'],str(project))['error'];desktop.close()


def test_poll_filters_unauthorized_before_queue_and_stop_is_scoped(isolated):
    directory,project,owner=isolated;ChannelConfig(directory/'channels.json').save('telegram',config(project),{'telegram':{}})
    class Adapter:
        def connect(self):return '100'
        def poll(self,cursor,stopped):
            g.stop_event.set()
            return '9',[event(user='99',update='2'),event(bot='999',update='3'),event(platform='discord',update='4'),event(update='5',text='/stop')]
        def close(self):pass
    g=Gateway(directory,owner,adapter_loader=lambda *args:Adapter());stops=[]
    g.active[event(user='11').key()]={'state':SimpleNamespace(stop=lambda:stops.append(True)),'event':event(user='11')}
    g.poll_channel('telegram',config(project));assert stops==[]
    assert g.store.summary()['counts']=={'queued':1}


def test_telegram_normalization_topics_cursor_and_plain_reply():
    import importlib.util
    from akatikito.channel_plugins import BUNDLES
    spec=importlib.util.spec_from_file_location('test_tg',BUNDLES/'telegram/adapter.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    calls=[]
    class FakeHTTP:
        def request(self,method,path,data=None):
            calls.append((path,data))
            if path=='/getMe':return {'ok':True,'result':{'id':100,'is_bot':True}}
            if path=='/getUpdates':return {'ok':True,'result':[
                {'update_id':3,'message':{'from':{'id':10},'chat':{'id':-20,'type':'supergroup'},'message_thread_id':9,'text':'hello'}},
                {'update_id':4,'message':{'from':{'id':100,'is_bot':True},'chat':{'id':-20},'text':'ignore'}}]}
            return {'ok':True,'result':{}}
        def close(self):pass
    adapter=mod.Telegram({'token':'secret'},http=FakeHTTP());assert adapter.connect()=='100'
    cursor,events=adapter.poll('3',lambda:False);assert cursor=='5' and len(events)==1
    assert events[0].thread=='9' and events[0].group
    adapter.send(events[0],'<untrusted> '+('x'*4000))
    sent=[d for p,d in calls if p=='/sendMessage'];assert len(sent)==2 and sent[0]['message_thread_id']==9 and 'parse_mode' not in sent[0]


def test_discord_identify_heartbeat_resume_and_mentions():
    import importlib.util
    from akatikito.channel_plugins import BUNDLES
    spec=importlib.util.spec_from_file_location('test_dc',BUNDLES/'discord/adapter.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    sent=[];rest=[]
    class WS:
        def __init__(self):self.rows=[{'op':10,'d':{'heartbeat_interval':1000}}, {'op':0,'s':1,'t':'READY','d':{'session_id':'session','resume_gateway_url':'wss://gateway.discord.gg'}},
          {'op':11}, {'op':0,'s':2,'t':'MESSAGE_CREATE','d':{'id':'400','channel_id':'20','guild_id':'30','author':{'id':'10'},'content':'hello'}}]
        def recv(self,**kwargs):return json.dumps(self.rows.pop(0))
        def send(self,value):sent.append(json.loads(value))
        def close(self):pass
    class Rest:
        def request(self,method,path,data=None):rest.append((path,data));return {'id':'100','bot':True}
        def close(self):pass
    sockets=[]
    def connect(*args,**kwargs):ws=WS();sockets.append(ws);return ws
    adapter=mod.Discord({'token':'synthetic'},http=Rest(),connect_ws=connect);assert adapter.connect()=='100'
    adapter.poll('',lambda:False);adapter.poll('',lambda:False);_,events=adapter.poll('',lambda:False)
    assert events[0].platform=='discord' and events[0].group and sent[0]['op']==2
    assert any(x['op']==1 for x in sent)
    adapter.send(events[0],'@everyone hello');assert rest[-1][1]['allowed_mentions']=={'parse':[]}
    adapter.ws=None;adapter._open();assert sent[-1]['op']==6 and sent[-1]['d']['seq']==2


def test_transport_error_redacts_token_and_redirect_is_not_followed(monkeypatch):
    import httpx
    token='very-secret-token';original=httpx.Client;calls=[]
    def handler(request):calls.append(request);return httpx.Response(302,headers={'Location':'https://evil.example/'+token},json={})
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    transport=HTTP('https://api.telegram.org/bot'+token)
    with pytest.raises(ChannelError) as exc:transport.request('POST','/getMe',{})
    assert token not in str(exc.value) and len(calls)==1
    transport.close()


def test_desktop_project_locks_and_usage_are_shared_across_processes(tmp_path):
    import subprocess,sys,os
    from akatikito.process_lock import FileLock
    path=tmp_path/'lease';lock=FileLock(path)
    code='from akatikito.process_lock import FileLock\nimport sys\ntry:\n with FileLock(sys.argv[1],timeout=.1): print("unlocked")\nexcept TimeoutError: print("locked")'
    with lock:
        run=subprocess.run([sys.executable,'-B','-c',code,str(path)],capture_output=True,text=True,timeout=10)
        assert run.returncode==0 and run.stdout.strip()=='locked'
    assert subprocess.run([sys.executable,'-B','-c',code,str(path)],capture_output=True,text=True,timeout=10).stdout.strip()=='unlocked'
    assert not path.exists()  # Never checkpoint a locked operating-system handle.


def test_parallel_projects_and_same_session_fifo(isolated):
    from akatikito.agent import Akatikito
    from akatikito.model import ScriptedModel
    directory,project,owner=isolated;other=project.parent/'other';other.mkdir()
    channel_config=ChannelConfig(directory/'channels.json')
    channel_config.save('telegram',config(project),{'telegram':{},'discord':{}})
    channel_config.save('discord',config(other),{'telegram':{},'discord':{}})
    both=threading.Barrier(2);seen=[];seen_lock=threading.Lock();states=[]
    class Model(ScriptedModel):
        def chat(self,messages,tools=None,**kwargs):
            text=next(m['content'] for m in messages if m.get('role')=='user' and not m.get('content','').startswith('[ΚΑΤΑΣΤΑΣΗ ΕΦΑΡΜΟΓΗΣ:'))
            with seen_lock:seen.append(text)
            both.wait(timeout=8);return {'content':'done locally','tool_calls':[]},{'prompt_tokens':3,'completion_tokens':2}
    g=Gateway(directory,owner,agent_factory=lambda root,config:Akatikito(root,config,model=Model([])))
    make_state=g._state
    def capture(*args):
        state=make_state(*args);states.append(state);return state
    g._state=capture
    first=event(text='project alpha');second=event(platform='discord',update='2',text='project beta')
    g.receive('telegram','100','2',[first]);g.receive('discord','100','3',[second]);threads=[threading.Thread(target=g.work,daemon=True) for _ in range(2)]
    for t in threads:t.start()
    deadline=time.monotonic()+12
    while g.store.summary()['counts'].get('done',0)<2 and time.monotonic()<deadline:time.sleep(.05)
    g.stop()
    for t in threads:t.join(timeout=2)
    assert sorted(seen)==['project alpha','project beta'] and g.store.summary()['counts']=={'done':2}, [(s.error,s.transcript) for s in states]
    # At most one input of a given route can be claimed while its predecessor runs.
    store=GatewayStore(directory/'fifo.sqlite3');store.admit('tg','3',[event(),event(update='2')]);row=store.next()
    assert row and store.next() is None;store.finish(row['id'],'done');assert store.next()['update_id']=='2'


def test_reply_claim_is_atomic_between_workers(isolated):
    directory,project,owner=isolated;ChannelConfig(directory/'channels.json').save('telegram',config(project),{'telegram':{}})
    g=Gateway(directory,owner);g.receive('telegram','100','2',[event(text='/help')]);g.process(g.store.next());row=g.store.outbox()[0]
    sent=[]
    class Adapter:
        def send(self,*args):sent.append(True);time.sleep(.05)
    g.adapters['telegram']=Adapter();threads=[threading.Thread(target=g.deliver,args=(row,)) for _ in range(4)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=5)
    assert sent==[True] and not g.store.outbox()


def test_shared_usage_and_memory_transactions_do_not_lose_updates(tmp_path):
    import os,subprocess,sys
    from akatikito.usage_ledger import UsageLedger
    ledger=tmp_path/'usage.json'
    code='from akatikito.usage_ledger import UsageLedger\nimport sys\nl=UsageLedger(sys.argv[1])\nfor i in range(12):l.record(sys.argv[2],{"prompt_tokens":100,"completion_tokens":10},{"input":1,"cached":1,"output":1})'
    jobs=[subprocess.Popen([sys.executable,'-B','-c',code,str(ledger),str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for i in range(2)]
    for job in jobs:
        out,err=job.communicate(timeout=20);assert job.returncode==0,(out,err)
    data=UsageLedger(ledger).view('0');assert data['day']['calls']==24 and data['day']['prompt_tokens']==2400 and data['session']['calls']==12
    memory=tmp_path/'memory';memory.mkdir();target=memory/'MEMORY.md';target.write_bytes(b'original memory\r\n')
    env=os.environ.copy();env['AKATIKITO_MEMORY_DIR']=str(memory)
    code='from akatikito.memory import append_note\nfrom pathlib import Path\nimport sys\nfor i in range(8):append_note(Path(sys.argv[1]),"synthetic preference "+sys.argv[2]+str(i),"saved ","duplicate ")'
    jobs=[subprocess.Popen([sys.executable,'-B','-c',code,str(target),str(i)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for i in range(2)]
    for job in jobs:
        out,err=job.communicate(timeout=20);assert job.returncode==0,(out,err)
    raw=target.read_bytes();assert raw.startswith(b'original memory\r\n') and raw.count(b'synthetic preference')==16


def test_owner_approval_is_visible_and_remote_self_edit_is_guarded(isolated,monkeypatch):
    from akatikito.agent import Akatikito
    from akatikito.model import ScriptedModel
    from akatikito import app_api
    directory,project,owner=isolated;ChannelConfig(directory/'channels.json').save('telegram',config(project),{'telegram':{}})
    g=Gateway(directory,owner,agent_factory=lambda root,config:Akatikito(root,config,model=ScriptedModel([])))
    state=g._state(event(),config(project),g.store.route(event()))
    g.active[event().key()]={'state':state,'event':event()}
    answers=[];worker=threading.Thread(target=lambda:answers.append(state.approvals.request(project,'command',{'command':'synthetic command'})))
    worker.start();deadline=time.monotonic()+3
    while not g.status()['approvals'] and time.monotonic()<deadline:time.sleep(.02)
    pending=g.status()['approvals'];assert len(pending)==1
    state.approvals.respond(pending[0]['id'],False);worker.join(timeout=3);assert answers==[False]
    with pytest.raises(PermissionError):state.agent.ws.write_guard(app_api.APP_ROOT/'akatikito/agent.py','','bad source')
    state.close();g.active.clear()


def test_changed_connection_binding_never_runs_old_pending_messages(isolated):
    from dataclasses import replace
    directory,project,owner=isolated;cfg=ChannelConfig(directory/'channels.json');cfg.save('telegram',config(project),{'telegram':{}})
    first=Gateway(directory,owner);accepted=first.receive('telegram','100','2',[event()]);old=accepted[0]
    other=project.parent/'new-project';other.mkdir();cfg.save('telegram',config(other),{'telegram':{}})
    second=Gateway(directory,owner);second.identities['telegram']='100'
    assert not second.matches_binding(old)
    assert second.store.next(second.matches_binding) is None
    new=second.receive('telegram','100','3',[event(update='2')])[0]
    assert second.matches_binding(new) and old.key()!=new.key()
    assert second.status()['queue']['held']==1
    # Bot identity is also checked before any delivery or model turn.
    second.identities['telegram']='999';assert not second.matches_binding(new)


def test_duplicate_stop_cannot_cancel_a_later_turn(isolated):
    directory,project,owner=isolated;ChannelConfig(directory/'channels.json').save('telegram',config(project),{'telegram':{}})
    g=Gateway(directory,owner)
    assert len(g.receive('telegram','100','2',[event(text='/stop')]))==1
    assert g.receive('telegram','100','2',[event(text='/stop')])==[]
    assert g.store.summary()['counts']=={'queued':1}
