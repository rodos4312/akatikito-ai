"""Local HTTP API server and request handler extracted from app_api.py."""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api_security import MAX_BODY, endpoint_identity, request_allowed

APP_ROOT = Path(__file__).resolve().parents[1]
UI_FILE = APP_ROOT / "akatikito" / "ui.html"
UI_DIR = APP_ROOT / "akatikito" / "ui"


def _version() -> str:
    from . import __version__

    return __version__


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
            if request_allowed(self.headers, self.server.server_address[1], self.server.auth_token, api, post):
                return True
            self._reject({'error':'Η τοπική πρόσβαση απορρίφθηκε.'},403)
            return False

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
                '/ui/chat.js': ('chat.js', 'text/javascript; charset=utf-8'),
                '/ui/files.js': ('files.js', 'text/javascript; charset=utf-8'),
                '/ui/learning.js': ('learning.js', 'text/javascript; charset=utf-8'),
                '/ui/settings.js': ('settings.js', 'text/javascript; charset=utf-8'),
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
            else:
                self._send({"error": "άγνωστη διαδρομή"}, 404)

        def do_OPTIONS(self):
            self._send({'error':'Cross-origin requests are not allowed.'},403)

        def do_POST(self) -> None:
            if not self._authorized(post=True):return
            path = self.path.split("?")[0]
            data = self._body()
            if data is None:return
            if path == "/api/connect":
                self._send(state.connect(
                    provider=data.get("provider", ""),
                    base_url=data.get("base_url", ""),
                    model=data.get("model", ""),
                    api_key=data.get("api_key", ""),
                    project=data.get("project", ""),
                    max_steps=data.get("max_steps", 50),
                    verify=data.get("verify", ""),
                    confirm_key_destination=data.get('confirm_key_destination') is True,
                ))
            elif path == "/api/plugins/action":
                self._send(state.plugin_action(data))
            elif path == "/api/channel/session":
                self._send(state.channel_session(data.get("id",""),data.get("project","")))
            elif path == "/api/send":
                self._send(state.send(data.get("text", ""),data.get("attachments")))
            elif path == "/api/stop":
                self._send(state.stop())
            elif path == "/api/poll":
                self._send(state.poll(int(data.get("since", 0))))
            elif path == "/api/mode":
                self._send(state.set_mode(data.get("mode", "")))
            elif path == "/api/model":
                self._send(state.change_model(data.get("model", "")))
            elif path == "/api/models":
                self._send(state.load_models(
                    data.get("provider", ""), data.get("base_url", ""), data.get("api_key", "")
                ))
            elif path == "/api/session/open":
                self._send(state.open_session(data.get("id", ""), data.get("project", "")))
            elif path == "/api/session/new":
                self._send(state.new_session())
            elif path == "/api/session/delete":
                self._send(state.delete_session(data.get("id", ""), data.get("project", "")))
            elif path == "/api/restore":
                self._send(state.restore(data.get("backup", "")))
            elif path == "/api/pick-folder":
                self._send(state.pick_folder())
            elif path == '/api/voice/config':
                self._send(state.configure_voice(data))
            elif path == '/api/voice/prepare':
                self._send(state.start_voice(data,True))
            elif path == '/api/voice/transcribe':
                self._send(state.start_voice(data))
            elif path == '/api/voice/cancel':
                self._send({'ok':state.voice.cancel(data.get('id'))})
            elif path == '/api/mcp/connect':
                self._send(state.mcp_connect(data.get('name','')))
            elif path == '/api/mcp/disconnect':
                if state.busy:self._send({'error':'Περίμενε να ολοκληρωθεί η εργασία.'})
                else:state.mcp.disconnect(data.get('name',''));self._send({'ok':True})
            elif path == '/api/mcp/save':
                self._send(state.mcp_save(data))
            elif path == '/api/mcp/import':
                self._send(state.mcp_import(data))
            elif path == '/api/mcp/remove':
                self._send(state.mcp_remove(data.get('name','')))
            elif path == '/api/attachments/upload':
                self._send(state.upload_attachment(data))
            elif path == '/api/attachments/screenshot':
                self._send(state.capture_screenshot(data))
            elif path == '/api/reminder/done':
                self._send(state.finish_reminder(data.get('id','')))
            elif path == '/api/agenda/list':
                self._send(state.agenda_list())
            elif path == '/api/agenda/add':
                self._send(state.agenda_add(data))
            elif path == '/api/agenda/toggle':
                self._send(state.agenda_toggle(data.get('id',''), data.get('done',True)))
            elif path == '/api/agenda/delete':
                self._send(state.agenda_delete(data.get('id','')))
            elif path == '/api/scheduled/list':
                self._send(state.scheduled_list())
            elif path == '/api/scheduled/action':
                self._send(state.scheduled_action(data.get('id',''), data.get('action','')))
            elif path == '/api/scheduled/history':
                self._send(state.scheduled_history(data.get('id','')))
            elif path == '/api/diagnostics/health':
                self._send(state.diagnostics_health())
            elif path == '/api/doctor/diagnose':
                self._send(state.doctor_diagnose(data))
            elif path == '/api/doctor/fix':
                self._send(state.doctor_fix(data))
            elif path == '/api/diagnostics/export':
                self._send(state.diagnostics_export(data))
            elif path == '/api/i18n':
                self._send(state.i18n_bundle(data.get('lang')))
            elif path == '/api/update/check':
                self._send(state.github_update_check())
            elif path == '/api/update/apply':
                self._send(state.github_update_apply(data))
            elif path == '/api/project-profile':
                self._send(state.project_profile(data))
            elif path == '/api/audit':
                self._send(state.audit_view(data.get('limit', 60)))
            elif path == '/api/docs/search':
                self._send(state.docs_search(data.get('query', ''), data.get('limit', 8)))
            elif path == '/api/routines/list':
                self._send(state.routines_list())
            elif path == '/api/routines/add':
                self._send(state.routines_add(data))
            elif path == '/api/routines/delete':
                self._send(state.routines_delete(data.get('id', '')))
            elif path == '/api/project/switch':
                self._send(state.switch_project(data.get('project', '')))
            elif path == '/api/files/open-os':
                self._send(state.open_in_os(data.get('path', ''), data.get('reveal') is True))
            elif path == '/api/mobile-bridge/status':
                self._send(state.mobile_bridge_status())
            elif path == '/api/mobile-bridge/start':
                self._send(state.mobile_bridge_start(data.get('port', 0)))
            elif path == '/api/mobile-bridge/stop':
                self._send(state.mobile_bridge_stop())
            elif path == '/api/approval':
                self._send(state.answer_approval(data.get('id',''),data.get('allow') is True,data.get('always') is True))
            elif path == '/api/learning':
                self._send(state.learning_view(data.get('offset',0)))
            elif path == '/api/learning/action':
                self._send(state.learning_action(data))
            elif path == '/api/checkpoint/diff':
                self._send(state.checkpoint_action(data.get('id','')))
            elif path == '/api/checkpoint/undo':
                self._send(state.checkpoint_action(data.get('id',''),True))
            elif path == '/api/preferences':
                self._send(state.set_preferences(data))
            elif path == '/api/files/pick':
                self._send(state.pick_file())
            elif path == '/api/files/tree':
                self._send(state.list_project_tree(
                    data.get('path','.'), data.get('project'),
                    data.get('offset',0), data.get('limit',100), data.get('version',''),
                ))
            elif path == '/api/files/plan':
                self._send(state.files_organize('plan', data))
            elif path == '/api/files/apply':
                self._send(state.files_organize('apply', data))
            elif path == '/api/files/undo-plan':
                self._send(state.files_organize('undo_plan', data))
            elif path == '/api/files/undo':
                self._send(state.files_organize('undo', data))
            elif path == '/api/files/history':
                self._send(state.files_organize('history', data))
            elif path == '/api/files/open':
                self._send(state.open_file(data.get('path',''), data.get('offset',0), data.get('version',''),
                                          data.get('project'), data.get('session_id')))
            elif path == "/api/upgrade/start":
                self._send(state.start_upgrade())
            elif path == "/api/upgrade/check":
                self._send(state.check_upgrade())
            elif path == "/api/upgrade/apply":
                self._send(state.apply_upgrade())
            elif path == "/api/upgrade/cancel":
                self._send(state.cancel_upgrade())
            elif path == "/api/copy":
                self._send(state.copy_text(data.get("text", "")))
            elif path == "/api/paste":
                self._send(state.paste_text())
            elif path == "/api/chat/export":
                self._send(state.export_chat(data))
            else:
                self._send({"error": "άγνωστη διαδρομή"}, 404)

    return Handler


def start_server(state: AppState) -> tuple[str, ThreadingHTTPServer]:
    """Ανοίγει τοπικό server σε τυχαία ελεύθερη πόρτα (μόνο localhost)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    server.auth_token = secrets.token_urlsafe(32)
    server.ui_url = url + '/#token=' + server.auth_token
    return url, server
