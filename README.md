# Akatikito — Local-First Desktop AI Agent for Windows

[![CI](https://img.shields.io/badge/CI-Windows%20Tests%20%2B%20Evals-blue)](#testing--verification)
[![Website](https://img.shields.io/badge/Website-GitHub%20Pages-38bdf8)](https://rodos4312.github.io/akatikito-ai/)
[![Languages](https://img.shields.io/badge/UI%20Languages-EL%20%7C%20EN%20%7C%20RU%20%7C%20DE%20%7C%20ES%20%7C%20FR-green)](#multi-language-interface)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Akatikito** is a local-first, security-hardened desktop AI assistant and coding agent built for everyday users and developers alike. It runs locally in its own native window (**zero background console window**), connects to your choice of LLM provider (DeepSeek, OpenAI, Anthropic, Gemini, OpenRouter, Ollama, or custom OpenAI-compatible endpoints), and keeps all your files, keys, and memory under strict owner control.

---

## Highlights

- **Windowless Native Desktop App**: Launches cleanly from a Start Menu / Desktop shortcut via `pythonw.exe` — no black terminal window left behind.
- **Simple vs Pro View**: 1-click switch (`✨ Simple` / `⚙️ Pro`) hides technical clutter for everyday users and unlocks full developer controls on demand.
- **6-Language Interface**: Live 1-click switching between **English (`en`)**, **Greek (`el`)**, **Russian (`ru`)**, **German (`de`)**, **Spanish (`es`)**, and **French (`fr`)** — updating both the UI and the agent's default response language.
- **Safe File Organization & Undo**: Paginated folder browser with multi-select Move, Copy, Bulk Rename (`{index}_{stem}{ext}`), New Folder, and Safe Trash — always previewed before applying, backed up with NTFS stream preservation, and reversible via verified Undo.
- **Real Office Documents & Exact Math**:
  - Creates real `.docx`, `.xlsx` (with literal-text protection against Excel formula injection), and `.pdf` (isolated Chromium CDP primary + offline TrueType Unicode vector fallback).
  - 50-digit `Decimal` calculator with exact VAT breakdown + strict CSV/JSON/Markdown converter.
- **Isolated Web & Research**: DuckDuckGo + Wikipedia search, RSS/News, YouTube transcripts, and headless Chromium via Chrome DevTools Protocol (CDP) routed through a **DNS-pinned local PublicProxy** blocking loopback/private SSRF sub-requests.
- **Multi-Language Coding Engine**: Native pinned **Tree-sitter** parsers for 12 languages (C, C++, Rust, Java, C#, PHP, JS, TS/TSX, Go, HTML, CSS, XML) + offline parsers for **Vue, Svelte, Angular**, with automatic graceful fallback and bare-Git object checkpoints.
- **MCP Hub & Channels**: 1-click MCP presets (GitHub, Notion, Playwright, SQLite, Filesystem, Memory), Claude Desktop / Cursor JSON import, masked secret editing, hash-bound `autoConnect`, and optional Telegram / Discord gateway.
- **OS-Level Security**:
  - All API keys, MCP secrets, and channel tokens are encrypted at rest with **Windows DPAPI (`CryptProtectData`)**.
  - Hash-authorized scheduled jobs run in isolated sessions with crash-safe `uncertain` recovery.
  - 1-click **Health Check & Anonymized Diagnostics `.zip`** (strictly strips all secrets and user data).

---

## Quick Install (Windows 10 / 11)

1. Make sure **Python 3.11+** is installed (with *"Add python.exe to PATH"* checked). Optional: **Node.js 22+** for Vue/Svelte/Angular parsers and **Git** for checkpoints.
2. Clone this repository or download the latest Portable ZIP from **[Releases](https://github.com/rodos4312/akatikito-ai/releases)**:
   ```cmd
   git clone https://github.com/rodos4312/akatikito-ai.git
   cd akatikito-ai
   Install.cmd
   ```
3. Launch **Akatikito** from your **Desktop** or **Start Menu** shortcut (opens cleanly with no console window).

To remove the shortcuts later, run `Uninstall.cmd`.

---

## Multi-Language Interface

Switch languages at any time using the **`🌐`** selector in the top bar (no restart required):

| Code | Language | Code | Language |
| :--- | :--- | :--- | :--- |
| `en` | English | `de` | Deutsch |
| `el` | Ελληνικά | `es` | Español |
| `ru` | Русский | `fr` | Français |

---

## Testing & Verification

```cmd
.venv\Scripts\python.exe -m pytest tests\test_pro_release.py tests\test_best_of_both_worlds.py -q
.venv\Scripts\python.exe scripts\run_evals.py
.venv\Scripts\python.exe packaging\build_portable.py
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full technical blueprint and security model.

---

## Ελληνικά (Σύνοψη)

Το **Akatikito** είναι ένας τοπικός, ασφαλής AI βοηθός και coding agent για Windows με δικό του παράθυρο (χωρίς μαύρο παράθυρο Python), 6 γλώσσες διεπαφής, κρυπτογράφηση κλειδιών με Windows DPAPI, ασφαλή οργάνωση αρχείων με Κάδο & Undo, δημιουργία Word/Excel/PDF, απομονωμένο Chromium browser, Tree-sitter για 12 γλώσσες και πλήρες MCP hub.

**Εγκατάσταση με 1 κλικ:** Τρέξε το `Install.cmd` και άνοιξε το **Akatikito** από τη συντόμευση της Επιφάνειας Εργασίας ή του Μενού Έναρξη.
