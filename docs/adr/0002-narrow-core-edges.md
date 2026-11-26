# ADR 0002 — Narrow Core, Capabilities at the Edges & 3-Door Advanced Gate

- **Status:** Accepted
- **Context:** Every tool registered in `TOOL_SPECS` is serialized into every LLM request, increasing token cost and degrading tool-selection accuracy. High-risk capabilities (`self_update`, `subtasks`, `git`) should never be silently exposed to models or UI users who did not opt in.
- **Decision:**
  1. New domain workflows belong in Skills (`skills/*.md`) or Tool Plugins (`akatikito/tool_plugins.py`), not in core `TOOL_SPECS`.
  2. Read-only `ask` mode dynamically narrows advertised tools (`modes.select_ask_tools`) to save ~66% prompt payload.
  3. High-risk features pass through a 3-door gate (`akatikito/advanced.py`): disabled by default in UI controls, HTTP API routes (returning HTTP 403), and model tool definitions simultaneously.
- **Consequences:** Lower per-turn token overhead and defense-in-depth against accidental invocation of privileged operations.
