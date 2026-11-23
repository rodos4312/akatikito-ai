"""Opt-in Mobile Companion Pairing & LAN Bridge for Akatikito PC (clean-room).

Disabled by default. When explicitly enabled by the desktop owner, binds a local
LAN HTTP server protected by a high-entropy bearer token and a 6-digit pairing PIN,
allowing Akatikito-Mobile on Android/iOS to securely log into the PC core over Wi-Fi
or Tailscale without exposing API keys or opening router ports.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _detect_lan_ip() -> str:
    """Return the primary non-loopback LAN IPv4 address (or 127.0.0.1 if offline)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip and not host_ip.startswith("127."):
            return host_ip
    except OSError:
        pass
    return "127.0.0.1"


class MobileBridge:
    """Manages an opt-in LAN HTTP bridge for Akatikito-Mobile."""

    def __init__(self, state):
        self.state = state
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.pin: str = ""
        self.token: str = ""
        self.port: int = 0
        self.lan_ip: str = ""
        self.started_at: float = 0.0
        self._sessions: dict[str, float] = {}  # session_token -> created_at
        self._failed_attempts: int = 0

    @property
    def active(self) -> bool:
        return self._server is not None

    def status(self) -> dict:
        with self._lock:
            if not self._server:
                return {
                    "active": False,
                    "url": "",
                    "lan_ip": "",
                    "port": 0,
                    "pin": "",
                    "pairing_uri": "",
                    "paired_devices": 0,
                }
            url = f"http://{self.lan_ip}:{self.port}"
            qs = urllib.parse.urlencode({"url": url, "pin": self.pin, "token": self.token})
            return {
                "active": True,
                "url": url,
                "lan_ip": self.lan_ip,
                "port": self.port,
                "pin": self.pin,
                "token": self.token,
                "pairing_uri": f"akatikito://pair?{qs}",
                "paired_devices": len(self._sessions),
            }

    def start(self, port: int = 0, host: str = "0.0.0.0") -> dict:
        with self._lock:
            if self._server is not None:
                return self.status()
            self.pin = f"{secrets.randbelow(900000) + 100000}"
            self.token = secrets.token_urlsafe(24)
            self.lan_ip = "127.0.0.1" if host == "127.0.0.1" else _detect_lan_ip()
            self._sessions.clear()
            self._failed_attempts = 0
            handler = self._make_handler()
            server = ThreadingHTTPServer((host, int(port or 0)), handler)
            self.port = int(server.server_address[1])
            self._server = server
            self.started_at = time.time()
            self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="akatikito-mobile-bridge")
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            srv = self._server
            self._server = None
            self.pin = ""
            self.token = ""
            self.port = 0
            self._sessions.clear()
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except OSError:
                pass
        return self.status()

    def _authenticate_login(self, pin: str, token: str) -> tuple[bool, str]:
        with self._lock:
            if not self._server:
                return False, "Η σύνδεση κινητού είναι απενεργοποιημένη."
            if self._failed_attempts >= 10:
                return False, "Πάρα πολλές αποτυχημένες προσπάθειες. Κάνε επανεκκίνηση της σύνδεσης κινητού από το PC."
            ok_pin = secrets.compare_digest(str(pin or "").strip(), self.pin)
            ok_tok = secrets.compare_digest(str(token or "").strip(), self.token)
            if not (ok_pin and ok_tok):
                self._failed_attempts += 1
                return False, "Λάθος PIN ή κωδικός σύζευξης."
            self._failed_attempts = 0
            sess = secrets.token_urlsafe(32)
            self._sessions[sess] = time.time()
            # Keep at most 10 paired sessions
            if len(self._sessions) > 10:
                oldest = min(self._sessions, key=self._sessions.get)
                self._sessions.pop(oldest, None)
            return True, sess

    def _is_session_valid(self, header_token: str) -> bool:
        tok = str(header_token or "").strip()
        if not tok:
            return False
        with self._lock:
            for s in list(self._sessions.keys()):
                if secrets.compare_digest(tok, s):
                    return True
        return False

    def _make_handler(self) -> type:
        bridge = self
        state = self.state

        class MobileHandler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _send(self, payload: dict, code: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict | None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 10 * 1024 * 1024:
                        self._send({"error": "Invalid request size"}, 413)
                        return None
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    data = json.loads(raw.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("Expected JSON object")
                    return data
                except (ValueError, UnicodeError):
                    self._send({"error": "Invalid JSON body"}, 400)
                    return None

            def _check_auth(self) -> bool:
                tok = self.headers.get("X-Akatikito-Mobile-Token", "")
                if not tok:
                    auth = self.headers.get("Authorization", "")
                    if auth.lower().startswith("bearer "):
                        tok = auth[7:].strip()
                if bridge._is_session_valid(tok):
                    return True
                self._send({"error": "Μη εξουσιοδοτημένη πρόσβαση. Κάνε πρώτα Login."}, 401)
                return False

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path == "/api/mobile/ping":
                    self._send({"ok": True, "app": "Akatikito", "bridge": True})
                    return
                if not self._check_auth():
                    return
                if path == "/api/mobile/status":
                    st = state.status()
                    # Never leak internal paths or sensitive data beyond needed status
                    self._send(st)
                elif path == "/api/mobile/sessions":
                    self._send({"sessions": state.sessions()})
                else:
                    self._send({"error": "Not found"}, 404)

            def do_POST(self) -> None:
                path = self.path.split("?")[0]
                data = self._read_json()
                if data is None:
                    return
                if path == "/api/mobile/login":
                    ok, res = bridge._authenticate_login(data.get("pin", ""), data.get("token", ""))
                    if not ok:
                        self._send({"ok": False, "error": res}, 403)
                        return
                    st = state.status()
                    self._send({
                        "ok": True,
                        "session_token": res,
                        "project": st.get("project", ""),
                        "model": st.get("model", ""),
                        "provider": st.get("provider", ""),
                        "version": st.get("version", ""),
                    })
                    return
                if not self._check_auth():
                    return
                if path == "/api/mobile/send":
                    self._send(state.send(data.get("text", ""), data.get("attachments")))
                elif path == "/api/mobile/stop":
                    self._send(state.stop())
                elif path == "/api/mobile/poll":
                    self._send(state.poll(int(data.get("since", 0))))
                elif path == "/api/mobile/mode":
                    self._send(state.set_mode(data.get("mode", "")))
                elif path == "/api/mobile/session/new":
                    self._send(state.new_session())
                elif path == "/api/mobile/session/open":
                    self._send(state.open_session(data.get("id", ""), data.get("project", "")))
                elif path == "/api/mobile/routines":
                    self._send(state.routines_list())
                elif path == "/api/mobile/docs/search":
                    self._send(state.docs_search(data.get("query", ""), data.get("limit", 8)))
                elif path == "/api/mobile/approval":
                    self._send(state.answer_approval(data.get("id", ""), data.get("allow") is True, data.get("always") is True))
                elif path == "/api/mobile/files/tree":
                    self._send(state.list_project_tree(data.get("path", "."), data.get("project"), data.get("offset", 0), data.get("limit", 100), ""))
                elif path == "/api/mobile/files/open":
                    self._send(state.open_file(data.get("path", ""), data.get("offset", 0), "", data.get("project"), data.get("session_id")))
                else:
                    self._send({"error": "Not found"}, 404)

        return MobileHandler
