"""Per-channel owner configuration. Tokens never leave the protected configuration API."""
from __future__ import annotations
import copy,json,re
from pathlib import Path
from .settings import mask_secret,unmask_secret
from .local_store import load_json,save_json
from .channel_plugins import ID

class ChannelConfig:
    def __init__(self,path):self.path=Path(path)
    def load(self,secrets=False):
        data=load_json(self.path,dict)
        if not isinstance(data,dict):raise ValueError('Μη έγκυρες ρυθμίσεις καναλιών.')
        result=copy.deepcopy(data)
        for ident,row in result.items():
            if secrets:row['token']=unmask_secret(row.get('token',''))
            else:
                row['has_token']=bool(row.pop('token',''));row.pop('api_key',None)
        return result
    def save(self,ident,data,installed):
        if not ID.fullmatch(str(ident)) or ident not in installed:raise ValueError('Εγκατάστησε πρώτα το πρόσθετο.')
        if not isinstance(data,dict):raise ValueError('Μη έγκυρη ρύθμιση.')
        current=load_json(self.path,dict);old=current.get(ident,{})
        users=data.get('allowed_users',[]);chats=data.get('allowed_chats',[])
        if (not isinstance(users,list) or not 1<=len(users)<=100 or not isinstance(chats,list) or len(chats)>100
                or not all(isinstance(x,str) and re.fullmatch(r'-?[0-9]{1,24}',x) for x in users+chats)):
            raise ValueError('Χρειάζεται τουλάχιστον ένα αριθμητικό ID χρήστη. Τα ID συνομιλιών πρέπει επίσης να είναι αριθμητικά.')
        token=data.get('token','')
        if not isinstance(token,str) or len(token)>512 or any(x.isspace() for x in token):raise ValueError('Μη έγκυρο bot token.')
        if not token and not old.get('token'):raise ValueError('Χρειάζεται bot token.')
        root=Path(data.get('project','')).expanduser()
        if not root.is_absolute() or not root.is_dir():raise ValueError('Διάλεξε υπαρκτό απόλυτο φάκελο project.')
        mode=data.get('mode','ask')
        if mode not in ('ask','assistant','code','debug','auto'):raise ValueError('Μη έγκυρη λειτουργία καναλιού.')
        if type(data.get('enabled',False)) is not bool:raise ValueError('Μη έγκυρη κατάσταση καναλιού.')
        current[ident]={'token':mask_secret(token) if token else old['token'],'allowed_users':list(dict.fromkeys(users)),
                        'allowed_chats':list(dict.fromkeys(chats)),'project':str(root.resolve()),'mode':mode,'enabled':data.get('enabled',False)}
        save_json(self.path,current);return self.load()
