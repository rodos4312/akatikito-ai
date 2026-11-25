"""Central memory store (MEMORY-PLAN M1): one local SQLite file, a lossless history, an index.

Every memory is a row in ``memories``. A revision writes a NEW row and points the old one at it
(``superseded_by``); forgetting hides a row and, only when asked for good, blanks its words while
keeping the trace that it happened. Only active rows sit in the search index. Writes pass the
memory guard (prompt injection) and secret redaction first. Nothing here calls the network.

The index holds normalized search keys built by ``memory_text.index_terms``. Without FTS5 (some
Android Python builds) the same keys live in a plain table and are scanned in Python.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1
KINDS = ("core", "fact", "belief", "episode")
STATUSES = ("confirmed", "unconfirmed")
MAX_TEXT = 4000
MAX_TOPIC = 120
MAX_KEYWORDS = 600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT    NOT NULL DEFAULT 'global',
    kind          TEXT    NOT NULL DEFAULT 'fact',
    status        TEXT    NOT NULL DEFAULT 'confirmed',
    topic         TEXT    NOT NULL DEFAULT '',
    text          TEXT    NOT NULL,
    lang          TEXT    NOT NULL DEFAULT '',
    keywords      TEXT    NOT NULL DEFAULT '',
    importance    INTEGER NOT NULL DEFAULT 1 CHECK (importance BETWEEN 0 AND 3),
    created_at    REAL    NOT NULL,
    updated_at    REAL    NOT NULL,
    last_used_at  REAL,
    use_count     INTEGER NOT NULL DEFAULT 0,
    helpful       INTEGER NOT NULL DEFAULT 0,
    unhelpful     INTEGER NOT NULL DEFAULT 0,
    source        TEXT    NOT NULL DEFAULT '',
    forgotten     INTEGER NOT NULL DEFAULT 0,
    superseded_by INTEGER REFERENCES memories(id)
);
CREATE INDEX IF NOT EXISTS memories_active ON memories(scope, kind) WHERE forgotten = 0 AND superseded_by IS NULL;
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    actor       TEXT    NOT NULL DEFAULT 'owner',
    at          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS history_memory ON history(memory_id);
CREATE TABLE IF NOT EXISTS proposals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_FTS = "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(keys, sk, tokenize='unicode61 remove_diacritics 0')"
_PLAIN = "CREATE TABLE IF NOT EXISTS memories_keys (id INTEGER PRIMARY KEY, keys TEXT NOT NULL, sk TEXT NOT NULL)"
_COLUMNS = ("id", "scope", "kind", "status", "topic", "text", "lang", "keywords", "importance", "created_at",
            "updated_at", "last_used_at", "use_count", "helpful", "unhelpful", "source", "forgotten", "superseded_by")
_ACTIVE = "forgotten = 0 AND superseded_by IS NULL"


class MemoryRefused(ValueError):
    """A refused memory write; the message is plain Greek for the owner."""


def _default_indexer(text: str, topic: str, keywords: str) -> tuple[str, str]:
    from .memory_text import index_terms
    return index_terms(text, topic, keywords)


def _clean(value: Any, limit: int, name: str) -> str:
    value = " ".join(str(value or "").split())
    if len(value) > limit:
        raise MemoryRefused(f"Το πεδίο «{name}» ξεπερνά τους {limit} χαρακτήρες.")
    return value


def _screen(*texts: str) -> None:
    from . import memory_guard
    for text in texts:
        reason = memory_guard.scan(text) if text else None
        if reason:
            raise MemoryRefused(memory_guard.refusal(reason))


def _redacted(text: str) -> str:
    from .redact import redact
    return redact(text)


class MemoryStore:
    """Thread-safe access to one ``memory.db``. Use as a context manager or call ``close()``."""

    def __init__(self, path: Path | str, *, indexer: Callable[[str, str, str], tuple[str, str]] | None = None,
                 force_plain: bool = False):
        from .local_store import checked
        self.path = checked(Path(path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._indexer = indexer or _default_indexer
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=5, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # Refuse a newer file before ANY pragma: switching to WAL already rewrites the header.
        if self._db.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
            self._db.close()
            raise MemoryRefused("Το αρχείο μνήμης είναι από νεότερη έκδοση του Akatikito· δεν αλλάχτηκε.")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA secure_delete=ON")  # forgotten words are overwritten, not left in free pages
        self.fts = self._create_schema(force_plain)

    # ── lifecycle ──────────────────────────────────────────────────────────────────────────────
    def _create_schema(self, force_plain: bool) -> bool:
        with self._lock:
            self._db.executescript(_SCHEMA)
            fts = not force_plain and self._try_fts()
            if not fts:
                self._db.execute(_PLAIN)
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('fts5', ?)", (str(int(fts)),))
            return fts

    def _try_fts(self) -> bool:
        try:
            self._db.execute(_FTS)
            return True
        except sqlite3.OperationalError:
            return False

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def integrity_ok(self) -> bool:
        with self._lock:
            return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def backup(self, destination: Path | str) -> Path:
        """Consistent copy of the whole database (taken before any schema migration)."""
        from .local_store import checked
        destination = checked(Path(destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(str(destination)) as target:
            self._db.backup(target)
        return destination

    def meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # ── writes ─────────────────────────────────────────────────────────────────────────────────
    def add(self, text: str, *, topic: str = "", scope: str = "global", kind: str = "fact",
            status: str = "confirmed", importance: int = 1, keywords: str = "", source: str = "owner",
            lang: str = "", actor: str = "owner", created_at: float | None = None) -> int:
        """Store one memory and return its id. An identical active memory in the same scope is reused."""
        row = self._prepare(text, topic, scope, kind, status, importance, keywords, source, lang)
        with self._lock:
            existing = self._find_same(row)
            if existing is not None:
                return existing
            with self._transaction():
                new_id = self._insert(row, created_at)
                self._log(new_id, "add", None, self._dump(new_id), actor)
            return new_id

    def update(self, memory_id: int, *, actor: str = "owner", **changes: Any) -> int:
        """Revise a memory: a new row replaces it, the old row stays in history. Returns the new id."""
        with self._lock:
            old = self.get(memory_id)
            if old is None or old["forgotten"] or old["superseded_by"] is not None:
                raise MemoryRefused("Η μνήμη δεν υπάρχει ή έχει ήδη αντικατασταθεί.")
            unknown = set(changes) - {"text", "topic", "scope", "kind", "status", "importance", "keywords", "lang"}
            if unknown:
                raise MemoryRefused(f"Άγνωστα πεδία: {', '.join(sorted(unknown))}")
            merged = {k: changes.get(k, old[k]) for k in
                      ("text", "topic", "scope", "kind", "status", "importance", "keywords", "lang")}
            row = self._prepare(merged["text"], merged["topic"], merged["scope"], merged["kind"], merged["status"],
                                merged["importance"], merged["keywords"], old["source"], merged["lang"])
            with self._transaction():
                new_id = self._insert(row, old["created_at"])
                self._db.execute("UPDATE memories SET superseded_by = ? WHERE id = ?", (new_id, memory_id))
                self._unindex(memory_id)
                self._log(memory_id, "revise", old, self._dump(new_id), actor)
            return new_id

    def forget(self, memory_id: int, *, hard: bool = False, actor: str = "owner") -> None:
        """Hide a memory. ``hard`` also erases its words everywhere, keeping only the fact it existed."""
        with self._lock:
            old = self.get(memory_id)
            if old is None:
                raise MemoryRefused("Δεν υπάρχει αυτή η μνήμη.")
            with self._transaction():
                self._db.execute("UPDATE memories SET forgotten = 1, updated_at = ? WHERE id = ?",
                                 (time.time(), memory_id))
                self._unindex(memory_id)
                if hard:
                    self._erase(memory_id)
                    self._log(memory_id, "forget_hard", None, None, actor)
                else:
                    self._log(memory_id, "forget", old, None, actor)
            if hard:
                if self.fts:  # deleted FTS terms stay in old segments until they are merged
                    self._db.execute("INSERT INTO memories_fts(memories_fts) VALUES ('optimize')")
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def touch(self, ids: Iterable[int], now: float | None = None) -> None:
        ids = [int(i) for i in ids]
        if not ids:
            return
        with self._lock:
            self._db.executemany("UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                                 [(now or time.time(), i) for i in ids])

    def feedback(self, memory_id: int, helpful: bool) -> None:
        column = "helpful" if helpful else "unhelpful"
        with self._lock:
            self._db.execute(f"UPDATE memories SET {column} = {column} + 1 WHERE id = ?", (int(memory_id),))

    def add_proposal(self, payload: dict) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO proposals(payload_json, created_at) VALUES (?, ?)",
                                   (json.dumps(payload, ensure_ascii=False), time.time()))
            return int(cur.lastrowid)

    def proposals(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, payload_json, created_at FROM proposals ORDER BY id").fetchall()
            return [{"id": r[0], "created_at": r[2], **json.loads(r[1])} for r in rows]

    # ── reads ──────────────────────────────────────────────────────────────────────────────────
    def get(self, memory_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
            return dict(row) if row else None

    def active(self, *, scope: str | None = None, kind: str | None = None, limit: int = 100,
               offset: int = 0) -> list[dict]:
        sql, args = f"SELECT * FROM memories WHERE {_ACTIVE}", []
        if scope is not None:
            sql, args = sql + " AND scope = ?", [*args, scope]
        if kind is not None:
            sql, args = sql + " AND kind = ?", [*args, kind]
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, [*args, max(1, int(limit)), max(0, int(offset))])]

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute(f"SELECT COUNT(*) FROM memories WHERE {_ACTIVE}").fetchone()[0])

    def history(self, memory_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM history WHERE memory_id = ? ORDER BY id", (int(memory_id),))
            return [dict(r) for r in rows]

    def candidates(self, match: str, scopes: Iterable[str], limit: int = 200) -> list[tuple[dict, float]]:
        """Active rows matching an FTS5 ``match`` expression, best first, with a relevance >= 0."""
        scopes = list(scopes)
        marks = ",".join("?" * len(scopes))
        sql = (f"SELECT m.*, -bm25(memories_fts, 1.0, 0.6) AS rel FROM memories_fts JOIN memories m "
               f"ON m.id = memories_fts.rowid WHERE memories_fts MATCH ? AND m.scope IN ({marks}) "
               f"ORDER BY rel DESC LIMIT ?")
        with self._lock:
            rows = self._db.execute(sql, [match, *scopes, int(limit)]).fetchall()
        return [(dict(r), max(0.0, float(r["rel"]))) for r in rows]

    def scan_keys(self, scopes: Iterable[str]) -> list[tuple[dict, str, str]]:
        """Fallback without FTS5: every active row with its search keys."""
        scopes = list(scopes)
        marks = ",".join("?" * len(scopes))
        table = "memories_fts" if self.fts else "memories_keys"
        key_id = "rowid" if self.fts else "id"
        sql = (f"SELECT m.*, k.keys AS k_keys, k.sk AS k_sk FROM {table} k JOIN memories m ON m.id = k.{key_id} "
               f"WHERE m.scope IN ({marks})")
        with self._lock:
            return [(dict(r), r["k_keys"], r["k_sk"]) for r in self._db.execute(sql, scopes)]

    def reindex(self) -> int:
        """Rebuild every search key (after a change to memory_text). Returns the rows indexed."""
        with self._lock, self._transaction():
            self._db.execute("DELETE FROM memories_fts" if self.fts else "DELETE FROM memories_keys")
            rows = self._db.execute(f"SELECT id, text, topic, keywords FROM memories WHERE {_ACTIVE}").fetchall()
            for r in rows:
                self._index(r["id"], r["text"], r["topic"], r["keywords"])
            return len(rows)

    # ── internals ──────────────────────────────────────────────────────────────────────────────
    def _prepare(self, text, topic, scope, kind, status, importance, keywords, source, lang) -> dict:
        text = _clean(text, MAX_TEXT, "κείμενο")
        if not text:
            raise MemoryRefused("Κενή σημείωση — δεν αποθηκεύτηκε.")
        topic, keywords = _clean(topic, MAX_TOPIC, "θέμα"), _clean(keywords, MAX_KEYWORDS, "λέξεις-κλειδιά")
        if kind not in KINDS or status not in STATUSES:
            raise MemoryRefused("Μη έγκυρο είδος ή κατάσταση μνήμης.")
        if kind == "fact" and status != "confirmed":
            raise MemoryRefused("Ένα ανεπιβεβαίωτο στοιχείο δηλώνεται ως belief/unconfirmed.")
        if not isinstance(importance, int) or not 0 <= importance <= 3:
            raise MemoryRefused("Η σημασία είναι ακέραιος από 0 έως 3.")
        scope = _clean(scope, 200, "εμβέλεια") or "global"
        _screen(text, topic, keywords)
        return {"text": _redacted(text), "topic": _redacted(topic), "keywords": _redacted(keywords),
                "scope": scope, "kind": kind, "status": status, "importance": importance,
                "source": _clean(source, 200, "πηγή"), "lang": _clean(lang, 20, "γλώσσα")}

    def _find_same(self, row: dict) -> int | None:
        from .memory import note_key
        found = self._db.execute(f"SELECT id, text FROM memories WHERE {_ACTIVE} AND scope = ? AND kind = ? "
                                 "AND status = ?", (row["scope"], row["kind"], row["status"])).fetchall()
        target = note_key(row["text"])
        return next((r["id"] for r in found if note_key(r["text"]) == target), None)

    def _insert(self, row: dict, created_at: float | None) -> int:
        now = time.time()
        cur = self._db.execute(
            "INSERT INTO memories(scope, kind, status, topic, text, lang, keywords, importance, created_at, "
            "updated_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["scope"], row["kind"], row["status"], row["topic"], row["text"], row["lang"], row["keywords"],
             row["importance"], created_at or now, now, row["source"]))
        new_id = int(cur.lastrowid)
        self._index(new_id, row["text"], row["topic"], row["keywords"])
        return new_id

    def _index(self, memory_id: int, text: str, topic: str, keywords: str) -> None:
        keys, sk = self._indexer(text, topic, keywords)
        if self.fts:
            self._db.execute("INSERT INTO memories_fts(rowid, keys, sk) VALUES (?, ?, ?)", (memory_id, keys, sk))
        else:
            self._db.execute("INSERT OR REPLACE INTO memories_keys(id, keys, sk) VALUES (?, ?, ?)",
                             (memory_id, keys, sk))

    def _unindex(self, memory_id: int) -> None:
        if self.fts:
            self._db.execute("DELETE FROM memories_fts WHERE rowid = ?", (memory_id,))
        else:
            self._db.execute("DELETE FROM memories_keys WHERE id = ?", (memory_id,))

    def _erase(self, memory_id: int) -> None:
        """Blank the words of this memory and of every earlier version of it, in rows and history."""
        chain, frontier = {memory_id}, [memory_id]
        while frontier:
            found = [r[0] for r in self._db.execute(
                f"SELECT id FROM memories WHERE superseded_by IN ({','.join('?' * len(frontier))})", frontier)]
            frontier = [i for i in found if i not in chain]
            chain.update(frontier)
        marks = ",".join("?" * len(chain))
        self._db.execute(f"UPDATE memories SET text = '[ξεχάστηκε οριστικά]', topic = '', keywords = '', "
                         f"forgotten = 1 WHERE id IN ({marks})", list(chain))
        self._db.execute(f"UPDATE history SET before_json = NULL, after_json = NULL WHERE memory_id IN ({marks})",
                         list(chain))

    def _dump(self, memory_id: int) -> dict:
        row = self._db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return {k: row[k] for k in _COLUMNS}

    def _log(self, memory_id: int, action: str, before: dict | None, after: dict | None, actor: str) -> None:
        dump = (lambda v: None if v is None else json.dumps(v, ensure_ascii=False))
        self._db.execute("INSERT INTO history(memory_id, action, before_json, after_json, actor, at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (memory_id, action, dump(before), dump(after), actor, time.time()))

    def _transaction(self):
        return _Transaction(self._db)


class _Transaction:
    """BEGIN IMMEDIATE … COMMIT, or ROLLBACK on any error: a half-written memory never survives."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, _exc, _tb):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
