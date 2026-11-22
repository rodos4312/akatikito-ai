"""Durable routing and admission receipts. Interrupted model turns are never replayed."""
from __future__ import annotations
import json,sqlite3,time,uuid
from pathlib import Path
from .channel_sdk import Inbound

class GatewayStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS routes(route TEXT PRIMARY KEY, session TEXT NOT NULL, origin TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inputs(id INTEGER PRIMARY KEY, platform TEXT NOT NULL, bot TEXT NOT NULL,
              update_id TEXT NOT NULL, route TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
              reply TEXT NOT NULL DEFAULT '', delivery TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
              UNIQUE(platform,bot,update_id));
            CREATE TABLE IF NOT EXISTS cursors(connector TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS input_state ON inputs(state,id);
            CREATE INDEX IF NOT EXISTS input_delivery ON inputs(delivery,id);
            CREATE INDEX IF NOT EXISTS input_route ON inputs(route,state);
            """)
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        return db
    def recover(self):
        with self.db() as db:
            db.execute("UPDATE inputs SET state='unknown' WHERE state='running'")
            db.execute("UPDATE inputs SET delivery='unknown' WHERE delivery='sending'")
    def cursor(self,connector):
        with self.db() as db:row=db.execute('SELECT value FROM cursors WHERE connector=?',(connector,)).fetchone()
        return row['value'] if row else ''
    def admit(self,connector,cursor,events):
        admitted=[]
        with self.db() as db:
            for event in events:
                result=db.execute('INSERT OR IGNORE INTO inputs(platform,bot,update_id,route,payload,created) VALUES(?,?,?,?,?,?)',
                           (event.platform,event.bot,event.update,event.key(),json.dumps(event.payload(),ensure_ascii=False),time.time()))
                if result.rowcount==1:admitted.append(event)
            db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',(connector,str(cursor)))
        return admitted
    def next(self,eligible=lambda event:True):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            blocked={r[0] for r in db.execute("SELECT DISTINCT route FROM inputs WHERE state='unknown'")}
            running={r[0] for r in db.execute("SELECT DISTINCT route FROM inputs WHERE state='running'")}
            for row in db.execute("SELECT * FROM inputs WHERE state='queued' ORDER BY id"):
                event=Inbound(**json.loads(row['payload']))
                if row['route'] in running or not eligible(event):continue
                if row['route'] in blocked and json.loads(row['payload'])['text'].strip() not in ('/new','/reset','/status','/help'):continue
                db.execute("UPDATE inputs SET state='running' WHERE id=?",(row['id'],));return dict(row)
        return None
    def route(self,event,reset=False,before=None):
        with self.db() as db:
            row=db.execute('SELECT * FROM routes WHERE route=?',(event.key(),)).fetchone()
            if row and not reset:return row['session']
            session=uuid.uuid4().hex[:12]
            db.execute('INSERT OR REPLACE INTO routes VALUES(?,?,?)',(event.key(),session,json.dumps(event.payload(),ensure_ascii=False)))
            if reset:
                db.execute("UPDATE inputs SET state='interrupted' WHERE route=? AND (state='unknown' OR (state='queued' AND id<?))",(event.key(),before or 0))
            return session
    def finish(self,ident,reply,state='done'):
        with self.db() as db:db.execute('UPDATE inputs SET state=?,reply=? WHERE id=?',(state,str(reply),ident))
    def claim_delivery(self,ident):
        with self.db() as db:return db.execute("UPDATE inputs SET delivery='sending' WHERE id=? AND delivery='pending'",(ident,)).rowcount==1
    def delivery(self,ident,value):
        with self.db() as db:db.execute('UPDATE inputs SET delivery=? WHERE id=?',(value,ident))
    def outbox(self):
        with self.db() as db:return [dict(x) for x in db.execute("SELECT * FROM inputs WHERE state IN ('done','failed') AND delivery='pending' ORDER BY id")]
    def pending(self):
        with self.db() as db:return [dict(r) for r in db.execute("SELECT payload FROM inputs WHERE state='queued' OR (state IN ('done','failed') AND delivery='pending')")]
    def summary(self):
        with self.db() as db:
            counts={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM inputs GROUP BY state')}
            issues=[{'id':r['id'],'state':r['state'],'delivery':r['delivery'],'platform':r['platform']} for r in db.execute("SELECT * FROM inputs WHERE state='unknown' OR delivery IN ('failed','unknown') ORDER BY id DESC LIMIT 20")]
            routes=[{'key':r['route'],'session':r['session'],'origin':json.loads(r['origin'])} for r in db.execute('SELECT * FROM routes')]
        for row in routes:row['origin'].pop('text',None);row['origin'].pop('update',None)
        return {'counts':counts,'issues':issues,'routes':routes}
