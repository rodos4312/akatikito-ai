# Akatikito AI — Public Roadmap

This roadmap outlines planned releases focused on **Speed**, **Security**, and **Everyday + Developer Capabilities**.

---

## v2.1 — GitHub Auto-Updater, Startup Speed & Full 6-Language Polish
- [x] **Verified GitHub Release Updater**: 1-click update check & install from GitHub Releases (`SHA256SUMS.txt` + `MANIFEST.json` verification, automatic backup & rollback, never touches user `data/` or `.venv/`).
- [x] **Full 6-Language UI (`el`, `en`, `ru`, `de`, `es`, `fr`)**: Extended across Setup Onboarding, Settings, Update Checker, Files, and Agent prompts.
- [x] **SEO & GitHub Pages**: Live site at [rodos4312.github.io/akatikito-ai](https://rodos4312.github.io/akatikito-ai/) with `SoftwareApplication` JSON-LD, sitemap, and Open Graph card.

## v2.2 — Enterprise-Grade Local Security & Resource Isolation
- [x] **Win32 Job Objects for `run_command`**: Hard memory/process limits (`2048 MiB`, `32` child processes) and guaranteed full process-tree cleanup (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) on Stop/timeout (`akatikito/job_sandbox.py`).
- [x] **Per-Project Permission Profiles**: Save trust levels per folder (`Read-Only`, `Office`, `Developer`) stored outside workspaces (`akatikito/project_profiles.py` + `/api/project-profile`).
- [x] **Tamper-Evident Audit Log**: SHA-256 hash-chained local log of tool calls, file mutations, and owner approvals with live integrity verification (`akatikito/audit_log.py` + `/api/audit`).
- [x] **Prompt Injection Guardrails**: Automated tagging & isolation of suspicious imperative instructions inside untrusted web/PDF/MCP payloads (`akatikito/injection_guard.py`).

## v2.3 — Local Document Library & Rich Workspace UX *(Current)*
- [x] **Local Document Search (SQLite FTS5 + BM25)**: Instant offline search across folders of PDF, Word, Excel, CSV, and Markdown files — zero cloud, zero GPU (`akatikito/doc_index.py` + `search_documents` tool + `/api/docs/search`).
- [x] **Rich Right-Sidebar Previews**: Rendered Markdown toggle, image previews, and interactive CSV/TSV tables directly in the Files panel (`akatikito/file_view.py`).
- [x] **1-Click User Routines**: Built-in + custom repeatable multi-step workflows saved as 1-click buttons (`akatikito/routines.py` + `/api/routines/*`).
- [x] **Deterministic Eval Harness**: Automated 10-case benchmark suite (`scripts/run_evals.py`) verifying tools, safety guards, file organization, and document extraction.
