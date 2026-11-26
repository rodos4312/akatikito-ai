# ADR 0001 — Per-Call SQLite Connections & KV-Cache-Safe Memory Recall

- **Status:** Accepted
- **Context:** Akatikito runs a threaded desktop HTTP server, background tasks, Doctor diagnostics, and optional CLI commands concurrently on Windows. Holding a single long-lived SQLite connection across threads risks `OperationalError: database is locked`, file-handle locks during backups/upgrades, and cross-thread cursor issues. Meanwhile, injecting recalled memories into the `system` prompt on every turn invalidates provider prompt caches (KV cache).
- **Decision:**
  1. Central Memory (`akatikito/memory_store.py`) uses WAL mode, FTS5 multilingual indexing (`snowballstemmer` + Greek/Greeklish + CJK bigrams), and opens/closes clean connections per operation (Doctor runs read-only `PRAGMA quick_check`).
  2. Automatic recall (`akatikito/memory_central.py`) injects recalled notes through `context.observe()` as dynamic context messages after the static system policy, keeping the system prefix byte-identical for prompt caching.
- **Consequences:** Slightly higher per-query open overhead (~0.2 ms), in exchange for zero cross-thread locks, clean Windows backups/upgrades, and high prompt-cache hit rates.
