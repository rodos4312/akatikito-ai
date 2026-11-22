"""Offline SQLite FTS5 + BM25 document library search (v2.3)."""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .workspace import Workspace

INDEXABLE_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".md", ".markdown", ".txt", ".rst"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".agent", ".agent_backups", ".pytest_cache", "dist", "build",
}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB per document
MAX_FILES = 2000
MAX_TEXT_CHARS = 120_000
_TOKEN_RE = re.compile(r"[\w\u0370-\u03ff\u1f00-\u1fff]+", re.UNICODE)


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        total = 0
        for page in reader.pages[:200]:
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(text)
                total += len(text)
                if total >= MAX_TEXT_CHARS:
                    break
        return "\n".join(parts)[:MAX_TEXT_CHARS]
    except Exception:
        return ""


def _extract_docx(path: Path) -> str:
    try:
        import docx
        document = docx.Document(str(path))
        parts = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)[:MAX_TEXT_CHARS]
    except Exception:
        # Stdlib XML fallback for .docx
        try:
            with zipfile.ZipFile(path, "r") as zf:
                xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            texts = [el.text for el in root.iter() if el.tag.endswith("}t") and el.text]
            return " ".join(texts)[:MAX_TEXT_CHARS]
        except Exception:
            return ""


def _extract_xlsx(path: Path) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        parts: list[str] = []
        total = 0
        try:
            for name in wb.sheetnames[:20]:
                ws = wb[name]
                parts.append(f"[{name}]")
                for row in ws.iter_rows(max_row=500, values_only=True):
                    vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    if vals:
                        line = " | ".join(vals)
                        parts.append(line)
                        total += len(line)
                        if total >= MAX_TEXT_CHARS:
                            break
                if total >= MAX_TEXT_CHARS:
                    break
        finally:
            wb.close()
        return "\n".join(parts)[:MAX_TEXT_CHARS]
    except Exception:
        return ""


def _extract_csv(path: Path) -> str:
    try:
        raw = path.read_bytes()[: min(MAX_FILE_BYTES, 2 * 1024 * 1024)]
        text = raw.decode("utf-8-sig", errors="replace")
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        parts: list[str] = []
        total = 0
        for i, row in enumerate(reader):
            if i >= 1000:
                break
            vals = [c.strip() for c in row if c and c.strip()]
            if vals:
                line = " | ".join(vals)
                parts.append(line)
                total += len(line)
                if total >= MAX_TEXT_CHARS:
                    break
        return "\n".join(parts)[:MAX_TEXT_CHARS]
    except Exception:
        return ""


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".xlsx":
        return _extract_xlsx(path)
    if suffix in {".csv", ".tsv"}:
        return _extract_csv(path)
    try:
        raw = path.read_bytes()[: min(MAX_FILE_BYTES, 1024 * 1024)]
        return raw.decode("utf-8-sig", errors="replace")[:MAX_TEXT_CHARS]
    except Exception:
        return ""


def _fold_diacritics(text: str) -> str:
    """Strip combining diacritics (including Greek tonos/dialytika) and fold case."""
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.casefold()


def _fts_query(raw_query: str) -> str:
    tokens = _TOKEN_RE.findall(_fold_diacritics(raw_query or ""))
    if not tokens:
        return ""
    return " AND ".join(f'"{tok}"*' for tok in tokens[:12])


