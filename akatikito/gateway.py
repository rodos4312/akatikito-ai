"""Detached channel gateway: ingress -> durable admission -> isolated agent -> delivery."""
from __future__ import annotations
import copy,json,secrets,threading,time,hashlib
from dataclasses import replace
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .channel_sdk import Inbound,RetryAfter
from .gateway_store import GatewayStore
from .channel_plugins import Plugins
from .channel_config import ChannelConfig
from .local_store import save_json
from .process_lock import FileLock
from .settings import Settings,DEFAULT_PATH

class Gateway:
    def __init__(self,directory,settings=None,agent_factory=None,adapter_loader=None):
        self.directory=Path(directory);self.settings=settings or Settings.load()
        self.plugins=Plugins(self.directory/'plugins');self.configs=ChannelConfig(self.directory/'channels.json').load(secrets=True)
        installed=self.plugins.installed()
        self.bindings={name:hashlib.sha256(json.dumps([config,installed.get(name,{}).get('review','')],sort_keys=True).encode()).hexdigest() for name,config in self.configs.items()}
        self.identities={}
        self.store=GatewayStore(self.directory/'gateway'/'queue.sqlite3')
        self.stop_event=threading.Event();self.adapters={};self.channel_status={};self.threads=[]
        self.active={};self.active_lock=threading.RLock();self.dispatch_lock=threading.Lock();self.scheduled=set()
        self.agent_factory=agent_factory;self.adapter_loader=adapter_loader
    @staticmethod
    def authorized(event,config):
        return (isinstance(event,Inbound) and event.valid() and event.user in config['allowed_users']
                and (not event.group or event.chat in config.get('allowed_chats',[])))
    def receive(self,ident,bot,cursor,events):
        config=self.configs[ident];self.identities[ident]=bot
        accepted=[replace(e,binding=self.bindings[ident]) for e in events
                  if isinstance(e,Inbound) and e.platform==ident and e.bot==bot and self.authorized(e,config)]
        return self.store.admit(ident+':'+bot,cursor,accepted)

    def matches_binding(self,event):
        return (self.configs.get(event.platform,{}).get('enabled')
                and event.binding==self.bindings.get(event.platform)
                and event.bot==self.identities.get(event.platform))

    def poll_channel(self,ident,config):
        adapter=None;bot='';failures=0
        while not self.stop_event.is_set():
            try:
                if adapter is None:
                    adapter=(self.adapter_loader or self.plugins.load)(ident,config)
                    bot=str(adapter.connect());self.adapters[ident]=adapter;self.identities[ident]=bot
                cursor=self.store.cursor(ident+':'+bot)
                next_cursor,events=adapter.poll(cursor,self.stop_event.is_set)
                accepted=self.receive(ident,bot,next_cursor,events)
                self.channel_status[ident]={'state':'connected','bot':bot};failures=0
                for event in accepted:
                    if event.text.strip()=='/stop':
                        with self.active_lock:active=self.active.get(event.key())
                        if active:active['state'].stop()
                self.stop_event.wait(.05)
            except Exception as exc:
                self.channel_status[ident]={'state':'error','message':'Η σύνδεση δεν ολοκληρώθηκε. Έλεγξε token, δικαιώματα και δίκτυο.'}
                if not bot:
                    if adapter is not None:
                        try:adapter.close()
                        except Exception:pass
                    adapter=None;self.adapters.pop(ident,None)
                failures+=1
                self.stop_event.wait(exc.seconds if isinstance(exc,RetryAfter) else min(60,2**min(6,failures)))
        if adapter is not None:
            try:adapter.close()
            except Exception:pass
    def start(self):
        self.store.recover()
        for ident,config in self.configs.items():
            if not config.get('enabled'):continue
            self.channel_status[ident]={'state':'connecting'}
            thread=threading.Thread(target=self.poll_channel,args=(ident,config),daemon=True);thread.start();self.threads.append(thread)
        for _ in range(4):
            worker=threading.Thread(target=self.work,daemon=True);worker.start();self.threads.append(worker)
    def _state(self,event,config,session):
        from .app_api import AppState
        from .agent import Akatikito
        from .config import Config
        from .model import PROVIDERS
        from .sessions import SessionStore
        state=AppState();state._gateway_worker=True;state.settings=copy.deepcopy(self.settings)
        cfg=Config(model=self.settings.model,base_url=self.settings.base_url or PROVIDERS.get(self.settings.provider,''),
                   api_key=self.settings.key_for(self.settings.provider),max_steps=self.settings.max_steps,
                   verify_command='',checkpoints=self.settings.checkpoints)
        if not cfg.model:raise ValueError('Διάλεξε μοντέλο από τους Παρόχους πριν ξεκινήσεις το gateway.')
        state.agent=(self.agent_factory or Akatikito)(root=config['project'],config=cfg)
        state.agent.events.subscribe(state.emit_event);state._bind_permissions(state.agent)
        original=state.agent.ws.write_guard
        from .app_api import APP_ROOT
        def guard(path,*args,**kwargs):
            try:Path(path).resolve().relative_to(APP_ROOT.resolve())
            except ValueError:return original(path,*args,**kwargs) if original else None
            raise PermissionError('Οι αλλαγές της ίδιας της εφαρμογής γίνονται από την ελεγχόμενη αυτοαναβάθμιση στο παράθυρο.')
        state.agent.ws.write_guard=guard
        state.agent.ws.session_source={k:v for k,v in event.payload().items() if k not in ('text','update')}
        state.store=SessionStore(config['project'])
        data=state.store.load(session)
        if data:
            result=state.open_session(session,config['project'])
            if result.get('error'):raise ValueError(result['error'])
        else:
            origin={k:v for k,v in event.payload().items() if k not in ('text','update')}
            state.store.create(source=origin,session_id=session);state.session_id=session
        mode=config.get('mode','ask')
        if mode=='auto':state.agent.config.auto_mode=True;state.agent._manual_mode=False
        else:state.agent.set_mode(mode)
        return state
    def process(self,row):
        event=Inbound(**json.loads(row['payload']));config=self.configs.get(event.platform,{})
        if not self.authorized(event,config):self.store.finish(row['id'],'Η εξουσιοδότηση ανακλήθηκε.','failed');self.store.delivery(row['id'],'suppressed');return
        command=event.text.strip()
        if command in ('/new','/reset'):
            self.store.route(event,reset=True,before=row['id']);self.store.finish(row['id'],'Ξεκίνησε νέα, χωριστή συνομιλία.');return
        if command=='/help':
            self.store.finish(row['id'],'Akatikito: /new νέα συνομιλία · /status κατάσταση · /stop διακοπή · /skills λίστα συνταγών · /mode [ask|assistant|code|debug|architect|auto] · /learn εκμάθηση skill.');return
        if command=='/status':
            sid=self.store.route(event);self.store.finish(row['id'],'Συνεδρία '+sid+' · '+self.settings.provider+' / '+self.settings.model+' · Λειτουργία: '+str(config.get('mode','ask')));return
        if command=='/stop':self.store.finish(row['id'],'Ζητήθηκε διακοπή της δικής σου εργασίας.');return
        if command=='/skills':
            from .skills import Skills
            cat=Skills(config.get('project','.')).catalog()
            if not cat:
                self.store.finish(row['id'],'Δεν υπάρχουν αποθηκευμένα skills σε αυτό το project.');return
            lines=['Διαθέσιμες συνταγές (στείλε /<όνομα> για εκτέλεση):']
            for s in cat[:25]:
                c_tag=f" [{s['category']}]" if s.get('category') else ''
                lines.append(f"• /{s['name']}{c_tag} — {s['description']}")
            self.store.finish(row['id'],'\n'.join(lines));return
        if command=='/mode' or command.startswith('/mode '):
            from . import modes
            arg=command[5:].strip().lower()
            valid_modes=set(modes.MODES.keys())|{'auto'}
            if not arg:
                cur=config.get('mode','ask')
                self.store.finish(row['id'],f"Τρέχουσα λειτουργία: {cur}. Αλλαγή με /mode <{'|'.join(sorted(valid_modes))}>.");return
            if arg not in valid_modes:
                self.store.finish(row['id'],f"Άγνωστη λειτουργία '{arg}'. Διαθέσιμες: {', '.join(sorted(valid_modes))}.");return
            config['mode']=arg
            lbl='Αυτόματο' if arg=='auto' else modes.label(arg)
            self.store.finish(row['id'],f"Η λειτουργία του καναλιού άλλαξε σε: {lbl} ({arg}).");return
        from .app_api import requests_self_upgrade
        if requests_self_upgrade(event.text,config.get('project','')):
            self.store.finish(row['id'],'Η αυτοαναβάθμιση χρειάζεται το παράθυρο της εφαρμογής και τους ελέγχους του.');return
        session=self.store.route(event);state=None
        try:
            state=self._state(event,config,session)
            with self.active_lock:self.active[event.key()]={'state':state,'event':event}
            result=state.send(event.text)
            if result.get('error'):raise ValueError(result['error'])
            notified=False
            while state.busy:
                if self.stop_event.wait(.1):state.stop();return
                if state.approvals.pending() and not notified:
                    notified=True
                    try:self.adapters[event.platform].send(event,'Χρειάζεται έγκριση στο παράθυρο του Akatikito. Η εργασία περιμένει εκεί.')
                    except Exception:pass
            answers=[m['text'] for m in state.transcript if m.get('role')=='assistant']
            if state.error:raise ValueError('Η εργασία απέτυχε· δες τη συνεδρία στο Akatikito.')
            self.store.finish(row['id'],answers[-1] if answers else 'Η εργασία σταμάτησε χωρίς τελική απάντηση.')
        except Exception:
            self.store.finish(row['id'],'Η εργασία δεν ολοκληρώθηκε. Το ιστορικό διατηρήθηκε· έλεγξέ το στο Akatikito.','failed')
        finally:
            with self.active_lock:self.active.pop(event.key(),None)
            if state:state.close()
    def deliver(self,row):
        event=Inbound(**json.loads(row['payload']));adapter=self.adapters.get(event.platform)
        if adapter is None or not self.matches_binding(event):return
        if not self.authorized(event,self.configs.get(event.platform,{})):
            self.store.delivery(row['id'],'suppressed');return
        if not self.store.claim_delivery(row['id']):return
        try:adapter.send(event,row['reply'])
        except Exception:self.store.delivery(row['id'],'failed')
        else:self.store.delivery(row['id'],'sent')
    def work(self):
        while not self.stop_event.is_set():
            for row in self.store.outbox():
                if self.stop_event.is_set():return
                self.deliver(row)
            with self.dispatch_lock:
                project=lambda event:self.configs.get(event.platform,{}).get('project','')
                row=self.store.next(lambda event:self.matches_binding(event) and project(event) not in self.scheduled)
                if row is not None:
                    key=project(Inbound(**json.loads(row['payload'])));self.scheduled.add(key)
            if row is None:self.stop_event.wait(.15);continue
            try:self.process(row)
            finally:
                with self.dispatch_lock:self.scheduled.discard(key)
    def status(self):
        with self.active_lock:active=list(self.active.values())
        queue=self.store.summary()
        queue['held']=sum(not self.matches_binding(Inbound(**json.loads(row['payload']))) for row in self.store.pending())
        return {'running':not self.stop_event.is_set(),'channels':dict(self.channel_status),
                'provider':self.settings.provider,'model':self.settings.model,
                'queue':queue,'active':[r['event'].payload() for r in active],
                'approvals':[p for r in active for p in r['state'].approvals.pending()]}
    def stop(self):
        self.stop_event.set()
        with self.active_lock:active=list(self.active.values())
        for row in active:row['state'].stop()
    def close(self):
        self.stop()
        for thread in self.threads:thread.join(timeout=2)
        for adapter in list(self.adapters.values()):
            try:adapter.close()
            except Exception:pass

