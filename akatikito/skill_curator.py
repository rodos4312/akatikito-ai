"""Skill Curator (PRO-PLAN B9): proposes duplicate, stale, and conflicting skill cleanups.

Strictly read-only when curating: NEVER modifies or deletes skills automatically.
Any owner-approved change applies from the next session (preserving B2 prompt cache).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .skills import Skills
from .skills_format import _jaccard, _meaningful_tokens

DEFAULT_STALE_DAYS = 90
DEFAULT_SIMILARITY_THRESHOLD = 0.55


def _skill_tokens(entry: dict) -> set[str]:
    blob = " ".join(
        [
            str(entry.get("title") or ""),
            str(entry.get("description") or ""),
            " ".join(entry.get("triggers") or ()),
            " ".join(entry.get("steps") or ()),
        ]
    )
    return _meaningful_tokens(blob)


def _find_duplicates(entries: list[dict], threshold: float) -> list[dict]:
    proposals: list[dict] = []
    tokens = {e["name"]: _skill_tokens(e) for e in entries}
    for i, first in enumerate(entries):
        for second in entries[i + 1 :]:
            score = _jaccard(tokens[first["name"]], tokens[second["name"]])
            if score >= threshold:
                pct = int(round(score * 100))
                proposals.append(
                    {
                        "kind": "duplicate",
                        "skills": [first["name"], second["name"]],
                        "similarity": round(score, 3),
                        "message": (
                            f"2 skills μοιάζουν {pct}%: «{first['title']}» και «{second['title']}»"
                        ),
                        "actions": ["merge", "keep_both"],
                    }
                )
    return proposals


def _find_stale(entries: list[dict], uses: dict, *, stale_days: int, now_ts: float) -> list[dict]:
    proposals: list[dict] = []
    cutoff = now_ts - (max(1, int(stale_days)) * 86400.0)
    for entry in entries:
        if entry.get("source") == "builtin":
            continue
        path = Path(entry["path"])
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        use_count = int(uses.get(entry["name"], 0) or 0)
        if use_count == 0 and mtime <= cutoff:
            age_days = int((now_ts - mtime) // 86400)
            proposals.append(
                {
                    "kind": "stale",
                    "skills": [entry["name"]],
                    "age_days": age_days,
                    "message": f"Το skill «{entry['title']}» δεν χρησιμοποιήθηκε για {age_days} μέρες.",
                    "actions": ["archive", "keep"],
                }
            )
    return proposals


def _find_conflicts(entries: list[dict]) -> list[dict]:
    proposals: list[dict] = []
    for i, first in enumerate(entries):
        t1 = {str(t).strip().casefold() for t in (first.get("triggers") or ()) if str(t).strip()}
        if not t1:
            continue
        tools1 = set(first.get("required_tools") or ())
        for second in entries[i + 1 :]:
            t2 = {str(t).strip().casefold() for t in (second.get("triggers") or ()) if str(t).strip()}
            shared = sorted(t1 & t2)
            if not shared:
                continue
            tools2 = set(second.get("required_tools") or ())
            if tools1 != tools2:
                proposals.append(
                    {
                        "kind": "conflict",
                        "skills": [first["name"], second["name"]],
                        "shared_triggers": shared,
                        "message": (
                            f"Τα skills «{first['title']}» και «{second['title']}» μοιράζονται "
                            f"triggers ({', '.join(shared[:3])}) αλλά ζητούν διαφορετικά εργαλεία."
                        ),
                        "actions": ["review", "ignore"],
                    }
                )
    return proposals


def curate_skills(
    project: str | Path,
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    now_ts: float | None = None,
) -> dict:
    """Analyze installed skills and return owner-facing cleanup proposals (strictly read-only)."""
    store = Skills(project)
    entries = list(store._entries().values())
    try:
        loaded = store._load()
        uses = loaded.get("uses", {}) if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        uses = {}
    ref_ts = time.time() if now_ts is None else float(now_ts)
    duplicates = _find_duplicates(entries, similarity_threshold)
    stale = _find_stale(entries, uses, stale_days=stale_days, now_ts=ref_ts)
    conflicts = _find_conflicts(entries)
    proposals = duplicates + conflicts + stale
    return {
        "propose_only": True,
        "cache_note": "Οι αλλαγές σε skills ισχύουν από την επόμενη συζήτηση (Β2).",
        "scanned": len(entries),
        "counts": {
            "duplicate": len(duplicates),
            "conflict": len(conflicts),
            "stale": len(stale),
        },
        "proposals": proposals,
    }


def archive_skill(skill_path: str | Path, *, user_approved: bool = False) -> Path:
    """Move a non-builtin skill into an `archived/` sibling folder ONLY on explicit user approval."""
    if not user_approved:
        raise PermissionError("Η αρχειοθέτηση skill απαιτεί ρητή έγκριση του χρήστη.")
    path = Path(skill_path).resolve()
    if not path.is_file() or path.suffix.lower() != ".md":
        raise FileNotFoundError(f"Δεν βρέθηκε αρχείο skill: {path}")
    dest_dir = path.parent / "archived"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    shutil.move(str(path), str(dest))
    return dest
