"""Τοπικό HTTP API για το παράθυρο της εφαρμογής (pywebview).

Δεν ανοίγει browser: ο server ακούει μόνο σε 127.0.0.1 σε τυχαία πόρτα
και το παράθυρο (pywebview) φορτώνει τη σελίδα από εκεί.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
import secrets
from .api_security import request_allowed, endpoint_identity, MAX_BODY
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import memory as global_memory
from . import modes
from .config import Config
from .model import PROVIDER_HINTS, PROVIDER_MODELS, PROVIDERS, list_models
from .sessions import SessionStore
from .settings import Settings, short_key
from .workspace import detect_verify
from .app_api_ops import OpsStateMixin
from .app_api_files import FilesStateMixin

APP_ROOT = Path(__file__).resolve().parents[1]
UI_FILE = APP_ROOT / "akatikito" / "ui.html"


def requests_self_upgrade(text: str, current_project: str = "") -> bool:
    from .intent import self_upgrade
    own_project = bool(current_project and Path(current_project).resolve() == APP_ROOT.resolve())
    return self_upgrade(text, own_project=own_project)


class AppState(OpsStateMixin, FilesStateMixin):
    """Ό,τι «θυμάται» η εφαρμογή: agent, ρυθμίσεις, ουρά γεγονότων."""

    def __init__(self) -> None:
        self.settings = Settings.load()
        self.agent = None
        self.store: SessionStore | None = None
        self.session_id: str | None = None
        self.transcript: list[dict] = []
        self.files: list[dict] = []  # αρχεία που άλλαξε/έφτιαξε ο βοηθός στη συνεδρία
        self.busy = False
        self.stop_flag = False
        self.error = ""
        self.notice = ""
        self.events: queue.Queue[dict] = queue.Queue()
        self.event_seq = 0
        self.started_at = 0.0
        self.self_update = None
        self.restart_required = False
        self.streaming = None
        self._lock = threading.RLock()
        from .usage_ledger import UsageLedger
        from .settings import DEFAULT_PATH
        self.usage_ledger=UsageLedger(DEFAULT_PATH.parent/'usage.json')
        from .mcp_client import MCPHub
        self.mcp=MCPHub(DEFAULT_PATH.parent/'mcp.json')
        from .voice import VoiceService
        self.voice=VoiceService(DEFAULT_PATH.parent/'voice')
        from .approvals import ApprovalBroker
        self.approvals = ApprovalBroker(lambda:self.push('approval_changed'), self._remember_commands,
                                        self.settings.allowed_commands)
        self._learning_tick = 0
        self._pending_auto_tasks: list[tuple[str, str]] = []
        self._auto_drain_lock = threading.Lock()
        self._drain_owner_tid = 0
        from .gateway_control import GatewayControl
        self.gateway=GatewayControl(DEFAULT_PATH.parent)
        from .mobile_bridge import MobileBridge
        self.mobile_bridge = MobileBridge(self, DEFAULT_PATH.parent / 'mobile_bridge.json')
        self.mobile_bridge.maybe_autostart()

    # ---------------------------------------------------------------- βοηθητικά

    def push(self, kind: str, **data) -> None:
        with self._lock:
            self.event_seq += 1
            seq = self.event_seq
        self.events.put({"seq": seq, "kind": kind, "data": data, "at": time.time()})

    def emit_event(self, event) -> None:
        """Ακροατής για τα γεγονότα του agent → ουρά για το UI."""
        if event.kind == "checkpoint":
            self._save_session()
            return
        if event.kind == "stream_start":
            self.streaming = {"id": event.data["stream_id"], "text": "", "status": "active",
                              "model": event.data.get("model", "")}
        elif event.kind in ("stream_delta", "stream_end") and self.streaming and self.streaming["id"] == event.data.get("stream_id"):
            current = dict(self.streaming)
            if event.kind == "stream_delta":
                current["text"] += event.data.get("text", "")
            else:
                current["status"] = event.data.get("status", "complete")
            self.streaming = None if current["status"] == "tools" else current
        if (event.kind == 'tool_result' and event.data.get('name') in ('edit_file','write_file','create_word_document','create_excel_sheet','create_pdf_document','convert_data_file')
                and (str(event.data.get('result','')).startswith('ΕΓΙΝΕ') or event.data.get('recovery_required'))):
            try:
                paths = event.data.get('paths') or [event.data.get('path')]
                for path in paths:
                    if path:
                        action='ατελής επαναφορά' if event.data.get('recovery_required') else 'νέο' if event.data['name'] in ('write_file','create_word_document','create_excel_sheet','create_pdf_document','convert_data_file') else 'αλλαγή'
                        self._remember_file(path, action)
            except (ValueError, TypeError, OSError, PermissionError):
                pass  # An invalid path must never become an actionable file row.
        self.push(event.kind, **event.data)
        if event.kind=='subtask' and event.data.get('status')!='running':
            for path in event.data.get('files',[]):
                try:self._remember_file(path,'ατελής επαναφορά' if event.data.get('recovery_required') else 'αλλαγή')
                except (OSError,ValueError,PermissionError):pass

    def _remember_commands(self, values):
        self.settings.allowed_commands = values
        self.settings.save()

    def _bind_permissions(self, agent):
        agent.mcp=self.mcp
        agent.ws.external_guard=lambda kind,details:self.approvals.request(agent.ws.root,kind,details,stopped=lambda:agent._stop)
        price_key=lambda:self.settings.provider+'|'+agent.config.base_url+'|'+agent.config.model
        agent.before_model=lambda:self.usage_ledger.check(self.settings.daily_budget,agent.cache_id,self.settings.model_prices.get(price_key()))
        agent.usage_hook=lambda usage:self.usage_ledger.record(agent.cache_id,usage,self.settings.model_prices.get(price_key()))
        agent.config.supports_vision = endpoint_identity(agent.config.base_url)+'|'+agent.config.model in self.settings.vision_models
        agent.ws.command_guard = lambda command,risk:self.approvals.request(
            agent.ws.root,'command',{'command':command},risk,
            autonomous=self.settings.autonomous,stopped=lambda:agent._stop)
        def review(target,original,updated):
            if not self.settings.cautious_edits:return True
            import difflib,hashlib
            sha=hashlib.sha256(updated).hexdigest()
            is_binary=(b'\0' in updated) or (original is not None and b'\0' in original) or target.suffix.lower() in ('.pdf','.docx','.xlsx','.png','.jpg','.jpeg','.webp')
            if is_binary:
                action_lbl='Αντικατάσταση' if original is not None else 'Δημιουργία'
                prev_sz=len(original) if original is not None else 0
                text=f"[{action_lbl} δυαδικού αρχείου {agent.ws.relative(target)}: {prev_sz} → {len(updated)} bytes, sha256={sha}]"
            else:
                text = ''.join(difflib.unified_diff((original or b'').decode('utf-8-sig',errors='replace').splitlines(True),
                    updated.decode('utf-8-sig',errors='replace').splitlines(True),fromfile=str(target),tofile=str(target)))
                if len(text)>120000:raise PermissionError('Η διαφορά ξεπερνά το όριο πλήρους προεπισκόπησης 120000 χαρακτήρων. Κάνε μικρότερη αλλαγή.')
            return self.approvals.request(agent.ws.root,'write',{'path':agent.ws.relative(target),
                'sha256':sha,'diff':text[:120000]},stopped=lambda:agent._stop)
        agent.ws.review_write = review

    def answer_approval(self, ticket, allow=False, always=False):
        if isinstance(ticket,str) and ticket.startswith('gateway:'):
            return self.plugin_action({'action':'approval','id':ticket.split(':',1)[1],'allow':allow})
        return {'ok':self.approvals.respond(ticket,allow,always)}

    def _resolve_routing_creds(self, provider_name: str) -> tuple[str, str]:
        p = str(provider_name or '').strip()
        if not p:
            return '', ''
        url = str(PROVIDERS.get(p, '') or '').strip()
        if not url:
            return '', ''
        key = self.settings.key_for(p)
        is_local = ('127.0.0.1' in url) or ('localhost' in url)
        if not key and not is_local:
            # Never route to a remote provider that has no saved API key of its own
            return '', ''
        return url, key

    def set_preferences(self, data):
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        for key in ('autonomous','cautious_edits','checkpoints','advanced_view','supports_vision'):
            if key in data and type(data[key]) is not bool:return {'error':'Χρειάζεται boolean προτίμηση.'}
        for name in ('autonomous','cautious_edits','checkpoints','advanced_view'):
            if name in data:
                if type(data[name]) is not bool:return {'error':'Χρειάζεται boolean προτίμηση.'}
                setattr(self.settings,name,data[name])
        if 'fast_provider' in data:
            val = str(data.get('fast_provider') or '').strip()
            self.settings.fast_provider = val
            if self.agent is not None:
                self.agent.config.fast_provider = val
                fu, fk = self._resolve_routing_creds(val)
                self.agent.config.fast_base_url = fu
                self.agent.config.fast_api_key = fk
        if 'fast_model' in data:
            val = str(data.get('fast_model') or '').strip()
            self.settings.fast_model = val
            if self.agent is not None:
                self.agent.config.fast_model = val
        if 'smart_provider' in data:
            val = str(data.get('smart_provider') or '').strip()
            self.settings.smart_provider = val
            if self.agent is not None:
                self.agent.config.smart_provider = val
                su, sk = self._resolve_routing_creds(val)
                self.agent.config.smart_base_url = su
                self.agent.config.smart_api_key = sk
        if 'smart_model' in data:
            val = str(data.get('smart_model') or '').strip()
            self.settings.smart_model = val
            if self.agent is not None:
                self.agent.config.smart_model = val
        if 'auto_mode' in data and type(data['auto_mode']) is bool:
            if self.agent is not None:
                self.agent.config.auto_mode = data['auto_mode']
                if data['auto_mode']:
                    self.agent._manual_mode = False
        if 'model' in data:
            val = str(data.get('model') or '').strip()
            if val:
                self.settings.model = val
                if self.agent is not None:
                    self.agent.config.model = val
                    self.agent._default_model_name = val
                    if hasattr(self.agent.model, 'model'):
                        self.agent.model.model = val
        if 'daily_budget' in data or 'prices' in data:
            import math
            try:
                budget=float(data.get('daily_budget',self.settings.daily_budget))
                if not math.isfinite(budget) or budget<0:raise ValueError('Μη έγκυρο όριο.')
                prices=data.get('prices')
                if prices is not None:
                    if not self.agent or not isinstance(prices,dict) or set(prices)!=set(('input','cached','output')):raise ValueError('Χρειάζονται τρεις τιμές ανά εκατομμύριο tokens.')
                    prices={k:float(v) for k,v in prices.items()}
                    if any(not math.isfinite(v) or v<0 for v in prices.values()):raise ValueError('Μη έγκυρες τιμές.')
                    key=self.settings.provider+'|'+self.agent.config.base_url+'|'+self.agent.config.model
                    self.settings.model_prices[key]=prices
                self.settings.daily_budget=budget
            except (TypeError,ValueError) as exc:return {'error':str(exc)}
        if 'supports_vision' in data:
            if not self.agent or type(data['supports_vision']) is not bool:return {'error':'Συνδέσου και όρισε boolean υποστήριξη εικόνων.'}
            name=endpoint_identity(self.agent.config.base_url)+'|'+self.agent.config.model
            self.settings.vision_models=[x for x in self.settings.vision_models if x!=name]+([name] if data['supports_vision'] else [])
            self.agent.config.supports_vision=data['supports_vision']
        if 'ui_language' in data:
            from .i18n import SUPPORTED_LANGUAGES
            lang = str(data.get('ui_language') or '').strip().lower()
            if lang not in SUPPORTED_LANGUAGES:
                return {'error': 'Μη υποστηριζόμενη γλώσσα διεπαφής.'}
            self.settings.ui_language = lang
            if self.agent:
                self.agent.config.ui_language = lang
        if 'ui_theme' in data:
            theme = str(data.get('ui_theme') or '').strip().lower()
            if theme not in ('dark', 'light'):
                return {'error': 'Μη έγκυρο θέμα εμφάνισης.'}
            self.settings.ui_theme = theme
        if 'ui_font_size' in data:
            try:
                fsize = int(data.get('ui_font_size'))
                if not 12 <= fsize <= 18:
                    raise ValueError('Εκτός ορίων (12–18).')
                self.settings.ui_font_size = fsize
            except (TypeError, ValueError) as exc:
                return {'error': str(exc)}
        if self.agent and 'checkpoints' in data:
            from .checkpoints import Checkpoints
            self.agent.config.checkpoints=self.settings.checkpoints
            self.agent.checkpoint_store=Checkpoints(self.agent.ws.root) if self.settings.checkpoints else None
        self.settings.save()
        return self.status()

    def upload_attachment(self,data):
        if self.busy or not self.agent:return {'error':'Συνδέσου και περίμενε να τελειώσει η εργασία.'}
        if data.get('project') is not None and (data.get('project')!=str(self.agent.ws.root) or (data.get('session_id') or '')!=(self.session_id or '')):return {'error':'Άλλαξε η συνομιλία. Επίλεξε ξανά το συνημμένο.'}
        from .attachments import Attachments
        try:return Attachments(self.agent.ws).upload(data.get('name',''),data.get('base64',''))
        except (OSError,ValueError,TypeError) as exc:return {'error':str(exc)}

    def capture_screenshot(self, data=None):
        if self.busy or not self.agent:return {'error':'Συνδέσου πρώτα σε project.'}
        try:
            import io, base64
            from PIL import ImageGrab
            from .attachments import Attachments
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            name = f'screenshot-{int(time.time())}.png'
            return Attachments(self.agent.ws).upload(name, b64)
        except Exception as exc:
            return {'error': f'Δεν λήφθηκε στιγμιότυπο οθόνης: {exc}'}

    def connect(self, provider: str, base_url: str, model: str, api_key: str,
                project: str, max_steps: int, verify: str, confirm_key_destination: bool = False) -> dict:
        from .agent import Akatikito  # τοπικό import: αποφεύγει κύκλο

        with self._lock:
            if self.busy:
                return {"error": "Περίμενε να ολοκληρωθεί η τρέχουσα εργασία."}
            if self.self_update is not None:
                return {"error": "Ολοκλήρωσε ή ακύρωσε πρώτα την αυτοαναβάθμιση."}
            # Το UI στέλνει κενή διεύθυνση για τους έτοιμους παρόχους — τη βρίσκουμε εδώ.
            resolved_url = (base_url or "").strip() or PROVIDERS.get(provider, "")
            # Κενό πεδίο κλειδιού; → ξαναχρησιμοποίησε το αποθηκευμένο κλειδί του πάροχου.
            entered_key = (api_key or "").strip()
            key = entered_key or self.settings.key_for(provider)
            try:
                destination = endpoint_identity(resolved_url)
                known = self.settings.key_origins.get(provider) or ((self.settings.base_url or PROVIDERS.get(provider,'')) if self.settings.provider == provider else PROVIDERS.get(provider,''))
                known = endpoint_identity(known) if known else ''
            except ValueError as exc:
                return {'error':str(exc)}
            if key and not entered_key and known != destination and not confirm_key_destination:
                return {'error':'Το αποθηκευμένο κλειδί ανήκει σε διαφορετική διεύθυνση.',
                        'requires_key_confirmation':True, 'destination':destination}
            try:
                steps = int(max_steps)
            except (TypeError, ValueError):
                steps = 50
            picked_model = ((model or "").strip() or self.settings.models.get(provider)
                            or (self.settings.model if provider == self.settings.provider else "")
                            or (PROVIDER_MODELS.get(provider) or [""])[0])
            if not picked_model:
                return {"error": "Διάλεξε μοντέλο για αυτόν τον πάροχο ή φόρτωσε τη λίστα μοντέλων."}
            fu, fk = self._resolve_routing_creds(self.settings.fast_provider)
            su, sk = self._resolve_routing_creds(self.settings.smart_provider)
            config = Config(
                model=picked_model,
                fast_provider=self.settings.fast_provider,
                fast_model=self.settings.fast_model,
                fast_base_url=fu,
                fast_api_key=fk,
                smart_provider=self.settings.smart_provider,
                smart_model=self.settings.smart_model,
                smart_base_url=su,
                smart_api_key=sk,
                base_url=resolved_url,
                api_key=key,
                max_steps=max(1,min(150,steps)),
                verify_command=(verify or "").strip(),
                checkpoints=self.settings.checkpoints,
                ui_language=self.settings.ui_language,
            )
            try:
                new_agent = Akatikito(root=project, config=config)
            except Exception as exc:  # noqa: BLE001 — μήνυμα λάθους, όχι κρασάρισμα του server
                return {"error": f"{type(exc).__name__}: {exc}"}
            import copy
            updated = copy.deepcopy(self.settings)
            updated.provider = provider
            updated.base_url = (base_url or "").strip()
            updated.model = picked_model
            updated.models[provider] = picked_model
            updated.max_steps = config.max_steps
            updated.verify_command = (verify or "").strip()
            updated.project = project
            updated.remember_project(project)
            if key:
                updated.set_key(provider, key)
                updated.key_origins[provider] = destination
            updated.onboarding_complete = True
            try:updated.save()
            except (OSError,ValueError):
                return {'error':'Δεν αποθηκεύτηκαν οι ρυθμίσεις. Ο οδηγός δεν ολοκληρώθηκε· η προηγούμενη σύνδεση διατηρήθηκε.'}
            self.settings=updated
            self.agent=new_agent
            self.agent.events.subscribe(self.emit_event)
            self._bind_permissions(self.agent)
            self.store = SessionStore(project)
            self.session_id = None
            self.transcript = []
            self.streaming = None
            self.files = []
            self.error = ""
            self.busy = False
            self.notice = "Συνδέθηκες."
            from .features import AGENDA_LOCK
            with AGENDA_LOCK:
                self._load_auto_queue()
            if not getattr(self, '_gateway_worker', False):
                threading.Thread(target=self._auto_connect_mcp, daemon=True).start()
            return self.status()

    def _auto_connect_mcp(self):
        try:
            self.mcp.auto_connect()
        except Exception:
            pass

    def usage_status(self):
        agent=self.agent;view=self.usage_ledger.view(agent.cache_id if agent else '')
        if agent:
            missing=max(0,agent.tokens.get('prompt_tokens',0)-view['session'].get('prompt_tokens',0))
            if missing:view['session']={**view['session'],'unknown':view['session'].get('unknown',0)+1,'legacy_prompt_tokens':missing}
        return view

    def status(self) -> dict:
        agent = self.agent
        streaming = self.streaming
        detected = ""
        if agent is not None and not agent.config.verify_command:
            try:
                detected = detect_verify(agent.ws.root)
            except Exception:  # noqa: BLE001
                detected = ""
        gateway=self.gateway.status()
        remote_approvals=[{**row,"id":"gateway:"+row["id"],"channel":True} for row in gateway.get("approvals",[])]
        return {
            "gateway":gateway,
            "app_root": str(APP_ROOT),
            "connected": agent is not None,
            "fast_provider": self.settings.fast_provider,
            "fast_model": self.settings.fast_model,
            "smart_provider": self.settings.smart_provider,
            "smart_model": self.settings.smart_model,
            "busy": self.busy,
            "project": str(agent.ws.root) if agent else self.settings.project,
            "recent_projects": list(self.settings.recent_projects),
            "model": agent.config.model if agent else self.settings.model,
            "provider": self.settings.provider,
            "mode": agent.mode if agent else modes.DEFAULT_MODE,
            "mode_label": (("Αυτόματο · " if agent.config.auto_mode and not agent._manual_mode else "") + modes.label(agent.mode)) if agent else "",
            "auto_mode": bool(agent and agent.config.auto_mode and not agent._manual_mode),
            "verify": agent.config.verify_command if agent else self.settings.verify_command,
            "detected_verify": detected,
            "tokens": agent.tokens if agent else {},
            "context": agent.context.metrics if agent else {},
            "turn_usage": agent.turn_usage if agent else {},
            "transcript": self.transcript,
            "stream": dict(streaming) if streaming else None,
            "error": self.error,
            "notice": self.notice,
            "session_id": self.session_id,
            "version": _version(),
            "files": list(self.files),
            "files_project": str(self._files_root()),
            "last_seq": self.event_seq,
            "self_update": self.self_update.status() if self.self_update else None,
            "restart_required": self.restart_required,
            'voice':self.voice_status(),
            'onboarding':{'complete':self.settings.onboarding_complete},
            'mcp':self.mcp_status(),
            'usage_ledger':self.usage_status(),
            'subtasks':list(agent.subtasks) if agent else [],
            'todos':list(agent.task.todos) if agent else [],
            'reminders':self.reminders(),
            'agenda':self.agenda_list().get('tasks',[]),
            'approvals':self.approvals.pending()+remote_approvals,
            'learning':self.learning_summary(),
            'preferences':{'autonomous':self.settings.autonomous,'cautious_edits':self.settings.cautious_edits,'checkpoints':self.settings.checkpoints,'supports_vision':bool(agent and agent.config.supports_vision),'advanced_view':self.settings.advanced_view,'daily_budget':self.settings.daily_budget,'ui_language':self.settings.ui_language,'ui_theme':self.settings.ui_theme,'ui_font_size':self.settings.ui_font_size},
        }

    # ---------------------------------------------------------------- chat

    def send(self, text: str, attachments=None) -> dict:
        if self.restart_required:
            return {"error": "Η αναβάθμιση εφαρμόστηκε. Κλείσε και άνοιξε την εφαρμογή."}
        if self.agent is None:
            return {"error": "Δεν έχεις συνδεθεί ακόμα."}
        if self.busy:
            return {"error": "Δουλεύω ήδη — περίμενε να τελειώσω."}
        voice_job=self.voice.status().get('job') or {}
        if self.voice.busy() and voice_job.get('kind')=='transcribe':return {'error':'Ολοκλήρωσε ή ακύρωσε πρώτα την υπαγόρευση.'}
        text = (text or "").strip()
        if not text:
            return {"error": "Κενό μήνυμα."}
        from .attachments import Attachments
        try: attachment_blocks,attachment_names=Attachments(self.agent.ws).content(attachments or [],self.agent.config.supports_vision)
        except (OSError,ValueError,TypeError) as exc:return {'error':str(exc)}
        from .intent import resolve_selection
        resolved, _ = resolve_selection(text, self.agent.messages)
        routed = False
        boundary_seq = self.event_seq
        if self.self_update is None and requests_self_upgrade(resolved, str(self.agent.ws.root)):
            if getattr(self,'_gateway_worker',False):
                return {'error':'Η ελεγχόμενη αυτοαναβάθμιση γίνεται από το παράθυρο της εφαρμογής.'}
            if self.agent._manual_mode and self.agent.mode == "ask":
                return {"error": "Για αυτοαναβάθμιση επίλεξε Αυτόματο ή Κώδικα από το μενού."}
            requested_mode = self.agent.mode if self.agent._manual_mode else modes.classify(resolved)
            result = self.start_upgrade()
            if result.get("error"):
                return result
            if requested_mode == "architect":
                self.agent.set_mode("architect")
            routed = True
            boundary_seq = self.event_seq
        if resolved != text:
            self.agent._pending_owner_goal = resolved
            self.agent.task.begin(resolved, self.agent.messages)
        self.busy = True
        self.streaming = None
        self.stop_flag = False
        self.error = ""
        self.started_at = time.time()
        model_name = self.agent.config.model
        provider = self.settings.provider
        self.transcript.append({"role": "user", "text": text, "attachments":attachment_names})
        self._save_session()
        self.push("user", text=text)

        def run() -> None:
            try:
                from .process_lock import project_lock
                with project_lock(self.agent.ws.root):
                    answer = self.agent.chat(text,attachments=attachment_blocks) if attachment_blocks else self.agent.chat(text)
                self.transcript.append({"role": "assistant", "text": answer or "(χωρίς απάντηση)",
                                        "model": model_name, "provider": provider, 'checkpoint':self.agent.last_checkpoint})
                self.push("answer", text=answer)
                self._save_session()
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                self.push("fatal", text=self.error)
            finally:
                try:
                    self._save_session()
                except Exception as exc:
                    self.error = f"Δεν αποθηκεύτηκε η συνομιλία: {exc}"
                self.busy = False
                self.push("done")
                if not self.stop_flag:
                    try:
                        self._drain_auto_tasks()
                    except Exception:
                        pass

        threading.Thread(target=run, daemon=True).start()
        if routed:
            return {"ok": True, "conversation_changed": True, "last_seq": boundary_seq}
        return {"ok": True}

    def stop(self) -> dict:
        self.stop_flag = True
        self.approvals.cancel()
        scheduled = getattr(self, "_scheduled_active", None)
        if scheduled is not None:
            try:
                scheduled.stop()
            except Exception:
                pass
        if self.agent is not None:
            self.agent.stop()
            self.push("stopped_request")
        return {"ok": True}

    # ---------------------------------------------------------------- συνεδρίες

    def _save_session(self) -> None:
        if self.store is None or self.agent is None:
            return
        if not self.session_id:
            self.session_id = self.store.create()
        self.store.save(
            self.session_id,
            messages=self.agent.messages,
            tokens=self.agent.tokens,
            transcript=self.transcript,
            repair=self.agent.repair.snapshot(),
            task_state=self.agent.task.snapshot(),
            request_cache={'key':self.agent.cache_id, 'context':self.agent.context.snapshot_transport(self.agent.messages)},
            files=self.files,
        )

    def _session_roots(self) -> list[Path]:
        roots = [APP_ROOT]
        if self.settings.project:
            roots.append(Path(self.settings.project).expanduser().resolve())
        if self.store:
            roots.append(self.store.root)
        try:
            roots.extend(Path(row["project"]).resolve() for row in self.gateway.config.load().values())
        except (OSError,ValueError,KeyError):pass
        return list(dict.fromkeys(roots))

    def sessions(self) -> list[dict]:
        rows = []
        for root in self._session_roots():
            if self.self_update and root == self.self_update.stage:
                continue
            for row in SessionStore(root).list():
                rows.append({**row, "project": str(root), "project_name": root.name})
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)

    def open_session(self, session_id: str, project: str = "") -> dict:
        if self.busy or self.self_update:
            return {"error": "Περίμενε ή ολοκλήρωσε πρώτα την αυτοαναβάθμιση."}
        if not session_id or not session_id.isalnum():
            return {"error": "Μη έγκυρη συνεδρία."}
        root = Path(project).resolve() if project else (self.store.root if self.store else Path(self.settings.project or APP_ROOT).resolve())
        if root not in self._session_roots():
            return {"error": "Άγνωστο project."}
        data = SessionStore(root).load(session_id)
        if data is None:
            return {"error": "Η συνομιλία δεν βρέθηκε."}
        source=data.get('source') or {}
        if source.get('platform','desktop')!='desktop' and not getattr(self,'_gateway_worker',False):
            return {'error':'Αυτή η συνεδρία ανήκει σε κανάλι. Άνοιξε την προεπισκόπηση χωρίς μεταφορά του ιστορικού στο chat.'}
        if self.agent is None or self.agent.ws.root != root:
            result = self.connect(self.settings.provider, self.settings.base_url, self.settings.model,
                                  "", str(root), self.settings.max_steps, self.settings.verify_command)
            if result.get("error"):
                return result
        self.agent.messages = list(data.get("messages") or [])
        self.agent.tokens = dict(data.get("tokens") or {"prompt_tokens": 0, "completion_tokens": 0})
        self.agent._verify_failures = 0
        self.agent.repair.restore(data.get("repair"))
        self.agent.task.restore(data.get("task_state"))
        cache = data.get('request_cache')
        cache = cache if isinstance(cache, dict) else {}
        identity = (self.agent.config.model, self.agent.config.base_url)
        self.agent.context.begin_turn()
        self.agent.context.restore_transport(cache.get('context'), self.agent.messages, identity)
        if isinstance(cache.get('key'), str) and len(cache['key']) == 32 and all(c in '0123456789abcdef' for c in cache['key']):
            self.agent.cache_id = cache['key']
        if self.agent.messages and self.agent.messages[0].get("role") == "system":
            self.agent.messages[0] = {"role": "system", "content": self.agent.policy_prompt()}
        self.session_id = session_id
        self.transcript = list(data.get("transcript") or [])
        self.streaming = None
        self.files = []
        saved_files = data.get('files')
        if isinstance(saved_files, list):
            for row in reversed(saved_files[:100]):
                try:
                    if isinstance(row, dict) and row.get('action') in ('νέο','αλλαγή','άνοιγμα'):
                        self._remember_file(row['path'], row['action'])
                except (ValueError, TypeError, OSError, PermissionError, KeyError):
                    continue
        else:
            # Older sessions already contain exact successful tool results.
            calls = {}
            for message in self.agent.messages:
                calls.update({c['id']:c.get('function',{}) for c in message.get('tool_calls',[])})
                fn = calls.get(message.get('tool_call_id'), {})
                if (message.get('role') == 'tool' and fn.get('name') in ('write_file','edit_file')
                        and str(message.get('content','')).startswith('ΕΓΙΝΕ')):
                    try:
                        args = json.loads(fn.get('arguments') or '{}')
                        self._remember_file(args['path'], 'νέο' if fn['name']=='write_file' else 'αλλαγή')
                    except (ValueError, TypeError, OSError, PermissionError, KeyError):
                        continue
        self.notice = f"Συνέχεια: «{data.get('title') or session_id}»."
        return self.status()

    def new_session(self) -> dict:
        if self.busy or self.self_update:
            return {"error": "Περίμενε ή ολοκλήρωσε πρώτα την αυτοαναβάθμιση."}
        if self.agent is not None:
            self.agent.reset()
        self.session_id = None
        self.transcript = []
        self.streaming = None
        self.files = []
        self.notice = "Νέα συνομιλία."
        return self.status()

    def delete_session(self, session_id: str, project: str = "") -> dict:
        if self.busy or self.self_update:
            return {"error": "Περίμενε ή ολοκλήρωσε πρώτα την αυτοαναβάθμιση."}
        if not session_id or not session_id.isalnum():
            return {"error": "Μη έγκυρη συνεδρία."}
        root = Path(project).resolve() if project else (self.store.root if self.store else APP_ROOT)
        if root not in self._session_roots():
            return {"error": "Άγνωστο project."}
        remote=SessionStore(root).load(session_id) or {}
        if (remote.get('source') or {}).get('platform','desktop')!='desktop':
            return {'error':'Οι συνεδρίες καναλιών διατηρούνται για ασφαλή συνέχεια. Χρησιμοποίησε /new στο κανάλι για νέα συνομιλία.'}
        SessionStore(root).delete(session_id)
        if self.store and root == self.store.root and self.session_id == session_id:
            return self.new_session()
        return {"ok": True}

    # ------------------------------------------------------- αυτοαναβάθμιση

    def start_upgrade(self) -> dict:
        if self.busy or self.self_update or self.restart_required:
            return {"error": "Περίμενε να ολοκληρωθεί η εργασία ή κλείσε το προηγούμενο υποψήφιο."}
        if self.agent is None:
            return {"error": "Συνδέσου πρώτα με πάροχο και μοντέλο."}
        from dataclasses import replace
        from .agent import Akatikito
        from .self_update import SelfUpdate
        try:
            update = SelfUpdate(APP_ROOT)
            carried = update.restore_pending()
            config = replace(self.agent.config, verify_command="verify", auto_mode=False)
            agent = Akatikito(str(update.stage), config=config)
            agent.set_mode("code")
            agent.runtime_instructions = (
                "\nΑΥΤΟΑΝΑΒΑΘΜΙΣΗ ΕΝΕΡΓΗ: δουλεύεις σε υποψήφιο της τρέχουσας εγκατεστημένης εφαρμογής. "
                "ΜΠΟΡΕΙΣ να βελτιώσεις τον εαυτό σου: το agent.py, το app_api.py, το ui.html, τα εργαλεία και τα υπόλοιπα αρχεία της εφαρμογής. "
                "Προστατεύονται ΜΟΝΟ τα υπάρχοντα tests και τα self_update.py / verification.py που εγκρίνουν τις αλλαγές. "
                "Αυτός ο περιορισμός ΔΕΝ απαγορεύει την αυτοαναβάθμιση του υπόλοιπου κώδικα. Πρόσθεσε νέα tests για τις αλλαγές σου. "
                "Η εντολή verify τρέχει τον εξωτερικό ελεγκτή με τις εξαρτήσεις της εγκατεστημένης εφαρμογής. "
                "Αν αποτύχει, διάβασε το πραγματικό σφάλμα. Μην προτείνεις παράλειψη ελέγχων ή αλλαγή σε προστατευμένα αρχεία. "
                "Για ρητό αίτημα υλοποίησης προχώρησε στις επιτρεπόμενες αλλαγές χωρίς νέα επιβεβαίωση. "
                "Για ερώτηση/πρόταση απάντησε χωρίς αλλαγές. Η Εφαρμογή παραμένει ξεχωριστή ενέργεια του χρήστη μετά τους ελέγχους."
            )
            self._bind_permissions(agent)
            agent.ws.update_validator = update.validate
            # Enforce immutable baseline files BEFORE writing, not only during validation.
            from .self_update import PROTECTED
            repair_guard = agent.ws.write_guard
            def candidate_guard(target, original, updated):
                relative = target.relative_to(update.stage).as_posix()
                if relative in update.original and (relative.startswith("tests/") or relative in PROTECTED):
                    if updated.encode("utf-8") != update.original[relative]:
                        raise PermissionError("Το υπάρχον test/αρχείο προστασίας δεν αλλάζει σε υποψήφιο. Πρόσθεσε νέο test για τη συμπεριφορά σου.")
                repair_guard(target, original, updated)
            agent.ws.write_guard = candidate_guard
            self._before_upgrade = (self.agent, self.store, self.session_id, self.transcript, self.files)
            self.self_update = update
            self.agent = agent
            self.agent.events.subscribe(self.emit_event)
            self.store = SessionStore(update.stage)
            self.session_id = None
            self.transcript = []
            self.streaming = None
            self.files = []
            self.notice = "Υποψήφιο αυτοαναβάθμισης. Γράψε την αλλαγή που θέλεις. Τα κανονικά αρχεία μένουν ανέπαφα μέχρι να περάσουν οι έλεγχοι και να πατήσεις Εφαρμογή."
            if carried:
                self.notice += f" Μεταφέρθηκε η προηγούμενη δουλειά σε {len(carried)} αρχεία· απαιτείται νέος έλεγχος."
            return self.status()
        except Exception as exc:
            return {"error": str(exc)}

    def check_upgrade(self) -> dict:
        if not self.self_update or self.busy:
            return {"error": "Δεν υπάρχει διαθέσιμο υποψήφιο ή εκτελείται εργασία."}
        self.busy = True
        self.stop_flag = False
        def run():
            try:
                ok, output = self.self_update.validate()
                writable = self.agent is not None and self.agent.mode in ("code", "debug")
                # Policy/integrity failures cannot be fixed by editing the candidate.
                repairable = not output.startswith("ΑΠΟΡΡΙΨΗ:") and "Λείπουν εξαρτήσεις από την Python" not in output
                if not ok and writable and repairable and not self.stop_flag:
                    self.agent._turn_tools = set(modes.allowed_tools(self.agent.mode))
                    evidence = self.agent._check_feedback(False, output, 0)
                    if not self.stop_flag:
                        text = "[Αυτόματη διόρθωση] Ο έλεγχος του υποψηφίου απέτυχε. Εντόπισε την αιτία, κατέγραψε διάγνωση, διόρθωσε τον κώδικα και επανέλεγξε. Δεν απαιτείται νέα επιβεβαίωση.\n" + evidence
                        self.transcript.append({"role": "user", "text": "Ο έλεγχος απέτυχε — ξεκίνησε αυτόματη διάγνωση και διόρθωση."})
                        self._save_session()
                        answer = self.agent.chat(text, internal=True)
                        self.transcript.append({"role": "assistant", "text": answer, "model": self.agent.config.model, "provider": self.settings.provider})
                        self._save_session()
                    ok = not self.agent.repair.pending and bool(self.self_update.status().get("ready"))
                else:
                    if ok and self.agent is not None:
                        self.agent._check_feedback(True, output, 0, "verify")
                    elif writable and not ok:
                        self.agent._check_feedback(False, output, 0, "verify")
                        answer = self.agent._progress_report("Ο έλεγχος εμποδίζεται από το περιβάλλον ή την κατάσταση του υποψηφίου: " + output[-1200:])
                        self.transcript.append({"role":"assistant","text":answer,"model":self.agent.config.model,"provider":self.settings.provider})
                        self._save_session()
                    else:
                        self.push("verify", ok=ok, output=output)
                if ok:
                    self.notice = ("Οι έλεγχοι πέρασαν. Μπορείς να εφαρμόσεις τις αλλαγές." if self.self_update.status().get("ready") else
                                   "Οι έλεγχοι πέρασαν. Δεν υπάρχουν αλλαγές προς εφαρμογή.")
                else:
                    self.notice = "Η διόρθωση δεν επαληθεύτηκε. Δες τη συγκεκριμένη αιτία στη συνομιλία. Τα κανονικά αρχεία δεν άλλαξαν."
            except Exception as exc:
                self.error = f"Αποτυχία αυτόματης διόρθωσης: {type(exc).__name__}: {exc}"
                self.push("fatal", text=self.error)
            finally:
                self.busy = False
                self.push("done")
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def apply_upgrade(self) -> dict:
        if not self.self_update or self.busy:
            return {"error": "Δεν υπάρχει διαθέσιμο υποψήφιο ή εκτελείται εργασία."}
        try:
            result = self.self_update.apply()
            self.restart_required = True
            self.notice = result["notice"]
            return result
        except Exception as exc:
            return {"error": str(exc)}

    def cancel_upgrade(self) -> dict:
        if self.busy:
            return {"error": "Περίμενε να ολοκληρωθεί η εργασία."}
        if self.self_update and not self.restart_required:
            self.agent, self.store, self.session_id, self.transcript, self.files = self._before_upgrade
            self.self_update = None
            self.streaming = None
            self.notice = "Το υποψήφιο κρατήθηκε στο D: για έλεγχο. Επέστρεψες στη συνομιλία σου."
        return self.status()

    # ---------------------------------------------------------------- ρυθμίσεις

    def set_mode(self, name: str) -> dict:
        if self.busy:
            return {"error": "Περίμενε να ολοκληρωθεί η απάντηση."}
        if self.agent is None:
            return {"error": "Δεν έχεις συνδεθεί."}
        ok = self.agent.set_mode(name)
        return {"ok": ok, "mode": self.agent.mode, "label": self.status()["mode_label"], "auto_mode": self.status()["auto_mode"]}

    def change_model(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            return {"error": "Διάλεξε μοντέλο."}
        if self.busy:
            return {"error": "Το μοντέλο αλλάζει όταν ολοκληρωθεί η απάντηση."}
        if self.agent is None:
            return {"error": "Δεν έχεις συνδεθεί."}
        try:
            self.agent.change_model(name)
            self.agent.config.supports_vision=endpoint_identity(self.agent.config.base_url)+'|'+name in self.settings.vision_models
            self.settings.model = name
            self.settings.models[self.settings.provider] = name
            self.settings.save()
            return {"ok": True, "model": name}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    def providers(self) -> dict:
        saved = self.settings
        return {
            "providers": list(PROVIDERS),
            "hints": PROVIDER_HINTS,
            "suggested": PROVIDER_MODELS,
            "current": saved.provider,
            "base_url": saved.base_url,
            "model": saved.model,
            "models": saved.models,
            "project": saved.project,
            "max_steps": saved.max_steps,
            "verify": saved.verify_command,
            "keys": {name: short_key(saved.key_for(name)) for name in saved.known_providers()},
            "warning": saved.warning,
            'onboarding':{'complete':saved.onboarding_complete},
        }

    def load_models(self, provider: str, base_url: str, api_key: str) -> dict:
        url = (base_url or "").strip() or PROVIDERS.get(provider, "")
        entered = (api_key or '').strip()
        key = entered or self.settings.key_for(provider)
        try:
            known = self.settings.key_origins.get(provider) or ((self.settings.base_url or PROVIDERS.get(provider,'')) if self.settings.provider == provider else PROVIDERS.get(provider,''))
            if key and not entered and (not known or endpoint_identity(known) != endpoint_identity(url)):
                return {'models':[], 'error':'Επιβεβαίωσε πρώτα τη νέα διεύθυνση με Σύνδεση πριν σταλεί αποθηκευμένο κλειδί.'}

            found = list_models(url, key, force=True)
            return {"models": found, "error": "" if found else "Ο πάροχος δεν επέστρεψε μοντέλα."}
        except Exception as exc:  # noqa: BLE001
            return {"models": [], "error": f"Δεν φόρτωσε η λίστα ({type(exc).__name__})."}

    def backups(self) -> list[dict]:
        return self.agent.ws.list_backups(limit=20) if self.agent else []

    def restore(self, backup: str) -> dict:
        if self.agent is None:
            return {"error": "Δεν έχεις συνδεθεί."}
        try:
            restored = self.agent.ws.restore(backup)
            return {"ok": True, "restored": restored}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def voice_status(self):
        return {**self.voice.status(),'model':self.settings.voice_model,'language':self.settings.voice_language}

    def configure_voice(self,data):
        from .voice_worker import MODELS,LANGUAGES
        model=data.get('model',self.settings.voice_model)
        language=data.get('language',self.settings.voice_language)
        if self.busy or self.voice.busy():return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        if model not in MODELS or language not in LANGUAGES:return {'error':'Μη έγκυρη ρύθμιση φωνής.'}
        self.settings.voice_model=model;self.settings.voice_language=language;self.settings.save()
        return self.voice_status()

    def start_voice(self,data,prepare=False):
        from .voice import VoiceError
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        try:
            if prepare:return self.voice.prepare(self.settings.voice_model)
            if not self.agent:return {'error':'Συνδέσου πρώτα σε project.'}
            context=[str(self.agent.ws.root),self.session_id or '']
            if [data.get('project'),data.get('session_id') or '']!=context:return {'error':'Άλλαξε η συνομιλία. Η υπαγόρευση δεν στάλθηκε.'}
            return self.voice.transcribe(data.get('base64'),data.get('mime'),self.settings.voice_model,
                                         self.settings.voice_language,context)
        except (VoiceError,OSError,TypeError) as exc:return {'error':str(exc)}

    def close(self):
        if self.agent:self.agent.stop()
        self.approvals.cancel()
        self.voice.close()
        self.mcp.close()
        if getattr(self, 'mobile_bridge', None):
            self.mobile_bridge.stop()

    def mcp_connect(self,name):
        if self.busy or not self.agent:return {'error':'Συνδέσου και ολοκλήρωσε την εργασία.'}
        self.busy=True
        def run():
            try:self.mcp.connect(name,lambda details:self.approvals.request(self.agent.ws.root,'mcp_connect',details,'dangerous',stopped=lambda:self.stop_flag),stopped=lambda:self.stop_flag)
            except Exception as exc:self.error=str(exc);self.push('fatal',text=str(exc))
            finally:self.busy=False;self.push('done')
        self.stop_flag=False;threading.Thread(target=run,daemon=True).start();return {'ok':True}

    def mcp_status(self):
        try:
            pub=self.mcp.public_configs()
            warnings={}
            for n,c in self.mcp.servers.items():
                if getattr(c,'warnings',[]):warnings[n]=list(c.warnings)
            for n,w in getattr(self.mcp,'auto_warnings',{}).items():
                if w:warnings.setdefault(n,[]).extend(w)
            return {
                'configured':list(pub),
                'configs':pub,
                'connected':[n for n,c in self.mcp.servers.items() if not getattr(c,'closed',False)],
                'tools':[n for n,r in self.mcp.entries.items() if not getattr(self.mcp.servers.get(r['server']),'closed',True)],
                'server_tools':self.mcp.server_tools(),
                'warnings':warnings,
            }
        except (OSError,ValueError) as exc:return {'error':str(exc),'configured':[],'configs':{},'connected':[],'tools':[],'server_tools':{}}

    def mcp_save(self,data):
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        try:
            self.mcp.save_server(str(data.get('name') or '').strip(),data.get('config') or {})
            return {'ok':True,'mcp':self.mcp_status()}
        except (OSError,ValueError) as exc:return {'error':str(exc)}

    def mcp_import(self,data):
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        try:
            res=self.mcp.import_json(str(data.get('json') or ''))
            return {'ok':True,'imported':res['imported'],'mcp':self.mcp_status()}
        except (OSError,ValueError) as exc:return {'error':str(exc)}

    def mcp_remove(self,name):
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        try:
            self.mcp.remove_server(str(name or '').strip())
            return {'ok':True,'mcp':self.mcp_status()}
        except (OSError,ValueError) as exc:return {'error':str(exc)}



from .app_api_server import _version, make_handler, start_server  # noqa: E402, F401
