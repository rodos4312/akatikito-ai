"""Channel API v1: connectors transport messages, the gateway owns agent sessions."""
from __future__ import annotations
from dataclasses import dataclass,asdict
import json
from urllib.parse import urlparse
import httpx

@dataclass(frozen=True)
class Inbound:
    platform:str
    bot:str
    chat:str
    user:str
    update:str
    text:str
    thread:str=''
    group:bool=False
    binding:str=''  # Set by the gateway, never trusted from a connector.
    def valid(self):
        return (all(isinstance(x,str) and 0<len(x)<=80 for x in
                    (self.platform,self.bot,self.chat,self.user,self.update))
                and isinstance(self.thread,str) and len(self.thread)<=80
                and isinstance(self.text,str) and 0<len(self.text.strip())<=12000
                and type(self.group) is bool and isinstance(self.binding,str) and len(self.binding)<=64)
    def key(self):
        # Identity includes connector, bot, room, topic AND sender. No implicit history sharing.
        return json.dumps([self.platform,self.bot,self.chat,self.thread,self.user,self.binding],separators=(',',':'))
    def payload(self):return asdict(self)

class ChannelError(RuntimeError):pass
class RetryAfter(ChannelError):
    def __init__(self,seconds):self.seconds=max(1,min(300,float(seconds)));super().__init__('Προσωρινό όριο υπηρεσίας.')

class HTTP:
    def __init__(self,base,headers=None):
        self.base=base.rstrip('/');self.client=httpx.Client(headers=headers or {},timeout=35,follow_redirects=False,trust_env=False)
    def request(self,method,path,data=None):
        try:
            with self.client.stream(method,self.base+path,json=data) as response:
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>2_000_000:raise ChannelError('Πολύ μεγάλη απόκριση καναλιού.')
            body=json.loads(raw)
            if not isinstance(body,dict):raise ChannelError('Μη έγκυρη απόκριση υπηρεσίας.')
            if response.status_code==429:raise RetryAfter(body.get('retry_after',body.get('parameters',{}).get('retry_after',2)))
            if response.status_code>=300:raise ChannelError('Η υπηρεσία απέρριψε το αίτημα (HTTP '+str(response.status_code)+').')
            return body
        except (httpx.HTTPError,ValueError) as exc:
            # Never expose a token-bearing URL, request headers or response body.
            raise ChannelError('Αποτυχία επικοινωνίας με την υπηρεσία.') from None
    def close(self):self.client.close()

def chunks(text,limit):
    text=str(text or '(χωρίς απάντηση)')
    for start in range(0,len(text),limit):yield text[start:start+limit]
