"""MEMORY-PLAN M0: recall quality and speed of a memory selection engine.

    python scripts/memory_eval.py                 # every engine available
    python scripts/memory_eval.py --engine baseline --json

`baseline` is the selection Akatikito uses today when memory exceeds its prompt budget
(`context.memory_excerpt`: lines ranked by how many 4+ character query fragments they contain).
`recall` is the new engine (`akatikito.memory_recall`), once it exists.
Synthetic data only: the owner's real memory is never read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "memory_eval" / "corpus.json"
TOP = 8


def load_corpus(path: Path = CORPUS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class BaselineEngine:
    """Today's excerpt ranking, applied to one memory per line."""

    name = "baseline"

    def __init__(self, memories: list[dict]):
        self.rows = [(m["id"], m["text"].casefold()) for m in memories]

    def search(self, query: str, limit: int = TOP) -> list[str]:
        terms = set(re.findall(r"[\w/-]{4,}", query.casefold()))
        scored = [(sum(t in text for t in terms), i) for i, (_, text) in enumerate(self.rows)]
        ranked = sorted((s for s in scored if s[0] > 0), reverse=True)
        return [self.rows[i][0] for _, i in ranked[:limit]]


class RecallEngine:
    """The new store + recall, loaded into a throw-away database."""

    name = "recall"

    def __init__(self, memories: list[dict]):
        from akatikito.memory_recall import recall
        from akatikito.memory_store import MemoryStore
        self._recall = recall
        self._dir = tempfile.TemporaryDirectory(prefix="akatikito-memeval-")
        self.store = MemoryStore(Path(self._dir.name) / "memory.db")
        self.ids = {}
        for m in memories:
            row_id = self.store.add(m["text"], source="eval")
            self.ids[row_id] = m["id"]

    def search(self, query: str, limit: int = TOP) -> list[str]:
        result = self._recall(self.store, query, limit=limit, max_chars=10_000, touch=False)
        return [self.ids.get(item.id, f"#{item.id}") for item in result.items]

    def close(self):
        self.store.close()
        self._dir.cleanup()


def evaluate(engine, questions: list[dict]) -> dict:
    by_tag: dict[str, list[int]] = {}
    noise = []
    misses = []
    started = time.perf_counter()
    for q in questions:
        got = engine.search(q["q"])
        hit = int(all(e in got for e in q["expect"]))
        by_tag.setdefault(q["tag"], []).append(hit)
        noise.append(len([g for g in got if g not in q["expect"]]))
        if not hit:
            misses.append({"q": q["q"], "expect": q["expect"], "got": got})
    elapsed = (time.perf_counter() - started) / max(1, len(questions))
    hits = [h for values in by_tag.values() for h in values]
    return {
        "engine": engine.name,
        "recall_at_8": round(sum(hits) / len(hits), 3),
        "by_tag": {tag: round(sum(v) / len(v), 3) for tag, v in sorted(by_tag.items())},
        "avg_irrelevant": round(sum(noise) / len(noise), 2),
        "ms_per_query_small": round(elapsed * 1000, 2),
        "misses": misses,
    }


def scale_latency(engine_cls, memories: list[dict], size: int = 10_000) -> float:
    """Milliseconds per query with `size` memories (the corpus repeated with distinct suffixes)."""
    rows = [{"id": f"{m['id']}-{i}", "text": f"{m['text']} [{i}]"}
            for i in range(size // len(memories) + 1) for m in memories][:size]
    engine = engine_cls(rows)
    try:
        queries = ["Πότε γίνεται το αντίγραφο ασφαλείας;", "deepseek-v4-flash", "When do we deploy?",
                   "海鲜", "pyrosvestis", "Wann ist die Steuererklärung fällig?"]
        engine.search(queries[0])  # warm caches
        started = time.perf_counter()
        for q in queries * 5:
            engine.search(q)
        return round((time.perf_counter() - started) * 1000 / (len(queries) * 5), 2)
    finally:
        if hasattr(engine, "close"):
            engine.close()


def available_engines() -> list[type]:
    engines: list[type] = [BaselineEngine]
    try:
        import akatikito.memory_recall  # noqa: F401
        import akatikito.memory_store  # noqa: F401
        engines.append(RecallEngine)
    except ImportError:
        pass
    return engines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", choices=["baseline", "recall"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--misses", action="store_true", help="list the questions each engine missed")
    parser.add_argument("--scale", type=int, default=10_000, help="memories for the latency run (0 = skip)")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    corpus = load_corpus()
    reports = []
    for cls in available_engines():
        if args.engine and cls.name != args.engine:
            continue
        engine = cls(corpus["memories"])
        try:
            report = evaluate(engine, corpus["questions"])
        finally:
            if hasattr(engine, "close"):
                engine.close()
        if args.scale:
            report[f"ms_per_query_{args.scale}"] = scale_latency(cls, corpus["memories"], args.scale)
        reports.append(report)
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0
    for r in reports:
        print(f"== {r['engine']}: recall@8={r['recall_at_8']}  irrelevant/query={r['avg_irrelevant']}  "
              f"ms/query={r['ms_per_query_small']}" + (f"  ms/query@{args.scale}={r.get(f'ms_per_query_{args.scale}')}"
                                                     if args.scale else ""))
        print("   " + "  ".join(f"{tag}={value}" for tag, value in r["by_tag"].items()))
        if args.misses:
            for m in r["misses"]:
                print(f"   MISS {m['q']!r} expect={m['expect']} got={m['got'][:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
