"""Telegram Bot API connector. No model invocation exists in a connector."""
from akatikito.channel_sdk import HTTP,Inbound,ChannelError,chunks

class Telegram:
    def __init__(self,config,http=None):
        self.http=http or HTTP('https://api.telegram.org/bot'+config['token']);self.bot=''
    def connect(self):
        result=self.http.request('POST','/getMe',{})
        if not result.get('ok') or not result.get('result',{}).get('is_bot'):raise ChannelError('Δεν αναγνωρίστηκε το Telegram bot.')
        self.bot=str(result['result']['id']);return self.bot
    def poll(self,cursor,stopped):
        body=self.http.request('POST','/getUpdates',{'offset':int(cursor or 0),'timeout':20,'limit':50,'allowed_updates':['message']})
        if not body.get('ok'):raise ChannelError('Αποτυχία παραλαβής Telegram.')
        events=[];next_cursor=int(cursor or 0)
        for row in sorted(body.get('result',[]),key=lambda r:r.get('update_id',0)):
            ident=row.get('update_id')
            if type(ident) is not int:continue
            next_cursor=max(next_cursor,ident+1)
            msg=row.get('message') or {};user=msg.get('from') or {};chat=msg.get('chat') or {}
            if user.get('is_bot') or not user.get('id') or not chat.get('id'):continue
            text=msg.get('text') or msg.get('caption') or ''
            media=[]
            if isinstance(msg.get('document'),dict):
                doc=msg['document']
                media.append(f"[Συνημμένο έγγραφο Telegram: {doc.get('file_name') or 'document'} ({doc.get('mime_type') or 'file'})]")
            if isinstance(msg.get('voice'),dict):
                v=msg['voice']
                media.append(f"[Φωνητικό μήνυμα Telegram: {v.get('duration',0)}s]")
            elif isinstance(msg.get('audio'),dict):
                a=msg['audio']
                media.append(f"[Αρχείο ήχου Telegram: {a.get('file_name') or a.get('title') or 'audio'}]")
            if isinstance(msg.get('photo'),list) and msg.get('photo'):
                media.append("[Συνημμένη εικόνα Telegram]")
            if media:
                text=("\n".join(media)+("\n"+text if text else "")).strip()
            if not text:continue
            events.append(Inbound('telegram',self.bot,str(chat['id']),str(user['id']),str(ident),text,
                                  str(msg.get('message_thread_id') or ''),chat.get('type')!='private'))
        return str(next_cursor),events
    def send(self,event,text):
        for part in chunks(text,3500):
            data={'chat_id':event.chat,'text':part}
            if event.thread:data['message_thread_id']=int(event.thread)
            body=self.http.request('POST','/sendMessage',data)
            if not body.get('ok'):raise ChannelError('Η απάντηση Telegram δεν επιβεβαιώθηκε.')
    def close(self):self.http.close()

def create(config):return Telegram(config)
