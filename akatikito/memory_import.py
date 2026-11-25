"""Lossless move of today's memory into the central store, and a readable export (MEMORY-PLAN M4).

Sources: the global ``MEMORY.md``, the legacy ``USER.md``, the structured ``profile.json`` (facts,
beliefs, pending proposals, revision history) and each project's ``.agent/MEMORY.md``.
The source files are only READ, never changed. Each file is stamped with its sha256, so running
again does nothing until a file changes, and then only its new notes are added (duplicates are
recognised by the store). Everything from one file lands in one transaction or not at all.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .memory_store import MemoryStore

KIND_TITLES = {"core": "Προφίλ", "fact": "Γεγονότα", "belief": "Υποθέσεις (ανεπιβεβαίωτες)", "episode": "Επεισόδια"}


def project_scope(root: Path | str) -> str:
    """Stable scope of a project folder, independent of how its path is spelled on Windows."""
    resolved = str(Path(root).resolve()).replace("\\", "/").rstrip("/").casefold()
    return "project:" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def _notes(text: str) -> list[str]:
    """Bullet or plain lines of a Markdown memory file; headings and blank lines are not notes."""
    notes = []
    for line in text.splitlines():
        note = line.strip().lstrip("-*•").strip()
        if note and not note.startswith("#"):
            notes.append(note)
    return notes


def _read(path: Path) -> tuple[bytes, str] | None:
    from .local_store import checked
    path = checked(path)
    if not path.is_file():
        return None
    raw = path.read_bytes()
    return raw, raw.decode("utf-8-sig")


def _already(store: MemoryStore, path: Path, raw: bytes) -> bool:
    return store.meta(f"import:{path.name}:{_path_key(path)}") == hashlib.sha256(raw).hexdigest()


def _stamp(store: MemoryStore, path: Path, raw: bytes) -> None:
    store.set_meta(f"import:{path.name}:{_path_key(path)}", hashlib.sha256(raw).hexdigest())


def _path_key(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()[:12]


def import_markdown(store: MemoryStore, path: Path, *, scope: str = "global", kind: str = "fact") -> int:
    """Import one ``MEMORY.md`` / ``USER.md``. Returns the number of NEW memories."""
    source = _read(Path(path))
    if source is None or _already(store, Path(path), source[0]):
        return 0
    before = store.count()
    store.add_many([{"text": note, "scope": scope, "kind": kind, "source": f"{Path(path).name} (μεταφορά)"}
                    for note in _notes(source[1])], actor="migration")
    _stamp(store, Path(path), source[0])
    return store.count() - before


def _profile_rows(data: dict) -> list[dict]:
    rows = []
    for entry in data.get("entries", []):
        if not isinstance(entry, dict) or not str(entry.get("text", "")).strip():
            continue
        confirmed_fact = entry.get("kind") == "fact" and entry.get("status") == "confirmed"
        rows.append({
            "text": entry["text"], "topic": str(entry.get("topic", ""))[:120],
            "kind": "core" if confirmed_fact else "belief",
            "status": "confirmed" if confirmed_fact or entry.get("status") == "confirmed" else "unconfirmed",
            "source": f"profile.json ({entry.get('source') or 'προφίλ'})",
            "created_at": entry.get("created") if isinstance(entry.get("created"), (int, float)) else None,
        })
    return rows


def import_profile(store: MemoryStore, path: Path) -> int:
    """Import ``profile.json``: entries become memories, proposals wait again, revisions stay as history."""
    from .local_store import load_json
    path = Path(path)
    source = _read(path)
    if source is None or _already(store, path, source[0]):
        return 0
    data = load_json(path, dict, limit=16_000_000)
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise ValueError("Μη έγκυρο profile.json· δεν μεταφέρθηκε τίποτα και το αρχείο έμεινε ίδιο.")
    before = store.count()
    store.add_many(_profile_rows(data), actor="migration")
    for proposal in data.get("proposals", []):
        if isinstance(proposal, dict) and not _known_proposal(store, proposal):
            store.add_proposal({**proposal, "imported_from": "profile.json"})
    if data.get("revisions"):
        store.archive_legacy("profile.json", data["revisions"])
    _stamp(store, path, source[0])
    return store.count() - before


def _known_proposal(store: MemoryStore, proposal: dict) -> bool:
    return any(p.get("id") == proposal.get("id") and p.get("imported_from") for p in store.proposals())


def import_all(store: MemoryStore, memory_dir: Path, projects: list[Path] = ()) -> dict:
    """Everything Akatikito remembers today. Returns how many new memories came from each source."""
    from .workspace import AGENT_DIR, MEMORY_FILE
    memory_dir = Path(memory_dir)
    report = {
        "MEMORY.md": import_markdown(store, memory_dir / "MEMORY.md"),
        "profile.json": import_profile(store, memory_dir / "profile.json"),
        "USER.md": import_markdown(store, memory_dir / "USER.md", kind="core"),
    }
    for root in projects:
        report[f"project:{Path(root).name}"] = import_markdown(store, Path(root) / AGENT_DIR / MEMORY_FILE,
                                                               scope=project_scope(root))
    return report


def export_markdown(store: MemoryStore) -> str:
    """Every active memory as readable Markdown, grouped by scope and kind (for reading or backup)."""
    rows = []
    offset = 0
    while batch := store.active(limit=500, offset=offset):
        rows.extend(batch)
        offset += len(batch)
    lines = ["# Μνήμη Akatikito", ""]
    for scope in sorted({r["scope"] for r in rows}, key=lambda s: (s != "global", s)):
        lines += [f"## {'Γενική' if scope == 'global' else scope}", ""]
        for kind in ("core", "fact", "belief", "episode"):
            chosen = sorted((r for r in rows if r["scope"] == scope and r["kind"] == kind), key=lambda r: r["id"])
            if chosen:
                lines += [f"### {KIND_TITLES[kind]}", ""]
                lines += [f"- {r['topic'] + ': ' if r['topic'] else ''}{r['text']}" for r in chosen] + [""]
    return "\n".join(lines).rstrip() + "\n"


def legacy_revisions(store: MemoryStore, source: str) -> list:
    raw = store.meta(f"legacy:{source}")
    return json.loads(raw) if raw else []
