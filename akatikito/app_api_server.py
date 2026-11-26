"""Local HTTP API server and request handler extracted from app_api.py."""
from __future__ import annotations

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from .api_security import MAX_BODY, endpoint_identity, request_allowed

if TYPE_CHECKING:
    from .app_api import AppState

APP_ROOT = Path(__file__).resolve().parents[1]
UI_FILE = APP_ROOT / "akatikito" / "ui.html"
UI_DIR = APP_ROOT / "akatikito" / "ui"


def _version() -> str:
    from . import __version__

    return __version__


def _post_connect(state: AppState, d: dict) -> dict:
    return state.connect(
        provider=d.get("provider", ""),
        base_url=d.get("base_url", ""),
        model=d.get("model", ""),
        api_key=d.get("api_key", ""),
        project=d.get("project", ""),
        max_steps=d.get("max_steps", 50),
        verify=d.get("verify", ""),
        confirm_key_destination=d.get("confirm_key_destination") is True,
    )


def _post_mcp_disconnect(state: AppState, d: dict) -> dict:
    if state.busy:
        return {"error": "Περίμενε να ολοκληρωθεί η εργασία."}
    state.mcp.disconnect(d.get("name", ""))
    return {"ok": True}


POST_ROUTES = {
    "/api/connect": _post_connect,
    "/api/plugins/action": lambda s, d: s.plugin_action(d),
    "/api/channel/session": lambda s, d: s.channel_session(d.get("id", ""), d.get("project", "")),
    "/api/send": lambda s, d: s.send(d.get("text", ""), d.get("attachments")),
    "/api/steer": lambda s, d: s.steer(d.get("text", "")),
    "/api/stop": lambda s, _d: s.stop(),
    "/api/poll": lambda s, d: s.poll(int(d.get("since", 0))),
    "/api/mode": lambda s, d: s.set_mode(d.get("mode", "")),
    "/api/model": lambda s, d: s.change_model(d.get("model", "")),
    "/api/models": lambda s, d: s.load_models(d.get("provider", ""), d.get("base_url", ""), d.get("api_key", "")),
    "/api/session/open": lambda s, d: s.open_session(d.get("id", ""), d.get("project", "")),
    "/api/session/new": lambda s, _d: s.new_session(),
    "/api/session/delete": lambda s, d: s.delete_session(d.get("id", ""), d.get("project", "")),
    "/api/session/pin": lambda s, d: s.pin_session(d.get("id", ""), d.get("pinned") is True, d.get("project", "")),
    "/api/sessions/search": lambda s, d: s.search_sessions(d.get("query", ""), d.get("limit", 20)),
    "/api/restore": lambda s, d: s.restore(d.get("backup", "")),
    "/api/pick-folder": lambda s, _d: s.pick_folder(),
    "/api/voice/config": lambda s, d: s.configure_voice(d),
    "/api/voice/prepare": lambda s, d: s.start_voice(d, True),
    "/api/voice/transcribe": lambda s, d: s.start_voice(d),
    "/api/voice/cancel": lambda s, d: {"ok": s.voice.cancel(d.get("id"))},
    "/api/mcp/connect": lambda s, d: s.mcp_connect(d.get("name", "")),
    "/api/mcp/disconnect": _post_mcp_disconnect,
    "/api/mcp/save": lambda s, d: s.mcp_save(d),
    "/api/mcp/import": lambda s, d: s.mcp_import(d),
    "/api/mcp/remove": lambda s, d: s.mcp_remove(d.get("name", "")),
    "/api/attachments/upload": lambda s, d: s.upload_attachment(d),
    "/api/attachments/screenshot": lambda s, d: s.capture_screenshot(d),
    "/api/reminder/done": lambda s, d: s.finish_reminder(d.get("id", "")),
    "/api/agenda/list": lambda s, _d: s.agenda_list(),
    "/api/agenda/add": lambda s, d: s.agenda_add(d),
    "/api/agenda/toggle": lambda s, d: s.agenda_toggle(d.get("id", ""), d.get("done", True)),
    "/api/agenda/delete": lambda s, d: s.agenda_delete(d.get("id", "")),
    "/api/scheduled/list": lambda s, _d: s.scheduled_list(),
    "/api/scheduled/action": lambda s, d: s.scheduled_action(d.get("id", ""), d.get("action", "")),
    "/api/scheduled/history": lambda s, d: s.scheduled_history(d.get("id", "")),
    "/api/diagnostics/health": lambda s, _d: s.diagnostics_health(),
    "/api/doctor/diagnose": lambda s, d: s.doctor_diagnose(d),
    "/api/doctor/fix": lambda s, d: s.doctor_fix(d),
    "/api/doctor/snapshot": lambda s, d: s.doctor_snapshot(d),
    "/api/diagnostics/export": lambda s, d: s.diagnostics_export(d),
    "/api/i18n": lambda s, d: s.i18n_bundle(d.get("lang")),
    "/api/update/check": lambda s, _d: s.github_update_check(),
    "/api/update/apply": lambda s, d: s.github_update_apply(d),
    "/api/project-profile": lambda s, d: s.project_profile(d),
    "/api/audit": lambda s, d: s.audit_view(d.get("limit", 60)),
    "/api/docs/search": lambda s, d: s.docs_search(d.get("query", ""), d.get("limit", 8)),
    "/api/routines/list": lambda s, _d: s.routines_list(),
    "/api/routines/add": lambda s, d: s.routines_add(d),
    "/api/routines/delete": lambda s, d: s.routines_delete(d.get("id", "")),
    "/api/project/switch": lambda s, d: s.switch_project(d.get("project", "")),
    "/api/files/open-os": lambda s, d: s.open_in_os(d.get("path", ""), d.get("reveal") is True),
    "/api/mobile-bridge/status": lambda s, _d: s.mobile_bridge_status(),
    "/api/mobile-bridge/start": lambda s, d: s.mobile_bridge_start(d.get("port", 0)),
    "/api/mobile-bridge/stop": lambda s, _d: s.mobile_bridge_stop(),
    "/api/approval": lambda s, d: s.answer_approval(d.get("id", ""), d.get("allow") is True, d.get("always") is True),
    "/api/learning": lambda s, d: s.learning_view(d.get("offset", 0)),
    "/api/learning/action": lambda s, d: s.learning_action(d),
    "/api/memory": lambda s, d: s.memory_view(d),
    "/api/memory/action": lambda s, d: s.memory_action(d),
    "/api/checkpoint/diff": lambda s, d: s.checkpoint_action(d.get("id", "")),
    "/api/checkpoint/undo": lambda s, d: s.checkpoint_action(d.get("id", ""), True),
    "/api/preferences": lambda s, d: s.set_preferences(d),
    "/api/files/pick": lambda s, _d: s.pick_file(),
    "/api/files/tree": lambda s, d: s.list_project_tree(
        d.get("path", "."), d.get("project"), d.get("offset", 0), d.get("limit", 100), d.get("version", "")
    ),
    "/api/files/plan": lambda s, d: s.files_organize("plan", d),
    "/api/files/apply": lambda s, d: s.files_organize("apply", d),
    "/api/files/undo-plan": lambda s, d: s.files_organize("undo_plan", d),
    "/api/files/undo": lambda s, d: s.files_organize("undo", d),
    "/api/files/history": lambda s, d: s.files_organize("history", d),
    "/api/files/open": lambda s, d: s.open_file(
        d.get("path", ""), d.get("offset", 0), d.get("version", ""), d.get("project"), d.get("session_id")
    ),
    "/api/files/diff": lambda s, d: s.file_diff(d.get("path", ""), d.get("backup_id", "")),
    "/api/upgrade/start": lambda s, _d: s.start_upgrade(),
    "/api/upgrade/check": lambda s, _d: s.check_upgrade(),
    "/api/upgrade/apply": lambda s, _d: s.apply_upgrade(),
    "/api/upgrade/cancel": lambda s, _d: s.cancel_upgrade(),
    "/api/usage/insights": lambda s, d: s.usage_insights(d.get("month", "")),
    "/api/copy": lambda s, d: s.copy_text(d.get("text", "")),
    "/api/paste": lambda s, d: s.paste_text(d),
    "/api/chat/export": lambda s, d: s.export_chat(d),
    "/api/profiles/action": lambda s, d: s.family_profile_action(d),
    "/api/email/action": lambda s, d: s.email_draft_action(d),
    "/api/background/stop": lambda s, _d: s.background_service_stop(),
    "/api/onboarding/detect-local": lambda s, _d: s.detect_local_ai(),
}