def serve(directory):
    directory=Path(directory);runtime=Gateway(directory);token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def answer(self,data,code=200):
            body=json.dumps(data,ensure_ascii=False).encode();self.send_response(code)
            self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def valid(self):
            expected='127.0.0.1:'+str(self.server.server_port)
            return (self.headers.get('Host')==expected and not self.headers.get('Origin')
                    and secrets.compare_digest(self.headers.get('X-Akatikito-Gateway',''),token))
        def do_GET(self):
            if not self.valid():return self.answer({'error':'Απαγορεύεται.'},403)
            if self.path=='/status':return self.answer(runtime.status())
            return self.answer({'error':'Άγνωστη διαδρομή.'},404)
        def do_POST(self):
            if not self.valid():return self.answer({'error':'Απαγορεύεται.'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16000 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='application/json':raise ValueError()
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError()
            except (ValueError,UnicodeError):return self.answer({'error':'Μη έγκυρο αίτημα.'},400)
            if self.path=='/stop':runtime.stop();return self.answer({'ok':True})
            if self.path=='/approval':
                with runtime.active_lock:active=list(runtime.active.values())
                return self.answer({'ok':any(row['state'].approvals.respond(data.get('id',''),data.get('allow') is True,False) for row in active)})
            return self.answer({'error':'Άγνωστη διαδρομή.'},404)
    import os
    with FileLock(directory/'gateway'/'process.lock',timeout=0):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        descriptor=directory/'gateway'/'control.json'
        save_json(descriptor,{'url':'http://127.0.0.1:'+str(server.server_port),'token':token,'pid':os.getpid()})
        server.timeout=.2
        try:
            runtime.start()
            while not runtime.stop_event.is_set():server.handle_request()
        finally:
            runtime.close();server.server_close();descriptor.unlink(missing_ok=True)

if __name__=='__main__':
    try:serve(DEFAULT_PATH.parent)
    except Exception as exc:
        print('Gateway stopped: '+type(exc).__name__)
        raise SystemExit(1)
