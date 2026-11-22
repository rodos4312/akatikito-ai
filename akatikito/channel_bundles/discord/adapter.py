"""Discord Gateway v10 + REST connector, with resumable transport and heartbeat."""
import json,time
from urllib.parse import urlparse
from akatikito.channel_sdk import HTTP,Inbound,ChannelError,chunks

class Discord:
    def __init__(self,config,http=None,connect_ws=None):
        self.token=config['token'];self.http=http or HTTP('https://discord.com/api/v10',{'Authorization':'Bot '+self.token})
        self.ws=None;self.bot='';self.seq=None;self.session=None;self.resume_url=None
        self.interval=30;self.heartbeat=0;self.acked=True;self.connect_ws=connect_ws
    def connect(self):
        body=self.http.request('GET','/users/@me')
        if not body.get('bot') or not body.get('id'):raise ChannelError('Δεν αναγνωρίστηκε το Discord bot.')
        self.bot=str(body['id']);return self.bot
    def _open(self):
        if self.connect_ws is None:
            from websockets.sync.client import connect
            connect_ws=connect
        else:connect_ws=self.connect_ws
        url=self.resume_url or 'wss://gateway.discord.gg'
        parsed=urlparse(url)
        if parsed.scheme!='wss' or not (parsed.hostname=='gateway.discord.gg' or (parsed.hostname or '').endswith('.discord.gg')) or parsed.username or parsed.password:
            raise ChannelError('Μη έγκυρη διεύθυνση Discord Gateway.')
        self.ws=connect_ws(url.rstrip('/')+'/?v=10&encoding=json',open_timeout=15,max_size=2_000_000,proxy=None,close_timeout=3)
        hello=json.loads(self.ws.recv(timeout=15))
        if hello.get('op')!=10:raise ChannelError('Μη έγκυρη έναρξη Discord Gateway.')
        self.interval=max(.1,float(hello['d']['heartbeat_interval'])/1000);self.heartbeat=time.monotonic();self.acked=True
        if self.session and self.seq is not None:
            self._send(6,{'token':self.token,'session_id':self.session,'seq':self.seq})
        else:
            self._send(2,{'token':self.token,'intents':1|512|4096|32768,
                         'properties':{'os':'windows','browser':'Akatikito','device':'Akatikito'}})
    def _send(self,op,data):self.ws.send(json.dumps({'op':op,'d':data}))
    def poll(self,cursor,stopped):
        try:
            if self.ws is None:self._open()
            if time.monotonic()>=self.heartbeat:
                if not self.acked:raise ChannelError('Δεν επιβεβαιώθηκε ο παλμός Discord.')
                self._send(1,self.seq);self.acked=False;self.heartbeat=time.monotonic()+self.interval
            try:payload=json.loads(self.ws.recv(timeout=min(1,max(.05,self.heartbeat-time.monotonic()))))
            except TimeoutError:return cursor,[]
            op=payload.get('op')
            if op==11:self.acked=True;return cursor,[]
            if op==1:self._send(1,self.seq);return cursor,[]
            if op in (7,9):
                if op==9 and not payload.get('d'):self.session=None;self.seq=None;self.resume_url=None
                self.ws.close();self.ws=None;return cursor,[]
            if payload.get('s') is not None:self.seq=payload['s']
            if payload.get('t')=='READY':
                self.session=payload['d']['session_id'];self.resume_url=payload['d'].get('resume_gateway_url');return cursor,[]
            if payload.get('t')!='MESSAGE_CREATE':return cursor,[]
            row=payload['d'];user=row.get('author') or {}
            if user.get('bot') or row.get('webhook_id') or not user.get('id'):return cursor,[]
            content=str(row.get('content') or '')
            atts=[a.get('filename') or 'attachment' for a in (row.get('attachments') or []) if isinstance(a,dict)]
            if atts:
                content=('[Συνημμένα Discord: '+', '.join(atts)+']'+('\n'+content if content else '')).strip()
            if not content:return cursor,[]
            event=Inbound('discord',self.bot,str(row['channel_id']),str(user['id']),str(row['id']),content,
                          '',bool(row.get('guild_id')))
            return cursor,[event]
        except Exception:
            if self.ws is not None:
                try:self.ws.close()
                except Exception:pass
            self.ws=None
            raise ChannelError('Η σύνδεση Discord διακόπηκε· θα γίνει επανασύνδεση.') from None
    def send(self,event,text):
        for part in chunks(text,1900):
            self.http.request('POST','/channels/'+event.chat+'/messages',{'content':part,'allowed_mentions':{'parse':[]}})
    def close(self):
        if self.ws is not None:self.ws.close()
        self.http.close()

def create(config):return Discord(config)
