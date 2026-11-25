"""Today's memory files kept in step with the central store, and a readable export (MEMORY-PLAN M4/M5).

Sources: the global ``MEMORY.md``, the legacy ``USER.md``, the structured ``profile.json`` (facts,
beliefs, pending proposals, revision history) and each project's ``.agent/MEMORY.md``.
Until the memory screen (M6) writes to the store directly, these files stay the place where notes
are written and approved; the store is the searchable copy. ``sync_all`` mirrors them: new notes are
added, notes removed or revised in the files are forgotten in the store (with history), rows the
store got from elsewhere are left alone. Source files are only READ. A sha256 stamp per scope makes
an unchanged run free. A file that cannot be read safely is reported and blocks forgetting for its
scope in that run, so a damaged file never wipes memories.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .memory_store import MemoryStore

KIND_TITLES = {"core": "Προφίλ", "fact": "Γεγονότα", "belief": "Υποθέσεις (ανεπιβεβαίωτες)", "episode": "Επεισόδια"}
TAGS = {"MEMORY.md": "MEMORY.md", "USER.md": "USER.md", "profile.json": "profile.json"}


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


def _read(path: Path) -> bytes | None:
    from .local_store import checked
    path = checked(Path(path))
    return path.read_bytes() if path.is_file() else None


def _markdown_items(path: Path, *, scope: str, kind: str) -> list[dict]:
    raw = _read(path)
    if raw is None:
        return []
    return [{"text": note, "scope": scope, "kind": kind, "source": TAGS.get(Path(path).name, Path(path).name)}
            for note in _notes(raw.decode("utf-8-sig"))]


def _load_profile(path: Path) -> dict | None:
    from .local_store import load_json
    if _read(path) is None:
        return None
    data = load_json(path, dict, limit=16_000_000)
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise ValueError("Μη έγκυρο profile.json· δεν μεταφέρθηκε τίποτα και το αρχείο έμεινε ίδιο.")
    return data


def _profile_items(data: dict | None) -> list[dict]:
    rows = []
    for entry in (data or {}).get("entries", []):
        if not isinstance(entry, dict) or not str(entry.get("text", "")).strip():
            continue
        confirmed_fact = entry.get("kind") == "fact" and entry.get("status") == "confirmed"
        rows.append({
            "text": entry["text"], "topic": str(entry.get("topic", ""))[:120],
            "kind": "core" if confirmed_fact else "belief",
            "status": "confirmed" if confirmed_fact or entry.get("status") == "confirmed" else "unconfirmed",
            "source": TAGS["profile.json"],
            "created_at": entry.get("created") if isinstance(entry.get("created"), (int, float)) else None,
        })
    return rows


def _key(row: dict) -> tuple:
    from .memory import note_key
    topic = note_key(row.get("topic", ""))
    return row.get("kind", "fact"), row.get("status", "confirmed"), topic, note_key(row["text"])


def _scope_rows(store: MemoryStore, scope: str) -> list[dict]:
    rows, offset = [], 0
    while batch := store.active(scope=scope, limit=500, offset=offset):
        rows.extend(batch)
        offset += len(batch)
    return rows


def _mirror(store: MemoryStore, scope: str, sources: dict[str, list[dict] | Exception]) -> dict:
    """Add each source's notes, then forget file-owned rows no source holds any more."""
    report: dict[str, int | str] = {}
    for name, items in sources.items():
        if isinstance(items, Exception):
            report[name] = f"ΣΦΑΛΜΑ: {items}"
            continue
        safe = _screened(items)
        if len(safe) < len(items):
            report[f"{name}: απορρίφθηκαν"] = len(items) - len(safe)
        before = store.count()
        try:
            store.add_many(safe, actor="sync")
        except ValueError as exc:  # e.g. an over-long note: it blocks its file, never the other sources
            report[name] = f"ΣΦΑΛΜΑ: {exc}"
            continue
        report[name] = store.count() - before
    forgotten = 0
    if all(isinstance(v, int) for v in report.values()):
        wanted = {_key(item) for items in sources.values() for item in items}
        tags = {item["source"] for items in sources.values() for item in items} | {TAGS.get(n, n) for n in sources}
        for row in _scope_rows(store, scope):
            if row["source"] in tags and _key(row) not in wanted:
                store.forget(row["id"], actor="sync")
                forgotten += 1
    report["forgotten"] = forgotten
    return report


def _screened(items: list[dict]) -> list[dict]:
    """Drop notes the memory guard refuses (prompt injection): one poisoned line must not hide the rest."""
    from . import memory_guard
    return [i for i in items if not any(memory_guard.scan(i.get(f) or "") for f in ("text", "topic", "keywords"))]


