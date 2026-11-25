"""What the memory screen can do (MEMORY-PLAN M6): see, search, correct, forget, judge.

Until every write goes to the store, a memory that came from a legacy file is corrected or
forgotten IN THAT FILE (``MEMORY.md`` line, ``profile.json`` entry) and the store is re-synced;
otherwise the next sync would bring it back. Memories born in the store are changed in place.
"Forget for good" also erases the words from the legacy history (profile revisions, USER.md).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import memory
from .memory_import import TAGS, project_scope
from .memory_store import MemoryRefused, MemoryStore

ERASED = "[ξεχάστηκε οριστικά]"
ACTIONS = {"add": "Προστέθηκε", "revise": "Διορθώθηκε", "forget": "Ξεχάστηκε", "forget_hard": "Ξεχάστηκε οριστικά"}


def _public(row: dict, root: Path | None) -> dict:
    scope = "global" if row["scope"] == "global" else (
        "project" if root is not None and row["scope"] == project_scope(root) else "other")
    return {k: row[k] for k in ("id", "kind", "status", "topic", "text", "source", "importance", "use_count",
                                "helpful", "unhelpful", "created_at", "updated_at", "last_used_at")} | {"scope": scope}


def view(s: MemoryStore, root: Path | None, *, query: str = "", scope: str = "all", kind: str = "",
         offset: int = 0, limit: int = 30) -> dict:
    """One page of memories (global + this project), or the best matches for ``query``."""
    from .memory_recall import recall
    scopes = {"all": ["global", project_scope(root)] if root else ["global"], "global": ["global"],
              "project": [project_scope(root)] if root else []}.get(scope, ["global"])
    limit = max(1, min(100, int(limit)))
    if query.strip():
        found = recall(s, query, project_scope=project_scope(root) if root else None, limit=limit,
                       max_chars=10**6, touch=False).items
        rows = [(i.row, i.why) for i in found if i.row["scope"] in scopes and (not kind or i.row["kind"] == kind)]
        return {"rows": [_public(r, root) | {"matched": why} for r, why in rows], "total": len(rows), "next": None,
                "proposals": proposals(s)}
    rows, total = s.active_in(scopes, kind=kind or None, limit=limit, offset=max(0, int(offset)))
    nxt = offset + limit if offset + limit < total else None
    return {"rows": [_public(r, root) for r in rows], "total": total, "next": nxt, "proposals": proposals(s)}


def history(s: MemoryStore, memory_id: int) -> list[dict]:
    """Every version of a memory, oldest first, in plain words."""
    import json
    chain, current = [], s.get(memory_id)
    while current is not None:
        chain.append(current["id"])
        previous = s.predecessor(current["id"])
        current = s.get(previous) if previous else None
    events = sorted((h for memory_id in chain for h in s.history(memory_id)), key=lambda h: (h["at"], h["id"]))
    text = (lambda raw: (json.loads(raw) or {}).get("text", "") if raw else "")
    return [{"action": ACTIONS.get(h["action"], h["action"]), "at": h["at"], "by": h["actor"],
             "before": text(h["before_json"]), "after": text(h["after_json"])} for h in events]


def proposals(s: MemoryStore) -> list[dict]:
    """Suggestions waiting for the owner (M7), not the archived ones of the old profile."""
    return [p for p in s.proposals() if not p.get("imported_from") and p.get("state", "pending") == "pending"]


# ── legacy write-through ───────────────────────────────────────────────────────────────────────
def _markdown_path(row: dict, root: Path | None) -> Path:
    from .workspace import AGENT_DIR, MEMORY_FILE
    if row["scope"] == "global":
        return Path(memory.DIR) / ("USER.md" if row["source"] == TAGS["USER.md"] else "MEMORY.md")
    if root is not None and row["scope"] == project_scope(root):
        return Path(root) / AGENT_DIR / MEMORY_FILE
    raise MemoryRefused("Αυτή η μνήμη ανήκει σε άλλο project· άνοιξε εκείνο το project για να την αλλάξεις.")


def _rewrite_markdown(path: Path, note: str, replacement: str | None) -> None:
    """Replace (or drop, with ``None``) the line holding ``note``; every other byte stays as it was."""
    from .local_store import LOCK, atomic_bytes, checked
    path = checked(path)
    with LOCK:
        raw = path.read_bytes() if path.is_file() else b""
        lines = raw.decode("utf-8-sig").splitlines(keepends=True)
        target = memory.note_key(note)
        kept, found = [], False
        for line in lines:
            if not found and memory.note_key(line.strip().lstrip("-*•").strip()) == target:
                found = True
                if replacement is not None:
                    ending = line[len(line.rstrip("\r\n")):] or "\n"
                    kept.append(f"- {replacement}{ending}")
                continue
            kept.append(line)
        if not found:
            raise MemoryRefused("Η σημείωση δεν βρέθηκε πια στο αρχείο της· ανανέωσε την οθόνη.")
        prefix = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
        atomic_bytes(path, prefix + "".join(kept).encode("utf-8"), expected=hashlib.sha256(raw).hexdigest())


def _profile_entry(row: dict) -> dict:
    from .profile import Profile
    entries = Profile().load()["entries"]
    key = (memory.note_key(row["topic"]), memory.note_key(row["text"]))
    found = next((e for e in entries if (memory.note_key(e.get("topic", "")), memory.note_key(e["text"])) == key), None)
    if found is None:
        raise MemoryRefused("Το στοιχείο δεν βρέθηκε πια στο προφίλ· ανανέωσε την οθόνη.")
    return found


def _revise_profile(row: dict, text: str) -> None:
    from .profile import Profile
    profile, entry = Profile(), _profile_entry(row)
    profile.propose(entry["topic"], text, entry["kind"], entry["status"], replaces=[entry["id"]])
    pending = next(p for p in profile.listing()["proposals"]
                   if p["text"] == text.strip() and entry["id"] in p.get("replaces", []))
    profile.answer(pending["id"], True, expected=pending["review_hash"])


def _scrub_profile(row: dict) -> None:
    """Remove the words of a forgotten item from the profile's revision history and USER.md."""
    from .local_store import LOCK
    from .profile import Profile
    target = memory.note_key(row["text"])

    def scrub(value):
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, dict) and memory.note_key(value.get("text", "")) == target:
            return {**value, "text": ERASED, "topic": ""}
        return value

    with LOCK:
        profile = Profile()
        data = profile.load()
        for revision in data["revisions"]:
            for field in ("before", "after"):
                if field in revision:
                    revision[field] = scrub(revision[field])
            if isinstance(revision.get("original"), str):
                revision["original"] = "\n".join(
                    line for line in revision["original"].splitlines()
                    if memory.note_key(line.strip().lstrip("-*•").strip()) != target)
        profile._save(data)
    user_md = Path(memory.DIR) / "USER.md"
    if user_md.is_file():
        try:
            _rewrite_markdown(user_md, row["text"], None)
        except MemoryRefused:
            pass  # the note was never (or no longer) in USER.md


