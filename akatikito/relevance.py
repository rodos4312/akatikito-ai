"""Explainable file relevance ranking for large projects (Category 3).

Inspired by MEU's ``meu_coding/relevance.py``: combines deterministic structural
signals (explicit file mentions, exact symbol definitions, filename/token overlap,
import proximity, and related tests) with clear per-file score explanations.
"""
from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass

from .searching import project_files

WEIGHTS: dict[str, float] = {
    "explicit_file": 100.0,
    "exact_symbol": 45.0,
    "filename": 25.0,
    "token_overlap": 15.0,
    "import_dependency": 16.0,
    "related_test": 12.0,
}

_STOPWORDS = frozenset(
    "a an and are as at be by can do does fix for from how i in into is it its me my not of "
    "on or our please should so that the this to up use we what when where which why with you "
    "add make change update file files code function class method module test tests bug error "
    "να το τη τα του της των σε με για από και ή στο στη στον στην ένα μια".split()
)
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_PATHLIKE_RE = re.compile(r"[\w.\-]+(?:[/\\][\w.\-]+)+|[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,5}\b")
_IDENT_RE = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?")
_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|const|let|var|interface|type|struct|func)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.M,
)
_IMPORT_RE = re.compile(
    r"(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|require\(['\"]([^'\"]+)['\"]\)|from\s+['\"]([^'\"]+)['\"])"
)


def split_words(text: str) -> list[str]:
    words = [w.lower() for part in re.split(r"[^A-Za-z0-9\u0370-\u03ff]+", text) for w in (_CAMEL_RE.findall(part) or ([part] if part else []))]
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS]


def is_test_path(rel_path: str) -> bool:
    name = posixpath.basename(rel_path).lower()
    parts = {p.lower() for p in rel_path.replace("\\", "/").split("/")[:-1]}
    return (
        name.startswith("test_")
        or "_test." in name
        or ".test." in name
        or ".spec." in name
        or bool(parts & {"tests", "test", "__tests__", "spec"})
    )


def tested_stem(rel_path: str) -> str:
    stem = posixpath.basename(rel_path).split(".")[0].lower()
    if stem.startswith("test_"):
        stem = stem[5:]
    if stem.endswith("_test"):
        stem = stem[:-5]
    return stem


@dataclass
class RankedFile:
    path: str
    score: float
    reasons: list[str]


def _parse_query(query: str) -> tuple[set[str], set[str], set[str]]:
    raw = str(query or "")
    paths = {m.group(0).replace("\\", "/").lower() for m in _PATHLIKE_RE.finditer(raw)}
    idents = {m.group(1) for m in _IDENT_RE.finditer(raw) if m.group(1).lower() not in _STOPWORDS}
    words = set(split_words(raw))
    return paths, idents, words


def _read_snippet(path, max_bytes: int = 32768) -> str:
    try:
        raw = path.read_bytes()[:max_bytes]
        if b"\x00" in raw:
            return ""
        return raw.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _score_base_signals(rel: str, text: str, q_paths: set[str], q_idents: set[str], q_words: set[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    norm = rel.replace("\\", "/").lower()
    base = posixpath.basename(norm)
    stem = base.split(".")[0]

    if norm in q_paths or base in q_paths:
        score += WEIGHTS["explicit_file"]
        reasons.append("explicit_file")
    elif any(p in norm for p in q_paths):
        score += WEIGHTS["filename"]
        reasons.append("path_match")

    if q_words and (stem in q_words or any(w in split_words(base) for w in q_words)):
        score += WEIGHTS["filename"]
        reasons.append("filename")

    if text and q_idents:
        defs = set(_DEF_RE.findall(text))
        hit_defs = sorted(defs & q_idents)
        if hit_defs:
            score += WEIGHTS["exact_symbol"] + min(20.0, 5.0 * (len(hit_defs) - 1))
            reasons.append("exact_symbol(" + ",".join(hit_defs[:3]) + ")")

    if q_words and text:
        low = text.lower()
        hits = sum(1 for w in q_words if w in low)
        if hits:
            ratio = hits / max(1, len(q_words))
            score += round(WEIGHTS["token_overlap"] * ratio, 1)
            reasons.append(f"tokens({hits}/{len(q_words)})")

    return score, reasons


def _collect_seed_neighbors(records: list[tuple[str, str, float, list[str]]]) -> tuple[set[str], set[str], set[str]]:
    seeds = sorted((r for r in records if r[2] >= WEIGHTS["filename"]), key=lambda r: r[2], reverse=True)[:4]
    seed_paths = {r[0] for r in seeds}
    seed_stems = {tested_stem(r[0]) for r in seeds if tested_stem(r[0])}
    seed_imports: set[str] = set()
    for _rel, text, _sc, _rs in seeds:
        for m in _IMPORT_RE.finditer(text):
            mod = next((g for g in m.groups() if g), "")
            if mod:
                seed_imports.add(mod.replace("\\", "/").split("/")[-1].split(".")[-1].lower())
    return seed_paths, seed_stems, seed_imports


def rank_project_files(ws, query: str, *, max_results: int = 8, time_limit: float = 1.5) -> list[RankedFile]:
    """Rank workspace files by explainable structural relevance to *query*."""
    q_paths, q_idents, q_words = _parse_query(query)
    if not (q_paths or q_idents or q_words):
        return []
    deadline = time.monotonic() + max(0.2, float(time_limit))
    records: list[tuple[str, str, float, list[str]]] = []
    for target in project_files(ws.root):
        if time.monotonic() > deadline:
            break
        rel = ws.relative(target).replace("\\", "/")
        text = _read_snippet(target)
        score, reasons = _score_base_signals(rel, text, q_paths, q_idents, q_words)
        records.append((rel, text, score, reasons))

    seed_paths, seed_stems, seed_imports = _collect_seed_neighbors(records)
    ranked: list[RankedFile] = []
    for rel, _text, score, reasons in records:
        stem = tested_stem(rel)
        if rel not in seed_paths and stem in seed_imports:
            score += WEIGHTS["import_dependency"]
            reasons = [*reasons, "import_dep"]
        if rel not in seed_paths and is_test_path(rel) and stem in seed_stems:
            score += WEIGHTS["related_test"]
            reasons = [*reasons, "related_test"]
        if score > 0:
            ranked.append(RankedFile(path=rel, score=round(score, 1), reasons=reasons))

    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked[: max(1, min(20, int(max_results)))]


def find_relevant_files(ws, query: str, max_results: int = 8) -> str:
    """Tool handler: return top relevant project files with explainable signal scores."""
    items = rank_project_files(ws, query, max_results=max_results)
    if not items:
        return "Δεν βρέθηκαν σχετικά αρχεία για αυτό το ερώτημα."
    lines = [f"Σχετικά αρχεία για «{str(query)[:100]}»:"]
    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. {item.path} (score={item.score:g} · {', '.join(item.reasons)})")
    return "\n".join(lines)


RELEVANCE_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "find_relevant_files",
            "description": (
                "Find and rank the most relevant files in the project for a task, symbol name, "
                "or bug description using structural signals (exact filename, symbol definitions, "
                "token overlap, import neighbors, and related tests). Prefer this over multiple "
                "list_files/search_project calls when locating where to read or edit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Task description, symbol name, or filename"},
                    "max_results": {"type": "integer", "description": "Max files to return (1-20, default 8)"},
                },
                "required": ["query"],
            },
        },
    }
]

RELEVANCE_HANDLERS = {"find_relevant_files": find_relevant_files}
