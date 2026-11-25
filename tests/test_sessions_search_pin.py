"""Tests for full-text session search (Greek/English diacritic-insensitive) and Pin (PRO-PLAN B5)."""
from __future__ import annotations

import time

from akatikito.app_api import AppState
from akatikito.sessions import SessionStore


def test_session_pin_sorts_first_and_persists(tmp_path):
    store = SessionStore(tmp_path)
    s1 = store.create()
    store.save(s1, transcript=[{"role": "user", "text": "Πρώτη παλιά συζήτηση"}])
    time.sleep(0.02)
    s2 = store.create()
    store.save(s2, transcript=[{"role": "user", "text": "Δεύτερη νεότερη συζήτηση"}])

    # Before pin: newest (s2) is first
    assert [r["id"] for r in store.list()] == [s2, s1]

    # Pin s1 -> s1 jumps to top without losing s2
    store.set_pinned(s1, True)
    rows = store.list()
    assert [r["id"] for r in rows] == [s1, s2]
    assert rows[0]["pinned"] is True and rows[1]["pinned"] is False


def test_session_full_text_search_handles_greek_accents_and_ignores_tool_outputs(tmp_path):
    store = SessionStore(tmp_path)
    s1 = store.create()
    store.save(
        s1,
        title="Βοήθεια γραφείου",
        transcript=[
            {"role": "user", "text": "Θέλω να ελέγξω τον λογαριασμό της ΔΕΗ για τον Οκτώβριο"},
            {"role": "assistant", "text": "Ο λογαριασμός ΔΕΗ έχει χρέωση 112€."},
        ],
        messages=[
            {"role": "tool", "tool_call_id": "t1", "content": "SECRET_TOOL_ONLY_MARKER_XYZ"},
        ],
    )
    s2 = store.create()
    store.save(
        s2,
        title="Συνταγή καφέ",
        transcript=[{"role": "user", "text": "Φτιάξε έναν freddo espresso"}],
    )

    # Accent-insensitive Greek search matches transcript body even though title didn't mention ΔΕΗ
    hits = store.search("λογαριασμος δεη")
    assert len(hits) == 1
    assert hits[0]["id"] == s1
    assert "ΔΕΗ" in hits[0]["snippet"]

    # Tool-only outputs are NOT indexed (per PRO-PLAN B5)
    assert store.search("SECRET_TOOL_ONLY_MARKER_XYZ") == []


def test_app_state_pin_and_search_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    proj = tmp_path / "proj"
    proj.mkdir()
    store = SessionStore(proj)
    sid = store.create()
    store.save(sid, transcript=[{"role": "user", "text": "Τιμολόγιο προμηθευτή Άλφα"}])

    state = AppState()
    try:
        state.settings.project = str(proj)
        pin_res = state.pin_session(sid, True, str(proj))
        assert pin_res.get("ok") is True and pin_res.get("pinned") is True

        search_res = state.search_sessions("προμηθευτη αλφα")
        assert len(search_res["results"]) == 1
        assert search_res["results"][0]["id"] == sid
        assert search_res["results"][0]["pinned"] is True
    finally:
        state.close()
