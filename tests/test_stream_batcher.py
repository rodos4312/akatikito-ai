"""DeltaBatcher: το πρώτο τμήμα εκπέμπεται αμέσως, τα υπόλοιπα σε δόσεις και το flush στο τέλος."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akatikito.streaming import DeltaBatcher


def test_first_chunk_emits_immediately_and_flush_releases_rest():
    emitted = []

    batcher = DeltaBatcher(emitted.append, flush_interval=60.0, flush_chars=1000)
    batcher.feed("Γεια ")
    batcher.feed("κόσμε")  # εντός ορίου: παραμένει pending
    assert emitted == ["Γεια "]
    batcher.flush()
    assert emitted == ["Γεια ", "κόσμε"]


def test_buffer_size_forces_flush():
    emitted = []

    batcher = DeltaBatcher(emitted.append, flush_interval=60.0, flush_chars=2)
    batcher.feed("abc")
    batcher.feed("de")
    assert emitted == ["abc", "de"]


def test_empty_feeds_do_not_emit():
    emitted = []

    batcher = DeltaBatcher(emitted.append)
    batcher.feed("")
    batcher.feed(None)
    batcher.flush()
    assert emitted == []
