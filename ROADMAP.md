# Akatikito AI — Public Roadmap

This roadmap outlines planned releases focused on **Speed**, **Security**, and **Everyday + Developer Capabilities**.

---

## v2.1 — GitHub Auto-Updater, Startup Speed & Full 6-Language Polish *(Current)*
- [x] **Verified GitHub Release Updater**: 1-click update check & install from GitHub Releases (`SHA256SUMS.txt` + `MANIFEST.json` verification, automatic backup & rollback, never touches user `data/` or `.venv/`).
- [x] **Full 6-Language UI (`el`, `en`, `ru`, `de`, `es`, `fr`)**: Extended across Setup Onboarding, Settings, Update Checker, Files, and Agent prompts.
- [x] **SEO & GitHub Pages**: Live site at [rodos4312.github.io/akatikito-ai](https://rodos4312.github.io/akatikito-ai/) with `SoftwareApplication` JSON-LD, sitemap, and Open Graph card.

## v2.2 — Enterprise-Grade Local Security & Resource Isolation
- [ ] **Win32 Job Objects for `run_command`**: Hard memory/process limits and guaranteed full process-tree cleanup on Stop/timeout.
- [ ] **Per-Project Permission Profiles**: Save trust levels per folder (`Read-Only`, `Office`, `Developer`).
- [ ] **Tamper-Evident Audit Log**: Searchable local log of tool calls, file mutations, and owner approvals with export.
- [ ] **Prompt Injection Guardrails**: Automated tagging & isolation of suspicious imperative instructions inside untrusted web/PDF/MCP payloads.

## v2.3 — Local Document Library & Rich Workspace UX
- [ ] **Local Document Search (SQLite FTS5 + BM25)**: Instant offline search across folders of PDF, Word, Excel, CSV, and Markdown files — zero cloud, zero GPU.
- [ ] **Rich Right-Sidebar Previews**: Rendered Markdown, image thumbnails, and interactive CSV/Excel tables directly in the Files panel.
- [ ] **1-Click User Routines**: Save repeatable multi-step workflows (e.g. *Morning News → PDF*, *Sort Downloads by Type*) as quick buttons.
- [ ] **Live LLM Benchmark Scorecards**: Published latency, token economy, and accuracy benchmarks across Gemini, OpenAI, Anthropic, DeepSeek, and Ollama.