class DocIndex:
    """Incremental SQLite FTS5 + BM25 search index for workspace documents."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.db_path = self.root / ".agent" / "doc-index.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS doc_meta ("
            "path TEXT PRIMARY KEY, "
            "mtime_ns INTEGER NOT NULL, "
            "size INTEGER NOT NULL, "
            "kind TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5("
            "path, title, content, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        return conn

    def _iter_docs(self) -> list[tuple[str, Path, int, int]]:
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        found: list[tuple[str, Path, int, int]] = []
        ws = Workspace(self.root)
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            # Prune skipped or reparse-point directories
            kept: list[str] = []
            for d in dirnames:
                if d in SKIP_DIRS:
                    continue
                full_d = Path(dirpath) / d
                try:
                    st = full_d.stat(follow_symlinks=False)
                    if stat.S_ISLNK(st.st_mode) or (reparse and getattr(st, "st_file_attributes", 0) & reparse):
                        continue
                except OSError:
                    continue
                kept.append(d)
            dirnames[:] = kept

            for fname in filenames:
                if len(found) >= MAX_FILES:
                    return found
                full = Path(dirpath) / fname
                if full.suffix.lower() not in INDEXABLE_SUFFIXES:
                    continue
                try:
                    st = full.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(st.st_mode)
                        or stat.S_ISLNK(st.st_mode)
                        or (reparse and getattr(st, "st_file_attributes", 0) & reparse)
                        or st.st_size > MAX_FILE_BYTES
                    ):
                        continue
                    rel = ws.relative(full).replace("\\", "/")
                    found.append((rel, full, st.st_mtime_ns, st.st_size))
                except (OSError, ValueError):
                    continue
        return found

    def index(self) -> dict:
        """Incrementally update the FTS5 index for all supported documents."""
        docs = self._iter_docs()
        current_map = {rel: (full, mtime_ns, size) for rel, full, mtime_ns, size in docs}
        indexed = 0
        removed = 0
        conn = self._connect()
        try:
            existing = {
                row[0]: (row[1], row[2])
                for row in conn.execute("SELECT path, mtime_ns, size FROM doc_meta")
            }
            # Remove deleted files
            for old_path in set(existing) - set(current_map):
                conn.execute("DELETE FROM doc_meta WHERE path = ?", (old_path,))
                conn.execute("DELETE FROM doc_fts WHERE path = ?", (old_path,))
                removed += 1
            # Upsert new or modified files
            for rel, (full, mtime_ns, size) in current_map.items():
                prev = existing.get(rel)
                if prev and prev == (mtime_ns, size):
                    continue
                text = _extract_text(full)
                conn.execute("DELETE FROM doc_fts WHERE path = ?", (rel,))
                conn.execute(
                    "INSERT INTO doc_fts (path, title, content) VALUES (?, ?, ?)",
                    (rel, _fold_diacritics(full.name), _fold_diacritics(text)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO doc_meta (path, mtime_ns, size, kind) VALUES (?, ?, ?, ?)",
                    (rel, mtime_ns, size, full.suffix.lower().lstrip(".")),
                )
                indexed += 1
            conn.commit()
            total = conn.execute("SELECT COUNT(*) FROM doc_meta").fetchone()[0]
        finally:
            conn.close()
        return {"indexed": indexed, "removed": removed, "total": total}

    def search(self, query: str, limit: int = 8, reindex: bool = True) -> dict:
        """Search indexed documents using SQLite FTS5 + BM25 ranking."""
        limit = max(1, min(30, int(limit or 8)))
        stats = self.index() if reindex else {"indexed": 0, "removed": 0, "total": 0}
        fts_q = _fts_query(query)
        if not fts_q:
            return {"query": query, "results": [], "stats": stats}
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT f.path, f.title, m.kind, m.size, "
                "bm25(doc_fts, 2.0, 4.0, 1.0) AS score, "
                "snippet(doc_fts, 2, '[', ']', ' ... ', 24) AS excerpt "
                "FROM doc_fts f "
                "LEFT JOIN doc_meta m ON m.path = f.path "
                "WHERE doc_fts MATCH ? "
                "ORDER BY score ASC "
                "LIMIT ?",
                (fts_q, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        results = [
            {
                "path": r[0],
                "title": r[1],
                "kind": r[2] or "",
                "size": r[3] or 0,
                "score": round(float(r[4]), 4) if r[4] is not None else 0.0,
                "snippet": (r[5] or "").replace("\n", " ").strip(),
            }
            for r in rows
        ]
        return {"query": query, "results": results, "stats": stats}


def search_documents(ws: Workspace, query: str, limit: int = 8) -> str:
    """Agent read-only tool: full-text BM25 search across local PDF/DOCX/XLSX/CSV/MD/TXT files."""
    if not isinstance(query, str) or not query.strip():
        return "Δώσε όρους αναζήτησης για το search_documents."
    data = DocIndex(ws.root).search(query.strip(), limit=limit, reindex=True)
    results = data.get("results") or []
    total = (data.get("stats") or {}).get("total", 0)
    if not results:
        return f"Δεν βρέθηκαν αποτελέσματα για «{query.strip()}» (ευρετηριασμένα έγγραφα: {total})."
    lines = [f"Αποτελέσματα αναζήτησης εγγράφων για «{query.strip()}» ({len(results)} από {total} έγγραφα):"]
    for i, item in enumerate(results, 1):
        lines.append(f"{i}. {item['path']} [{item['kind']}]")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
    return "\n".join(lines)
