"""Opt-in Mobile Companion Pairing & LAN Bridge for Akatikito PC (clean-room).

Disabled by default. When explicitly enabled by the desktop owner, binds a local
LAN HTTP server protected by a high-entropy bearer token and a 6-digit pairing PIN,
allowing Akatikito-Mobile on Android/iOS to securely log into the PC core over Wi-Fi
or Tailscale without exposing API keys or opening router ports.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_MOBILE_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MiB (supports APKs, ZIPs, videos, docs)
STREAM_CHUNK_BYTES = 64 * 1024
DEFAULT_MOBILE_PORT = 18765
SESSION_TTL_SECONDS = 30 * 86400  # 30 days persistent pairing


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def resolve_download_file(root: Path, rel_path: str) -> tuple[Path, str]:
    """Resolve and validate a file strictly inside the active project workspace."""
    from .tools import Workspace

    clean = str(rel_path or "").strip().replace("\\", "/")
    if not clean or clean in (".", ".."):
        raise ValueError("Διάλεξε ένα έγκυρο αρχείο για λήψη.")
    ws = Workspace(root)
    target = ws.resolve(clean)
    if not target.exists() or not target.is_file() or target.is_symlink():
        raise ValueError("Το αρχείο δεν βρέθηκε ή δεν είναι κανονικό αρχείο.")
    rel_posix = ws.relative(target).replace("\\", "/")
    return target, rel_posix


def save_upload_stream(root: Path, rel_path: str, stream, content_length: int) -> dict:
    """Safely stream an uploaded file from mobile into the active PC workspace."""
    from .tools import Workspace

    if content_length <= 0 or content_length > MAX_MOBILE_UPLOAD_BYTES:
        raise ValueError(f"Το αρχείο πρέπει να είναι 1 byte έως {MAX_MOBILE_UPLOAD_BYTES // (1024 * 1024)} MB.")
    raw = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise ValueError("Μη έγκυρη διαδρομή αρχείου.")
    safe_rel = "/".join(parts)
    ws = Workspace(root)
    target = ws.resolve(safe_rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{secrets.token_hex(4)}.part"
    written = 0
    try:
        with tmp.open("wb") as out:
            remaining = content_length
            while remaining > 0:
                chunk = stream.read(min(STREAM_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
        if written != content_length:
            raise ValueError(f"Ατελής μεταφορά αρχείου ({written}/{content_length} bytes). Το αρχείο προορισμού δεν αλλοιώθηκε.")
        if target.is_file():
            ws.backup(target)
        tmp.replace(target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return {"ok": True, "path": ws.relative(target).replace("\\", "/"), "size": written}


def _detect_tailscale_ip() -> str:
    """Detect local Tailscale IPv4 (100.x.y.z) if Tailscale is installed & connected."""
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.check_output(
            [exe, "ip", "-4"],
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).strip()
        for line in out.splitlines():
            ip = line.strip()
            if ip.startswith("100.") and ip.count(".") == 3:
                return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            parts = ip.split(".")
            if len(parts) == 4 and parts[0] == "100" and 64 <= int(parts[1]) <= 127:
                return ip
    except Exception:
        pass
    return ""


def _qr_svg_data_uri(payload: str) -> str:
    """Render an offline SVG QR code data URI for the pairing payload."""
    try:
        import segno
        buf = io.BytesIO()
        segno.make(payload, error="m").save(buf, kind="svg", scale=4, border=2, dark="#0f172a", light="#ffffff")
        return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


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
    """Manages an opt-in LAN/Tailscale HTTP bridge for Akatikito-Mobile with persistent pairing."""

    def __init__(self, state, store_path: Path | None = None):
        self.state = state
        self.store_path: Path | None = Path(store_path) if store_path else None
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.pin: str = ""
        self.token: str = ""
        self.port: int = 0
        self.lan_ip: str = ""
        self.tailscale_ip: str = ""
        self.started_at: float = 0.0
        self._sessions: dict[str, float] = {}  # session_token -> created_at
        self._auto_start: bool = False
        self._failed_attempts: int = 0
        self._load_persisted()

    def _load_persisted(self) -> None:
        if not self.store_path or not self.store_path.is_file():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self._auto_start = bool(data.get("auto_start", False))
            now = time.time()
            raw_sess = data.get("sessions") or {}
            if isinstance(raw_sess, dict):
                self._sessions = {
                    str(k): float(v)
                    for k, v in raw_sess.items()
                    if isinstance(k, str)
                    and len(k) == 64
                    and all(c in "0123456789abcdef" for c in k)
                    and (now - float(v)) < SESSION_TTL_SECONDS
                }
        except Exception:
            pass

    def _save_persisted(self) -> None:
        if not self.store_path:
            return
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "auto_start": self._auto_start,
                "port": self.port or DEFAULT_MOBILE_PORT,
                "sessions": self._sessions,
            }
            tmp = self.store_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.store_path)
        except OSError:
            pass

    def maybe_autostart(self) -> None:
        """Resume the bridge automatically if the user left it enabled before restarting PC."""
        if os.environ.get("AKATIKITO_TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if not self._auto_start:
            return
        try:
            self.start(port=0)
        except Exception:
            pass

    @property
    def active(self) -> bool:
        return self._server is not None

    def status(self) -> dict:
        with self._lock:
            if not self._server:
                return {
                    "active": False,
                    "url": "",
                    "lan_url": "",
                    "lan_ip": "",
                    "port": 0,
                    "pin": "",
                    "pairing_uri": "",
                    "paired_devices": len(self._sessions),
                }
            primary_ip = self.tailscale_ip or self.lan_ip
            url = f"http://{primary_ip}:{self.port}"
            lan_url = f"http://{self.lan_ip}:{self.port}"
            params = {"url": url, "pin": self.pin, "token": self.token}
            if lan_url != url:
                params["lan_url"] = lan_url
            qs = urllib.parse.urlencode(params)
            pairing_uri = f"akatikito://pair?{qs}"
            return {
                "active": True,
                "url": url,
                "lan_url": lan_url,
                "lan_ip": self.lan_ip,
                "tailscale_ip": self.tailscale_ip,
                "via_tailscale": bool(self.tailscale_ip),
                "port": self.port,
                "pin": self.pin,
                "token": self.token,
                "pairing_uri": pairing_uri,
                "qr_data_uri": _qr_svg_data_uri(pairing_uri),
                "paired_devices": len(self._sessions),
            }

    def start(self, port: int = 0, host: str = "0.0.0.0") -> dict:
        with self._lock:
            if self._server is not None:
                return self.status()
            self.pin = f"{secrets.randbelow(900000) + 100000}"
            self.token = secrets.token_urlsafe(24)
            self.lan_ip = "127.0.0.1" if host == "127.0.0.1" else _detect_lan_ip()
            self.tailscale_ip = "" if host == "127.0.0.1" else _detect_tailscale_ip()
            self._failed_attempts = 0
            handler = self._make_handler()
            req_port = int(port or 0)
            if req_port == 0 and host != "127.0.0.1":
                try:
                    server = _ReusableThreadingHTTPServer((host, DEFAULT_MOBILE_PORT), handler)
                except OSError:
                    server = _ReusableThreadingHTTPServer((host, 0), handler)
            else:
                server = _ReusableThreadingHTTPServer((host, req_port), handler)
            self.port = int(server.server_address[1])
            self._server = server
            self.started_at = time.time()
            self._auto_start = True
            self._save_persisted()
            self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="akatikito-mobile-bridge")
            self._thread.start()
            return self.status()

    def stop(self, user_initiated: bool = False) -> dict:
        with self._lock:
            srv = self._server
            self._server = None
            self.pin = ""
            self.token = ""
            self.port = 0
            if user_initiated:
                self._auto_start = False
                self._sessions.clear()
                self._save_persisted()
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
            sess_hash = hashlib.sha256(sess.encode("utf-8")).hexdigest()
            self._sessions[sess_hash] = time.time()
            # Keep at most 10 paired sessions
            if len(self._sessions) > 10:
                oldest = min(self._sessions, key=self._sessions.get)
                self._sessions.pop(oldest, None)
            self._save_persisted()
            return True, sess

    def _is_session_valid(self, header_token: str) -> bool:
        tok = str(header_token or "").strip()
        if not tok:
            return False
        tok_hash = hashlib.sha256(tok.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            for stored_hash, created in list(self._sessions.items()):
                if secrets.compare_digest(tok_hash, stored_hash):
                    if (now - created) >= SESSION_TTL_SECONDS:
                        self._sessions.pop(stored_hash, None)
                        self._save_persisted()
                        return False
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
                elif path == "/api/mobile/projects":
                    cur = str(state._files_root())
                    recent = list(getattr(getattr(state, "settings", None), "recent_projects", []) or [])
                    if cur and cur not in recent:
                        recent.insert(0, cur)
                    self._send({"current": cur, "recent": recent})
                elif path == "/api/mobile/files/download":
                    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    rel = (qs.get("path") or [""])[0]
                    try:
                        target, rel_posix = resolve_download_file(state._files_root(), rel)
                        size = target.stat().st_size
                        fname = urllib.parse.quote(target.name)
                    except (OSError, ValueError, PermissionError) as exc:
                        self._send({"error": str(exc)}, 400)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")
                    self.send_header("X-File-Path", urllib.parse.quote(rel_posix))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        with target.open("rb") as fh:
                            while True:
                                chunk = fh.read(STREAM_CHUNK_BYTES)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                    except OSError:
                        pass
                else:
                    self._send({"error": "Not found"}, 404)

            def _handle_upload(self) -> None:
                if not self._check_auth():
                    return
                raw_len = self.headers.get("Content-Length")
                if raw_len is None:
                    self._send({"error": "Missing Content-Length header"}, 400)
                    return
                try:
                    length = int(raw_len)
                except ValueError:
                    self._send({"error": "Invalid Content-Length"}, 400)
                    return
                if length <= 0:
                    self._send({"error": "Empty file upload is not allowed"}, 400)
                    return
                rel_hdr = urllib.parse.unquote(self.headers.get("X-File-Path", ""))
                if not rel_hdr:
                    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    rel_hdr = (qs.get("path") or [""])[0]
                try:
                    res = save_upload_stream(state._files_root(), rel_hdr, self.rfile, length)
                    self._send(res)
                except (OSError, ValueError, PermissionError) as exc:
                    self._send({"error": str(exc)}, 400)

            def _handle_login(self, data: dict) -> None:
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

            def do_POST(self) -> None:
                path = self.path.split("?")[0]
                if path == "/api/mobile/files/upload":
                    self._handle_upload()
                    return
                data = self._read_json()
                if data is None:
                    return
                if path == "/api/mobile/login":
                    self._handle_login(data)
                    return
                if not self._check_auth():
                    return
                handler = _MOBILE_POST_ROUTES.get(path)
                if handler is None:
                    self._send({"error": "Not found"}, 404)
                    return
                payload, code = handler(state, data)
                self._send(payload, code)

        return MobileHandler


def _mobile_poll(state, data: dict) -> tuple[dict, int]:
    try:
        since_val = int(data.get("since", 0))
    except (TypeError, ValueError):
        return {"error": "Invalid since parameter"}, 400
    return state.poll(since_val), 200


def _mobile_preferences(state, data: dict) -> tuple[dict, int]:
    # Strict allowlist: mobile may ONLY adjust model routing, NEVER security/budget/autonomous flags
    allowed_keys = {"model", "auto_mode", "fast_provider", "fast_model", "smart_provider", "smart_model"}
    filtered = {k: v for k, v in data.items() if k in allowed_keys}
    if not filtered:
        return {"error": "Δεν επιτρέπεται αλλαγή αυτής της ρύθμισης από το κινητό."}, 403
    return state.set_preferences(filtered), 200


def _mobile_project_switch(state, data: dict) -> tuple[dict, int]:
    # Security: mobile can ONLY switch to folders already approved in desktop recent_projects
    requested = str(data.get("project", "") or "").strip()
    recent = list(getattr(getattr(state, "settings", None), "recent_projects", []) or [])
    cur = str(state._files_root())
    allowed_paths = {str(Path(p).expanduser().resolve()).casefold() for p in ([cur] + recent) if p}
    try:
        req_resolved = str(Path(requested).expanduser().resolve()).casefold() if requested else ""
    except OSError:
        req_resolved = ""
    if not req_resolved or req_resolved not in allowed_paths:
        return {"error": "Επιτρέπεται εναλλαγή μόνο σε πρόσφατα εγκεκριμένα projects του PC."}, 403
    return state.switch_project(requested), 200


_MOBILE_POST_ROUTES = {
    "/api/mobile/send": lambda s, d: (s.send(d.get("text", ""), d.get("attachments")), 200),
    "/api/mobile/attachments/upload": lambda s, d: (s.upload_attachment(d), 200),
    "/api/mobile/stop": lambda s, _d: (s.stop(), 200),
    "/api/mobile/poll": _mobile_poll,
    "/api/mobile/mode": lambda s, d: (s.set_mode(d.get("mode", "")), 200),
    "/api/mobile/preferences": _mobile_preferences,
    "/api/mobile/session/new": lambda s, _d: (s.new_session(), 200),
    "/api/mobile/session/open": lambda s, d: (s.open_session(d.get("id", ""), d.get("project", "")), 200),
    "/api/mobile/session/delete": lambda s, d: (s.delete_session(d.get("id", ""), d.get("project", "")), 200),
    "/api/mobile/routines": lambda s, _d: (s.routines_list(), 200),
    "/api/mobile/agenda/list": lambda s, _d: (s.agenda_list(), 200),
    "/api/mobile/agenda/add": lambda s, d: (s.agenda_add(d), 200),
    "/api/mobile/agenda/toggle": lambda s, d: (s.agenda_toggle(d.get("id", ""), d.get("done", True)), 200),
    "/api/mobile/agenda/delete": lambda s, d: (s.agenda_delete(d.get("id", "")), 200),
    "/api/mobile/learning": lambda s, d: (s.learning_view(d.get("offset", 0)), 200),
    "/api/mobile/learning/action": lambda s, d: (s.learning_action(d), 200),
    "/api/mobile/docs/search": lambda s, d: (s.docs_search(d.get("query", ""), d.get("limit", 8)), 200),
    "/api/mobile/approval": lambda s, d: (s.answer_approval(d.get("id", ""), d.get("allow") is True, d.get("always") is True), 200),
    "/api/mobile/files/tree": lambda s, d: (s.list_project_tree(d.get("path", "."), d.get("project"), d.get("offset", 0), d.get("limit", 100), ""), 200),
    "/api/mobile/files/open": lambda s, d: (s.open_file(d.get("path", ""), d.get("offset", 0), "", d.get("project"), d.get("session_id")), 200),
    "/api/mobile/projects/switch": _mobile_project_switch,
}