def _digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        raw = _read(path)
        h.update(str(path).casefold().encode("utf-8") + b"\0" + (hashlib.sha256(raw).digest() if raw else b"-"))
    return h.hexdigest()


def _gather(loaders: dict[str, callable]) -> dict[str, list[dict] | Exception]:
    sources = {}
    for name, load in loaders.items():
        try:
            sources[name] = load()
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            sources[name] = exc
    return sources


def sync_global(store: MemoryStore, memory_dir: Path, *, force: bool = False) -> dict | None:
    """Mirror the global files. ``None`` when nothing changed since the last run."""
    memory_dir = Path(memory_dir)
    files = [memory_dir / "MEMORY.md", memory_dir / "profile.json", memory_dir / "USER.md"]
    stamp = _digest(files)
    if not force and store.meta("sync:global") == stamp:
        return None
    profile = {}
    sources = _gather({
        "MEMORY.md": lambda: _markdown_items(files[0], scope="global", kind="fact"),
        "profile.json": lambda: _profile_items(profile.setdefault("data", _load_profile(files[1]))),
        "USER.md": lambda: _user_md_items(profile, files[1], files[2]),
    })
    report = _mirror(store, "global", sources)
    if "data" in profile:
        _keep_profile_history(store, profile["data"])
    if all(isinstance(v, int) for v in report.values()):
        store.set_meta("sync:global", stamp)
    return report


def _user_md_items(profile: dict, profile_path: Path, user_path: Path) -> list[dict]:
    """USER.md notes the profile does not already hold: profile.json imports USER.md itself
    (``legacy_import``), under topics of its own, so the same words must not arrive twice."""
    from .memory import note_key
    # An unreadable profile makes USER.md unsafe to judge too: both wait for the next run.
    data = profile.setdefault("data", _load_profile(profile_path)) or {}
    if isinstance(data.get("legacy"), dict) and data["legacy"].get("USER.md"):
        return []
    held = {note_key(e.get("text", "")) for e in data.get("entries", []) if isinstance(e, dict)}
    return [item for item in _markdown_items(user_path, scope="global", kind="core") if note_key(item["text"]) not in held]


def sync_project(store: MemoryStore, root: Path, *, force: bool = False) -> dict | None:
    from .workspace import AGENT_DIR, MEMORY_FILE
    path, scope = Path(root) / AGENT_DIR / MEMORY_FILE, project_scope(root)
    stamp = _digest([path])
    if not force and store.meta(f"sync:{scope}") == stamp:
        return None
    report = _mirror(store, scope, _gather({"MEMORY.md": lambda: _markdown_items(path, scope=scope, kind="fact")}))
    if all(isinstance(v, int) for v in report.values()):
        store.set_meta(f"sync:{scope}", stamp)
    return report


def _keep_profile_history(store: MemoryStore, data: dict | None) -> None:
    if not data:
        return
    known = {p.get("id") for p in store.proposals() if p.get("imported_from")}
    for proposal in data.get("proposals", []):
        if isinstance(proposal, dict) and proposal.get("id") not in known:
            store.add_proposal({**proposal, "imported_from": "profile.json"})
    if data.get("revisions"):
        store.archive_legacy("profile.json", data["revisions"])


def import_all(store: MemoryStore, memory_dir: Path, projects: list[Path] = ()) -> dict:
    """Everything Akatikito remembers today. Returns how many new memories came from each source."""
    report: dict[str, int | str] = {"forgotten": 0}
    result = sync_global(store, memory_dir) or {"MEMORY.md": 0, "profile.json": 0, "USER.md": 0, "forgotten": 0}
    report["forgotten"] += result.pop("forgotten")
    report.update(result)
    for root in projects:
        result = sync_project(store, root) or {"MEMORY.md": 0, "forgotten": 0}
        report["forgotten"] += result.pop("forgotten")
        report[f"project:{Path(root).name}"] = result["MEMORY.md"]
    return report


def import_markdown(store: MemoryStore, path: Path, *, scope: str = "global", kind: str = "fact") -> int:
    """Add the notes of one Markdown file (never forgets). Returns the number of NEW memories."""
    before = store.count()
    store.add_many(_markdown_items(Path(path), scope=scope, kind=kind), actor="migration")
    return store.count() - before


def import_profile(store: MemoryStore, path: Path) -> int:
    """Add ``profile.json`` entries and keep its proposals and revisions (never forgets)."""
    data = _load_profile(Path(path))
    before = store.count()
    store.add_many(_profile_items(data), actor="migration")
    _keep_profile_history(store, data)
    return store.count() - before


def export_markdown(store: MemoryStore) -> str:
    """Every active memory as readable Markdown, grouped by scope and kind (for reading or backup)."""
    rows, offset = [], 0
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
