# Settings Reference — Akatikito AI

Every persisted field in `akatikito/settings.py::Settings` (`data/settings.json`).
Checked automatically against `Settings` by `scripts/release_check.py` (Q11 docs-drift gate).

| Field | Default | Purpose |
|---|---|---|
| `provider` | `"DeepSeek"` | Active primary AI provider name. |
| `base_url` | `""` | Custom OpenAI-compatible endpoint URL (optional). |
| `model` | `""` | Active primary model identifier. |
| `fast_provider` | `""` | Smart-routing provider for fast/simple tasks. |
| `fast_model` | `""` | Smart-routing model for fast/simple tasks. |
| `smart_provider` | `""` | Smart-routing provider for complex coding/analysis tasks. |
| `smart_model` | `""` | Smart-routing model for complex tasks. |
| `fallback_provider` | `""` | Explicit fallback provider used on 429/5xx retries (B3). |
| `fallback_model` | `""` | Explicit fallback model identifier (B3). |
| `max_steps` | `50` | Maximum agent tool-loop iterations per turn. |
| `verify_command` | `""` | Verification command run after edits (e.g. `pytest`). |
| `project` | `""` | Active workspace directory path. |
| `keys` | `{}` | DPAPI/masked API keys keyed by provider name. |
| `models` | `{}` | Remembered default model per provider. |
| `key_origins` | `{}` | Expected API base host per provider (prevents cross-host key leaks). |
| `autonomous` | `False` | When `True`, skips owner approval prompts for commands/writes. |
| `cautious_edits` | `False` | When `True`, requires approval even for ordinary workspace edits. |
| `advanced_view` | `False` | Toggles Simple vs Pro UI layout. |
| `advanced_features` | `[]` | Opt-in high-risk features gated by `advanced.py` (`self_update`, `subtasks`, `git`). |
| `daily_budget` | `0.0` | Daily USD spend cap (`0.0` = unlimited). |
| `model_prices` | `{}` | Custom per-model input/output/cached token prices. |
| `vision_models` | `[]` | Model IDs explicitly enabled for image attachments. |
| `checkpoints` | `True` | Enables local bare-Git undo checkpoints before edits. |
| `allowed_commands` | `[]` | Owner-approved shell commands allowed without re-prompting. |
| `voice_model` | `"base"` | Local Faster-Whisper speech-to-text model size. |
| `voice_language` | `"el"` | Speech-to-text recognition language code. |
| `ui_language` | `"el"` | UI & assistant language (`el`, `en`, `ru`, `fr`, `de`, `es`). |
| `ui_theme` | `"dark"` | Visual theme (`dark` or `light`). |
| `ui_font_size` | `14` | Chat/editor base font size in pixels (`12`–`20`). |
| `sidebar_collapsed` | `False` | Persists collapsible left sidebar state (`Ctrl+\`). |
| `recent_projects` | `[]` | Recently opened project folders (also Mobile Bridge allowlist). |
| `global_hotkey` | `"Alt+Space"` | Windows global shortcut to focus/toggle the desktop window. |
| `background_mode` | `False` | Enables 24/7 headless background service for scheduled tasks & channels (L2). |
| `background_autostart` | `False` | Registers Akatikito background service in Windows user Startup (HKCU Run) (L2). |
| `background_service` | `False` | Single toggle in Settings to run the 24/7 background service & Windows autostart (requires `daily_budget > 0`) (G2). |
| `notes_folder` | `""` | Optional Obsidian/Markdown notes vault folder (empty = disabled) (L5). |
| `onboarding_complete` | `False` | Marks first-run onboarding wizard completion. |
| `warning` | `""` | Transient non-persisted warning banner when loading a corrupted file. |
