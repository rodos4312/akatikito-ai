# Akatikito Plugin & Skill SDK Guide (`docs/PLUGINS.md`)

Akatikito plugins let developers add new messaging channels and custom skills without modifying core code.
Developer plugin loading is **disabled by default** and gated behind **Ρυθμίσεις → Advanced → Δικά μου plugins και skills (SDK)** (`plugin_sdk`).

---

## 1. Create a New Plugin Skeleton

Enable **CLI** and **SDK** in Advanced settings, then scaffold a plugin:

```powershell
akatikito plugin new my-channel --dir ./dev-plugins
```

This creates:
- `akatikito-plugin.json` — Channel API v1 manifest (`id`, `name`, `version`, `kind`, `api_version`, `entry`, `files`, `permissions`).
- `adapter.py` — implements `connect() -> str`, `poll(cursor, stopped) -> (cursor, list[Inbound])`, `send(event, text)`, `close()`.
- `test_plugin.py` — offline contract test for your adapter.
- `README.md` — plugin overview.

---

## 2. Security Contract (Channel API v1)

1. **Connectors never call LLMs directly** — they only normalize messages into `akatikito.channel_sdk.Inbound` and deliver replies from the Gateway.
2. **Validate outbound hosts** — never send bot tokens to arbitrary user-provided URLs.
3. **Read-only & approval first** — outward actions must obey owner allowlists and approvals.
4. **Unsigned dev plugins** load **only** from your explicit dev folder when `plugin_sdk` is enabled, and display a red **«μη υπογεγραμμένο»** badge.

---

## 3. Sign Your Plugin for Sharing

Generate a local signing key and sign your plugin folder:

```python
from akatikito.plugin_sdk import generate_signing_key, sign_plugin, verify_plugin_signature

key = generate_signing_key()
sign_plugin("dev-plugins/my-channel", key)
ok, detail = verify_plugin_signature("dev-plugins/my-channel", key)
```

Any single-byte change to any manifest-declared file after signing is automatically detected and rejected.
