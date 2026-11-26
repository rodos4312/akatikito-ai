# Connecting Editors to Akatikito via ACP (`docs/ACP.md`)

Akatikito provides a local JSON-RPC 2.0 stdio **Agent Client Protocol (ACP)** bridge (`akatikito/acp_server.py`) that talks exclusively to your running local Akatikito API (`127.0.0.1`).

> **Disabled by default:** Enable **Ρυθμίσεις → Advanced → Σύνδεση με editor (ACP)** (`acp`) first. When disabled, `Akatikito.cmd acp` refuses to start with exit code 2.

---

## Launch Command

- **Windows:** `Akatikito.cmd acp --api-url http://127.0.0.1:<PORT> --token <TOKEN>`
- **Cross-platform Python:** `python -m akatikito.acp_server --api-url http://127.0.0.1:<PORT> --token <TOKEN>`

---

## Zed Configuration (`settings.json`)

```json
{
  "agent_servers": {
    "Akatikito": {
      "command": "Akatikito.cmd",
      "args": ["acp", "--api-url", "http://127.0.0.1:8765", "--token", "<YOUR_LOCAL_TOKEN>"]
    }
  }
}
```

## VS Code (ACP Extension)

Point your ACP client extension command to `Akatikito.cmd` with args `["acp", "--api-url", "http://127.0.0.1:8765", "--token", "<YOUR_LOCAL_TOKEN>"]`.

## Supported JSON-RPC Methods

- `initialize` — returns server info and capabilities (`diffPreview`, `contextSelection`, `localApiOnly`).
- `session/new` — opens an editor session scoped to a workspace directory.
- `session/prompt` — forwards user prompt + `activeFile` + `selection` to the local Akatikito API.
- `diff/preview` — returns a read-only unified diff before applying any change to disk.
