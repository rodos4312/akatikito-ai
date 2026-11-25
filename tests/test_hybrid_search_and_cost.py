"""Tests for per-answer Cost/Cache-Hit badge calculation and Hybrid Semantic Document Search."""
from __future__ import annotations

from akatikito.doc_index import DocIndex
from akatikito.usage_ledger import summarize_turn_delta


def test_summarize_turn_delta_computes_cache_pct_and_eur_cost():
    before = {"prompt_tokens": 1000, "completion_tokens": 200, "cache_hit_tokens": 500}
    after = {"prompt_tokens": 3000, "completion_tokens": 700, "cache_hit_tokens": 2100}
    prices = {"input": 0.27, "cached": 0.07, "output": 1.10}

    res = summarize_turn_delta(before, after, prices)
    assert res["prompt_tokens"] == 2000
    assert res["completion_tokens"] == 500
    assert res["cache_hit_tokens"] == 1600
    assert res["cache_hit_pct"] == 80
    # (400*0.27 + 1600*0.07 + 500*1.10) / 1e6 = (108 + 112 + 550) / 1e6 = 0.00077
    assert res["cost_eur"] == 0.00077

    res_no_price = summarize_turn_delta(before, after, None)
    assert res_no_price["cost_eur"] is None


def test_hybrid_doc_search_finds_greek_electricity_and_bilingual_invoice(tmp_path):
    (tmp_path / "DEH_2026_03.md").write_text("Λογαριασμός ΔΕΗ Μαρτίου: 420 kWh, ποσό 84,50 EUR.\n", encoding="utf-8")
    (tmp_path / "timologio_104.txt").write_text("Τιμολόγιο παροχής υπηρεσιών με ΦΠΑ 24%.\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("Σημειώσεις για τη συνάντηση της Δευτέρας.\n", encoding="utf-8")

    idx = DocIndex(tmp_path)

    # Conceptual Greek query "ρεύμα" matches DEH_2026_03.md even though the word "ρεύμα" is absent!
    r1 = idx.search("ρεύμα")
    paths1 = [item["path"] for item in r1["results"]]
    assert "DEH_2026_03.md" in paths1
    deh_row = next(item for item in r1["results"] if item["path"] == "DEH_2026_03.md")
    assert deh_row["match_type"] == "semantic"

    # Cross-lingual query "invoice" matches Greek "Τιμολόγιο ... ΦΠΑ"
    r2 = idx.search("invoice")
    paths2 = [item["path"] for item in r2["results"]]
    assert "timologio_104.txt" in paths2

    # Exact FTS5 query still returns exact match first
    r3 = idx.search("Δευτέρας")
    assert r3["results"] and r3["results"][0]["path"] == "notes.txt"
    assert r3["results"][0]["match_type"] == "exact"