# ── actions ────────────────────────────────────────────────────────────────────────────────────
def _resync(s: MemoryStore, root: Path | None) -> None:
    from .memory_import import sync_global, sync_project
    sync_global(s, Path(memory.DIR), force=True)
    if root is not None:
        sync_project(s, Path(root), force=True)


def edit(s: MemoryStore, root: Path | None, memory_id: int, text: str) -> dict:
    row = _active(s, memory_id)
    text = " ".join(str(text or "").split())
    if not text:
        raise MemoryRefused("Κενή σημείωση — δεν αποθηκεύτηκε.")
    if row["source"] in (TAGS["MEMORY.md"], TAGS["USER.md"]):
        _check_safe(text)
        _rewrite_markdown(_markdown_path(row, root), row["text"], text)
    elif row["source"] == TAGS["profile.json"]:
        _revise_profile(row, text)
    else:
        return {"ok": True, "id": s.update(memory_id, text=text)}
    _resync(s, root)
    return {"ok": True}


def forget(s: MemoryStore, root: Path | None, memory_id: int, *, hard: bool = False) -> dict:
    row = s.get(memory_id)
    if row is None:
        raise MemoryRefused("Δεν υπάρχει αυτή η μνήμη.")
    if not row["forgotten"] and row["superseded_by"] is None:
        if row["source"] in (TAGS["MEMORY.md"], TAGS["USER.md"]):
            _rewrite_markdown(_markdown_path(row, root), row["text"], None)
        elif row["source"] == TAGS["profile.json"]:
            from .profile import Profile
            Profile().forget(_profile_entry(row)["id"])
    if hard and row["source"] == TAGS["profile.json"]:
        _scrub_profile(row)
    _resync(s, root)
    if hard or not s.get(memory_id)["forgotten"]:
        s.forget(memory_id, hard=hard)
    return {"ok": True}


def feedback(s: MemoryStore, memory_id: int, helpful: bool) -> dict:
    _active(s, memory_id)
    s.feedback(memory_id, bool(helpful))
    return {"ok": True}


def answer_proposal(s: MemoryStore, proposal_id: int, approve: bool, text: str | None = None) -> dict:
    """Approve (optionally edited) or reject a suggestion; a revision replaces the memory it names."""
    pending = next((p for p in proposals(s) if p["id"] == int(proposal_id)), None)
    if pending is None:
        raise MemoryRefused("Η πρόταση δεν περιμένει πια απάντηση· ανανέωσε την οθόνη.")
    result: dict = {"ok": True}
    if approve:
        fields = {k: pending[k] for k in ("topic", "kind", "status", "keywords", "importance") if k in pending}
        body = " ".join(str(text if text is not None else pending["text"]).split())
        replaces = pending.get("replaces")
        if replaces and s.get(replaces) and not s.get(replaces)["forgotten"]:
            result["id"] = s.update(replaces, text=body, **{k: v for k, v in fields.items() if k != "kind"})
        else:
            result["id"] = s.add(body, scope=pending.get("scope", "global"), source=pending.get("source", "learned"),
                                 actor="owner", **fields)
    s.set_proposal_state(int(proposal_id), "approved" if approve else "rejected", at=time.time())
    return result


def _active(s: MemoryStore, memory_id: int) -> dict:
    row = s.get(memory_id)
    if row is None or row["forgotten"] or row["superseded_by"] is not None:
        raise MemoryRefused("Η μνήμη δεν υπάρχει πια ή έχει αλλάξει· ανανέωσε την οθόνη.")
    return row


def _check_safe(text: str) -> None:
    from . import memory_guard
    reason = memory_guard.scan(text)
    if reason:
        raise MemoryRefused(memory_guard.refusal(reason))
