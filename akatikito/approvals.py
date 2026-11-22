"""Owner decisions are request-bound, cancellable and shared with child tasks."""
from __future__ import annotations
import hashlib
import json
import threading
import time
import uuid


from .command_policy import command_risk


class ApprovalBroker:
    def __init__(self, notify=lambda:None, remember=lambda value:None, allowed=(), timeout=600):
        self.notify, self.remember = notify, remember
        self.allowed = set(allowed)
        self.timeout = timeout
        self._pending = {}
        self._lock = threading.RLock()

    @staticmethod
    def key(root,kind,details):
        return hashlib.sha256(json.dumps([str(root),kind,details],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def pending(self):
        with self._lock:
            return [dict(v['view']) for v in self._pending.values() if not v['event'].is_set()]

    def request(self,root,kind,details,risk='normal',autonomous=False,stopped=lambda:False):
        if stopped():return False
        key = self.key(root,kind,details)
        if risk=='safe' or risk!='dangerous' and (autonomous or key in self.allowed):
            return True
        ticket = uuid.uuid4().hex
        view = {'id':ticket,'kind':kind,'details':details,'risk':risk,'project':str(root)}
        item = {'event':threading.Event(),'allowed':False,'view':view,'key':key}
        with self._lock:self._pending[ticket]=item
        self.notify()
        deadline=time.monotonic()+self.timeout
        try:
            while not item['event'].wait(.05):
                if stopped() or time.monotonic()>=deadline:return False
            return item['allowed'] and not stopped()
        finally:
            with self._lock:self._pending.pop(ticket,None)
            self.notify()

    def respond(self,ticket,allow=False,always=False):
        with self._lock:
            item=self._pending.get(ticket)
            if item is None or item['event'].is_set():return False
            item['allowed']=allow is True
            if item['allowed'] and always and item['view']['risk']!='dangerous' and item['view']['kind']=='command':
                self.allowed.add(item['key']);self.remember(sorted(self.allowed))
            item['event'].set()
        return True

    def cancel(self):
        with self._lock:
            for item in self._pending.values():item['allowed']=False;item['event'].set()
