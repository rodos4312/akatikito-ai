"""Local usage ledger. Prices are explicit owner estimates, not invented rates."""
from __future__ import annotations
import json,math,os,threading
from .process_lock import FileLock
from datetime import date
from pathlib import Path


class UsageLedger:
    def __init__(self,path):self.path=Path(path);self.lock=FileLock(self.path.with_suffix('.lock'))
    def load(self):
        if not self.path.exists():return {'days':{},'sessions':{}}
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or not isinstance(value.get('days'),dict) or not isinstance(value.get('sessions'),dict):raise ValueError('Μη έγκυρο αρχείο κατανάλωσης.')
        return value
    def view(self,session,day=None):
        with self.lock:
            data=self.load();day=day or date.today().isoformat()
            return {'day':data['days'].get(day,{}),'session':data['sessions'].get(session,{})}
    def check(self,budget,session,prices=None):
        if not budget:return ''
        view=self.view(session)['day']
        if not prices or any(k not in prices for k in ('input','cached','output')) or view.get('unknown',0):return 'Το ημερήσιο όριο κόστους χρειάζεται τιμές μοντέλου και πλήρως αποτιμημένη σημερινή χρήση.'
        if view.get('cost',0)>=budget:return 'Έφτασες το ημερήσιο όριο κόστους. Δεν έγινε νέα κλήση μοντέλου.'
        return ''
    def record(self,session,usage,prices=None,day=None):
        values={k:max(0,int(usage.get(k,0) or 0)) for k in ('prompt_tokens','completion_tokens','cache_hit_tokens')}
        hit=min(values['prompt_tokens'],values['cache_hit_tokens'])
        cost=None
        if prices and all(k in prices and isinstance(prices[k],(int,float)) and math.isfinite(prices[k]) and prices[k]>=0 for k in ('input','cached','output')):
            cost=((values['prompt_tokens']-hit)*prices['input']+hit*prices['cached']+values['completion_tokens']*prices['output'])/1_000_000
        with self.lock:
            data=self.load();day=day or date.today().isoformat()
            for bucket,key in (('days',day),('sessions',session)):
                row=data[bucket].setdefault(key,{'prompt_tokens':0,'completion_tokens':0,'cache_hit_tokens':0,'calls':0,'cost':0.0,'unknown':0})
                for k,v in values.items():row[k]+=v
                row['calls']+=1;row['cost']+=cost or 0;row['unknown']+=int(cost is None)
            self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,self.path)
        return cost


def summarize_turn_delta(before: dict, after: dict, prices: dict | None = None) -> dict:
    """Compute per-answer token delta, cache-hit percentage, and optional EUR cost."""
    prompt = max(0, int(after.get('prompt_tokens', 0) or 0) - int(before.get('prompt_tokens', 0) or 0))
    comp = max(0, int(after.get('completion_tokens', 0) or 0) - int(before.get('completion_tokens', 0) or 0))
    hit = min(prompt, max(0, int(after.get('cache_hit_tokens', 0) or 0) - int(before.get('cache_hit_tokens', 0) or 0)))
    pct = round(100 * hit / prompt) if prompt > 0 and hit > 0 else 0
    cost = None
    if prices and all(k in prices and isinstance(prices[k], (int, float)) and math.isfinite(prices[k]) and prices[k] >= 0 for k in ('input', 'cached', 'output')):
        cost = round(((prompt - hit) * prices['input'] + hit * prices['cached'] + comp * prices['output']) / 1_000_000, 5)
    return {'prompt_tokens': prompt, 'completion_tokens': comp, 'cache_hit_tokens': hit, 'cache_hit_pct': pct, 'cost_eur': cost}
