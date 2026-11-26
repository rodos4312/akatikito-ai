"""Editor Agent Client Protocol (ACP) JSON-RPC 2.0 stdio server (PRO-PLAN D3).

Talks to the SAME local Akatikito API (never a second agent), enforces the Advanced `acp`
feature gate, accepts open-file + selection context, and previews edits as unified diffs.
"""
from __future__ import annotations

import difflib
import json
import sys
import uuid
from pathlib import Path
from typing import Callable, TextIO

from . import advanced
from .cli import call_local_api
from .settings import DEFAULT_PATH, Settings

ACP_PROTOCOL_VERSION = "0.1"


def _jsonrpc_ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _compose_editor_prompt(prompt: str, active_file: str = "", selection: str = "") -> str:
    parts: list[str] = []
    if active_file:
        parts.append(f"[Ανοιχτό αρχείο editor: {active_file}]")
    if selection:
        parts.append(f"[Επιλεγμένο κείμενο]\n{selection[:20_000]}")
    parts.append(str(prompt or "").strip())
    return "\n\n".join(p for p in parts if p)


class AcpServer:
    """Lightweight stdio JSON-RPC 2.0 ACP bridge to the local Akatikito API."""

    def __init__(
        self,
        settings: Settings,
        *,
        api_url: str = "",
        api_token: str = "",
        sender: Callable[[str, str, dict], dict] | None = None,
    ) -> None:
        self.settings = settings
        self.api_url = api_url
        self.api_token = api_token
        self.sender = sender
        self._sessions: dict[str, dict] = {}

    def _require_enabled(self) -> str | None:
        if not advanced.enabled(self.settings, "acp"):
            return "Η λειτουργία «Σύνδεση με editor (ACP)» είναι κλειστή στο Ρυθμίσεις → Advanced."
        return None

    def _handle_initialize(self, req_id, _params: dict) -> dict:
        return _jsonrpc_ok(
            req_id,
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "serverInfo": {"name": "akatikito-acp", "version": "2.5.0"},
                "capabilities": {"diffPreview": True, "contextSelection": True, "localApiOnly": True},
            },
        )

    def _handle_session_new(self, req_id, params: dict) -> dict:
        root = str(params.get("workspace") or self.settings.project or "").strip()
        sid = f"acp-{uuid.uuid4().hex[:12]}"
        self._sessions[sid] = {"workspace": root}
        return _jsonrpc_ok(req_id, {"sessionId": sid, "workspace": root})

    def _handle_session_prompt(self, req_id, params: dict) -> dict:
        sid = str(params.get("sessionId") or "")
        if sid not in self._sessions:
            return _jsonrpc_err(req_id, -32602, "Άγνωστο ACP sessionId.")
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            return _jsonrpc_err(req_id, -32602, "Κενό prompt.")
        composed = _compose_editor_prompt(
            prompt,
            active_file=str(params.get("activeFile") or ""),
            selection=str(params.get("selection") or ""),
        )
        payload = {"text": composed, "mode": str(params.get("mode") or "ask"), "acp_session": sid}
        if self.sender is not None:
            reply = self.sender("/api/send", self.api_token, payload)
        else:
            if not self.api_url:
                return _jsonrpc_err(req_id, -32002, "Δεν δόθηκε τοπικό API URL για το Akatikito.")
            res = call_local_api(self.api_url, self.api_token, "/api/send", payload)
            reply = res.get("data") or {}
        return _jsonrpc_ok(req_id, {"sessionId": sid, "response": reply})

    def _handle_diff_preview(self, req_id, params: dict) -> dict:
        sid = str(params.get("sessionId") or "")
        session = self._sessions.get(sid)
        if session is None:
            return _jsonrpc_err(req_id, -32602, "Άγνωστο ACP sessionId.")
        rel = str(params.get("path") or "").strip()
        proposed = str(params.get("proposedContent") or "")
        workspace = Path(session["workspace"]).expanduser().resolve() if session.get("workspace") else Path.cwd().resolve()
        target = (workspace / rel).resolve()
        if not rel or not target.is_relative_to(workspace):
            return _jsonrpc_err(req_id, -32602, "Η διαδρομή αρχείου πρέπει να βρίσκεται μέσα στο workspace.")
        original = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
        return _jsonrpc_ok(req_id, {"path": rel, "applied": False, "changed": original != proposed, "diff": diff})

    def handle_request(self, request: dict) -> dict:
        req_id = request.get("id")
        err = self._require_enabled()
        if err:
            return _jsonrpc_err(req_id, -32003, err)
        method = str(request.get("method") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        handlers = {
            "initialize": self._handle_initialize,
            "session/new": self._handle_session_new,
            "session/prompt": self._handle_session_prompt,
            "diff/preview": self._handle_diff_preview,
        }
        handler = handlers.get(method)
        if handler is None:
            return _jsonrpc_err(req_id, -32601, f"Άγνωστη μέθοδος ACP: {method}")
        return handler(req_id, params)

    def serve_stream(self, in_stream: TextIO, out_stream: TextIO) -> int:
        processed = 0
        for raw in in_stream:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                out_stream.write(json.dumps(_jsonrpc_err(None, -32700, "Μη έγκυρο JSON."), ensure_ascii=False) + "\n")
                out_stream.flush()
                continue
            resp = self.handle_request(req if isinstance(req, dict) else {})
            out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out_stream.flush()
            processed += 1
        return processed


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Akatikito Editor ACP stdio server")
    parser.add_argument("--settings", default="", help="Path to settings.json")
    parser.add_argument("--api-url", default="", help="Local Akatikito API URL")
    parser.add_argument("--token", default="", help="Local Akatikito API token")
    args = parser.parse_args(argv)
    cfg = Settings.load(Path(args.settings) if args.settings else DEFAULT_PATH)
    if not advanced.enabled(cfg, "acp"):
        print("Η λειτουργία «Σύνδεση με editor (ACP)» είναι κλειστή στο Ρυθμίσεις → Advanced.", file=sys.stderr)
        return 2
    server = AcpServer(cfg, api_url=args.api_url, api_token=args.token)
    server.serve_stream(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
