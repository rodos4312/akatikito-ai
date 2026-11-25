"""Search index over a project's past conversations (MEMORY-PLAN M8).

``search_past_sessions`` used to open every session file and compare text one entry at a time.
This keeps, next to the sessions (``.agent/sessions/index.db``), the same language-aware keys the
memory uses (accents, word endings, Greeklish, CJK, identifiers). It is incremental: only sessions
whose file changed are read again, and deleted sessions leave the index. It is a cache: deleting
it loses nothing, and any failure falls back to the old scan.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .memory_recall import match_expression
from .memory_text import index_terms, query_terms

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (session_id TEXT PRIMARY KEY, stamp TEXT NOT NULL, source TEXT NOT NULL,
                                  title TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, idx INTEGER NOT NULL,
                                    role TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS entries_session ON entries(session_id);
"""


class SessionIndex:
    def __init__(self, store):
        from .local_store import checked
        self.store = store
        self.path = checked(Path(store.dir) / "index.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=5)
        self.db.executescript(SCHEMA)
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(keys, sk, "
                        "tokenize='unicode61 remove_diacritics 0')")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def refresh(self) -> int:
        """Re-read only the sessions whose file changed; drop the ones that are gone. Returns sessions read."""
        known = dict(self.db.execute("SELECT session_id, stamp FROM files"))
        present, changed = set(), 0
        for path in sorted(Path(self.store.dir).glob("*.json")) if Path(self.store.dir).is_dir() else []:
            session_id = path.stem
            if not session_id.isalnum():
                continue
            info = path.stat()
            stamp = f"{info.st_mtime_ns}:{info.st_size}"
            present.add(session_id)
            if known.get(session_id) != stamp:
                self._index(session_id, stamp)
                changed += 1
        for gone in set(known) - present:
            self._drop(gone)
        self.db.commit()
        return changed

    def _drop(self, session_id: str) -> None:
        ids = [r[0] for r in self.db.execute("SELECT id FROM entries WHERE session_id = ?", (session_id,))]
        self.db.executemany("DELETE FROM entries_fts WHERE rowid = ?", [(i,) for i in ids])
        self.db.execute("DELETE FROM entries WHERE session_id = ?", (session_id,))
        self.db.execute("DELETE FROM files WHERE session_id = ?", (session_id,))

    def _index(self, session_id: str, stamp: str) -> None:
        self._drop(session_id)
        rec = self.store.load(session_id) or {}
        source = json.dumps(rec.get("source"), sort_keys=True)
        self.db.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?)",
                        (session_id, stamp, source, str(rec.get("title", "")), float(rec.get("updated_at") or 0)))
        for idx, entry in enumerate(rec.get("transcript") or []):
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            cur = self.db.execute("INSERT INTO entries(session_id, idx, role, text) VALUES (?, ?, ?, ?)",
                                  (session_id, idx, str(entry.get("role", "user")), text[:20_000]))
            keys, sk = index_terms(text[:20_000])
            self.db.execute("INSERT INTO entries_fts(rowid, keys, sk) VALUES (?, ?, ?)", (cur.lastrowid, keys, sk))

    def search(self, query: str, *, limit: int = 5, source=None) -> list[dict]:
        """Sessions best matching ``query`` (most relevant first), each with up to 4 excerpts."""
        terms = query_terms(query)
        if terms.empty():
            return []
        rows = self.db.execute(
            "SELECT e.session_id, e.idx, e.role, e.text, f.title, f.updated_at, f.source, -bm25(entries_fts, 1.0, 0.6) "
            "FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid JOIN files f ON f.session_id = e.session_id "
            "WHERE entries_fts MATCH ? ORDER BY 8 DESC LIMIT 400", (match_expression(terms),)).fetchall()
        wanted = json.dumps(source, sort_keys=True) if source is not None else None
        sessions: dict[str, dict] = {}
        for session_id, idx, role, text, title, updated, src, _rel in rows:
            if wanted is not None and src != wanted:
                continue
            hit = sessions.setdefault(session_id, {"session_id": session_id, "title": title, "updated_at": updated,
                                                   "matches": [], "_idx": set()})
            if len(hit["matches"]) < 4 and idx not in hit["_idx"]:
                hit["matches"].append({"role": role, "excerpt": text[:450]})
                hit["_idx"].add(idx)
                if role == "user":
                    self._append_reply(hit, session_id, idx)
        result = list(sessions.values())[:max(1, min(15, int(limit)))]
        for hit in result:
            hit.pop("_idx")
        return result

    def _append_reply(self, hit: dict, session_id: str, idx: int) -> None:
        reply = self.db.execute("SELECT idx, role, text FROM entries WHERE session_id = ? AND idx = ?",
                                (session_id, idx + 1)).fetchone()
        if reply and len(hit["matches"]) < 4 and reply[0] not in hit["_idx"]:
            hit["matches"].append({"role": reply[1], "excerpt": reply[2][:450]})
            hit["_idx"].add(reply[0])
