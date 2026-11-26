"""Obsidian / Markdown notes vault: fast SQLite FTS5 search, strict confinement, previewed edits (L5)."""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

_TAG_RE = re.compile(r"(?:^|\s)#([\w/-]{1,64})", re.UNICODE)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)(?:[#|][^\[\]]+)?\]\]")
_TOKEN_RE = re.compile(r"[\w\u0370-\u03ff\u1f00-\u1fff]+", re.UNICODE)
_SKIP_DIRS = {".obsidian", ".trash", ".git", ".akatikito"}
MAX_NOTE_BYTES = 2 * 1024 * 1024


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text or "").casefold())
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")


def _extract_tags(text: str) -> list[str]:
    return sorted(set(_TAG_RE.findall(text or "")))


def _extract_wikilinks(text: str) -> list[str]:
    return sorted(set(m.strip() for m in _WIKILINK_RE.findall(text or "") if m.strip()))


def _extract_title(rel_path: str, text: str) -> str:
    for line in (text or "").splitlines()[:15]:
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(rel_path).stem


def _diff_hash(rel_path: str, old_text: str, new_text: str) -> str:
    payload = f"{rel_path}\x00{old_text}\x00{new_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


class NotesVault:
    """Confined Markdown / Obsidian notes vault with incremental SQLite FTS5 search."""

    def __init__(self, vault_dir: str | Path, *, index_path: str | Path | None = None) -> None:
        root = Path(vault_dir).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Ο φάκελος σημειώσεων δεν υπάρχει: {root}")
        self.root = root
        self.index_path = Path(index_path).resolve() if index_path else (root / ".akatikito" / "vault_fts.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.index_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notes_meta ("
            "rel_path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, title TEXT, tags TEXT, links TEXT)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
            "rel_path UNINDEXED, title, tags, body)"
        )
        return conn

    def _resolve_note(self, relative_name: str) -> tuple[Path, str]:
        raw = str(relative_name or "").strip().replace("\\", "/")
        if not raw or raw.startswith("/") or ":" in raw or ".." in raw.split("/"):
            raise ValueError("Μη επιτρεπτή διαδρομή σημείωσης (μόνο σχετική διαδρομή μέσα στον φάκελο).")
        if not raw.lower().endswith(".md"):
            raw = raw + ".md"
        candidate = (self.root / raw).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Η σημείωση πρέπει να βρίσκεται αποκλειστικά μέσα στον επιλεγμένο φάκελο.")
        for parent in [candidate, *candidate.parents]:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError("Δεν επιτρέπονται symlinks εκτός του φακέλου σημειώσεων.")
        rel = candidate.relative_to(self.root).as_posix()
        return candidate, rel

    def _iter_markdown_files(self) -> list[tuple[str, Path, os.stat_result]]:
        found: list[tuple[str, Path, os.stat_result]] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            base = Path(dirpath)
            for fname in filenames:
                if not fname.lower().endswith(".md"):
                    continue
                full = base / fname
                if full.is_symlink():
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                if st.st_size > MAX_NOTE_BYTES:
                    continue
                rel = full.relative_to(self.root).as_posix()
                found.append((rel, full, st))
        return found

    def sync_index(self) -> dict[str, int]:
        """Incrementally sync all .md files inside the vault into SQLite FTS5."""
        files = self._iter_markdown_files()
        seen = {rel for rel, _, _ in files}
        indexed = 0
        removed = 0
        with self._connect() as conn:
            existing = {
                row[0]: (row[1], row[2])
                for row in conn.execute("SELECT rel_path, mtime_ns, size FROM notes_meta")
            }
            for stale in set(existing.keys()) - seen:
                conn.execute("DELETE FROM notes_meta WHERE rel_path = ?", (stale,))
                conn.execute("DELETE FROM notes_fts WHERE rel_path = ?", (stale,))
                removed += 1
            for rel, full, st in files:
                prev = existing.get(rel)
                if prev == (st.st_mtime_ns, st.st_size):
                    continue
                try:
                    text = full.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                title = _extract_title(rel, text)
                tags = " ".join(_extract_tags(text))
                links = "\n".join(_extract_wikilinks(text))
                conn.execute(
                    "INSERT OR REPLACE INTO notes_meta(rel_path, mtime_ns, size, title, tags, links) VALUES (?,?,?,?,?,?)",
                    (rel, st.st_mtime_ns, st.st_size, title, tags, links),
                )
                conn.execute("DELETE FROM notes_fts WHERE rel_path = ?", (rel,))
                conn.execute(
                    "INSERT INTO notes_fts(rel_path, title, tags, body) VALUES (?,?,?,?)",
                    (rel, _fold(title), _fold(tags), _fold(text)),
                )
                indexed += 1
        return {"total": len(files), "indexed": indexed, "removed": removed}

    def search(self, query: str, *, limit: int = 10, sync: bool = True) -> list[dict]:
        """Search Markdown notes via SQLite FTS5 BM25 (accent-insensitive Greek & English)."""
        if sync:
            self.sync_index()
        tokens = _TOKEN_RE.findall(_fold(query))
        if not tokens:
            return []
        fts_query = " AND ".join(f'"{t}"*' for t in tokens[:10])
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT f.rel_path, m.title, m.tags, m.links, "
                "snippet(notes_fts, 3, '[', ']', '...', 18), bm25(notes_fts) "
                "FROM notes_fts f JOIN notes_meta m ON m.rel_path = f.rel_path "
                "WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
                (fts_query, max(1, min(int(limit), 50))),
            ).fetchall()
        return [
            {
                "path": row[0],
                "title": row[1],
                "tags": row[2].split() if row[2] else [],
                "links": row[3].splitlines() if row[3] else [],
                "snippet": row[4],
                "score": round(float(row[5]), 4),
            }
            for row in rows
        ]

    def create_note(self, relative_name: str, content: str, *, tags: tuple[str, ...] = ()) -> dict:
        """Create a NEW Markdown note strictly inside the vault (refuses to overwrite existing)."""
        path, rel = self._resolve_note(relative_name)
        if path.exists():
            raise FileExistsError(f"Η σημείωση υπάρχει ήδη ({rel}). Χρησιμοποίησε προεπισκόπηση αλλαγής.")
        body = str(content or "")
        if tags:
            tag_line = " ".join(f"#{t.lstrip('#')}" for t in tags if str(t).strip())
            body = f"{body.rstrip()}\n\n{tag_line}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("." + path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        return {"ok": True, "path": rel, "bytes": len(body.encode("utf-8"))}

    def preview_edit(self, relative_name: str, new_content: str) -> dict:
        """Generate a unified diff preview and approval hash before modifying an existing note."""
        path, rel = self._resolve_note(relative_name)
        if not path.is_file():
            raise FileNotFoundError(f"Δεν βρέθηκε η σημείωση: {rel}")
        old_text = path.read_text(encoding="utf-8")
        updated = str(new_content or "")
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
        return {
            "path": rel,
            "changed": old_text != updated,
            "diff": diff,
            "preview_hash": _diff_hash(rel, old_text, updated),
        }

    def apply_edit(self, relative_name: str, new_content: str, *, approved_hash: str) -> dict:
        """Atomically overwrite an existing note ONLY if `approved_hash` matches its preview."""
        path, rel = self._resolve_note(relative_name)
        if not path.is_file():
            raise FileNotFoundError(f"Δεν βρέθηκε η σημείωση: {rel}")
        old_text = path.read_text(encoding="utf-8")
        updated = str(new_content or "")
        expected = _diff_hash(rel, old_text, updated)
        if not approved_hash or approved_hash != expected:
            raise PermissionError("Η αλλαγή της σημείωσης χρειάζεται προεπισκόπηση και έγκριση πριν εφαρμοστεί.")
        tmp = path.with_name("." + path.name + ".tmp")
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, path)
        return {"ok": True, "path": rel, "bytes": len(updated.encode("utf-8"))}