def make_handler(state: AppState) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # σιωπηλό logging
            pass

        def _send(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def _reject(self,payload,code):
            # Closing with unread body bytes can reset TCP before the client sees 403.
            # Discard only bounded raw input; rejected requests never reach JSON/actions.
            self.close_connection=True
            if self.command=="POST" and not getattr(self,'_body_consumed',False) and not self.headers.get('Expect'):
                try:length=max(0,min(65536,int(self.headers.get('Content-Length','0'))))
                except ValueError:length=0
                previous=self.connection.gettimeout()
                deadline=time.monotonic()+.25
                try:
                    while length>0:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:break
                        self.connection.settimeout(remaining)
                        data=self.rfile.read1(min(length,8192))
                        if not data:break
                        length-=len(data)
                except (OSError,ValueError):
                    pass
                finally:
                    self.connection.settimeout(previous)
            self._send(payload,code)

        def _authorized(self, api=True, post=False):
            if not request_allowed(self.headers, self.server.server_address[1], self.server.auth_token, api, post):
                self._reject({'error':'Η τοπική πρόσβαση απορρίφθηκε.'},403)
                return False
            if post:
                from . import advanced
                denial = advanced.route_denial(state.settings, self.path.split("?")[0])
                if denial:
                    self._reject({'error': denial}, 403)
                    return False
            return True

        def _body(self):
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length < 1 or length > MAX_BODY:
                    self._reject({'error':'Μη έγκυρο μέγεθος αιτήματος.'},413);return None
                if self.headers.get('Transfer-Encoding'):
                    self._reject({'error':'Δεν υποστηρίζεται transfer encoding.'},400);return None
                raw=self.rfile.read(length)
                self._body_consumed=True
                data = json.loads(raw.decode('utf-8'))
                if not isinstance(data,dict):raise ValueError('Expected object')
                return data
            except (ValueError,UnicodeError):
                self._send({'error':'Χρειάζεται έγκυρο JSON object.'},400)
                return None

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            _ui_assets = {
                '/ui/style.css': ('style.css', 'text/css; charset=utf-8'),
                '/ui/studio.css': ('studio.css', 'text/css; charset=utf-8'),
                '/ui/chat.js': ('chat.js', 'text/javascript; charset=utf-8'),
                '/ui/files.js': ('files.js', 'text/javascript; charset=utf-8'),
                '/ui/attachments.js': ('attachments.js', 'text/javascript; charset=utf-8'),
                '/ui/learning.js': ('learning.js', 'text/javascript; charset=utf-8'),
                '/ui/settings.js': ('settings.js', 'text/javascript; charset=utf-8'),
                '/ui/studio.js': ('studio.js', 'text/javascript; charset=utf-8'),
                '/ui/memory.js': ('memory.js', 'text/javascript; charset=utf-8'),
            }
            if not self._authorized(api=path not in ('/', '/onboarding.js', *_ui_assets)):return
            if path == "/":
                body = UI_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path in _ui_assets:
                fname, ctype = _ui_assets[path]
                body = (UI_DIR / fname).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers();self.wfile.write(body)
            elif path == '/onboarding.js':
                body=UI_FILE.with_name('onboarding.js').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/javascript; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers();self.wfile.write(body)
            elif path == "/api/status":
                self._send(state.status())
            elif path == "/api/providers":
                self._send(state.providers())
            elif path == "/api/plugins":
                self._send(state.plugins_view())
            elif path == "/api/sessions":
                self._send({"sessions": state.sessions()})
            elif path == "/api/backups":
                self._send({"backups": state.backups()})
            elif path == "/api/profiles":
                self._send(state.family_profiles_view())
            elif path == "/api/email/drafts":
                self._send(state.email_drafts_view())
            elif path == "/api/background/status":
                self._send(state.background_service_status())
            else:
                self._send({"error": "άγνωστη διαδρομή"}, 404)

        def do_OPTIONS(self):
            self._send({'error':'Cross-origin requests are not allowed.'},403)

        def do_POST(self) -> None:
            if not self._authorized(post=True):
                return
            path = self.path.split("?")[0]
            data = self._body()
            if data is None:
                return
            handler = POST_ROUTES.get(path)
            if handler is None:
                self._send({"error": "άγνωστη διαδρομή"}, 404)
                return
            self._send(handler(state, data))

    return Handler


def start_server(state: AppState) -> tuple[str, ThreadingHTTPServer]:
    """Ανοίγει τοπικό server σε τυχαία ελεύθερη πόρτα (μόνο localhost)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    server.auth_token = secrets.token_urlsafe(32)
    server.ui_url = url + '/#token=' + server.auth_token
    return url, server
