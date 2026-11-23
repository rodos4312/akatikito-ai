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

APP_ROOT = Path(__file__).resolve().parents[1]
UI_FILE = APP_ROOT / "akatikito" / "ui.html"


def requests_self_upgrade(text: str, current_project: str = "") -> bool:
    from .intent import self_upgrade
    own_project = bool(current_project and Path(current_project).resolve() == APP_ROOT.resolve())
    return self_upgrade(text, own_project=own_project)


class AppState:
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

    # ---------------------------------------------------------------- βοηθητικά

    def push(self, kind: str, **data) -> None:
        self.event_seq += 1
        self.events.put({"seq": self.event_seq, "kind": kind, "data": data, "at": time.time()})

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

    def plugins_view(self):
        try:
            view = self.gateway.view()
            from .tool_plugins import ToolPlugins
            view['tool_plugins'] = ToolPlugins().catalog()
            return view
        except (OSError,ValueError) as exc:return {'error':str(exc)}

    def plugin_action(self,data):
        if (self.busy or self.self_update) and data.get('action') not in ('approval','stop'):
            return {'error':'Ολοκλήρωσε πρώτα την εργασία ή την αυτοαναβάθμιση.'}
        if data.get('action') == 'toggle_tool_plugin':
            from .tool_plugins import ToolPlugins
            try:
                return ToolPlugins().set_enabled(str(data.get('id', '')), bool(data.get('enabled', True)))
            except (OSError, ValueError) as exc:
                return {'error': str(exc)}
        try:return self.gateway.action(data)
        except Exception as exc:
            return {'error':str(exc) if isinstance(exc,(ValueError,OSError)) else 'Η ενέργεια πρόσθετου απέτυχε.'}

    def channel_session(self,session,project):
        if not isinstance(session,str) or not session.isalnum():return {'error':'Μη έγκυρη συνεδρία.'}
        root=Path(project).resolve()
        if root not in self._session_roots():return {'error':'Άγνωστο project.'}
        data=SessionStore(root).load(session)
        if not data or data.get('source',{}).get('platform','desktop')=='desktop':return {'error':'Δεν βρέθηκε συνεδρία καναλιού.'}
        return {'id':session,'title':data.get('title',''),'transcript':data.get('transcript',[]),
                'source':data['source'],'project':str(root),'readonly':True}

    def learning_summary(self):
        from .profile import Profile
        from .skills import Skills
        try:
            profile=Profile()
            if time.time()-self._learning_tick>60:
                profile.consolidate(due_only=True)
                self._learning_tick=time.time()
            data=profile.load()
            skills=Skills(self._files_root())
            pending=skills.view()['proposals']
            paths=[profile.path,profile.directory/'USER.md',skills.path]
            revision=[p.stat().st_mtime_ns if p.exists() else 0 for p in paths]
            return {'pending':len(data['proposals'])+len(pending),'revision':revision}
        except (OSError,ValueError) as exc:
            return {'pending':0,'error':str(exc)}

    def learning_view(self, offset=0):
        from .profile import Profile
        from .skills import Skills
        try:
            profile=Profile()
            data=profile.listing(offset,30)
            data['history']=profile.load()['revisions'][-10:]
            return {'project':str(self._files_root()),'profile':data,'skills':Skills(self._files_root()).view()}
        except (OSError,ValueError) as exc:
            return {'error':str(exc)}

    def learning_action(self,data):
        from .profile import Profile
        from .skills import Skills
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        if data.get('project')!=str(self._files_root()):
            return {'error':'Το project άλλαξε· ανανέωσε την προεπισκόπηση.'}
        action=data.get('action')
        try:
            if action in ('memory_approve','memory_reject'):
                if not data.get('review'):return {'error':'Χρειάζεται η τρέχουσα προεπισκόπηση.'}
                result=Profile().answer(str(data.get('id','')),action=='memory_approve',data.get('text'),data['review'])
            elif action=='memory_forget':
                result=Profile().forget(str(data.get('id','')))
            elif action=='consolidate':
                result=Profile().consolidate()
            elif action in ('skill_approve','skill_reject'):
                if not data.get('review'):return {'error':'Χρειάζεται η τρέχουσα προεπισκόπηση.'}
                result=Skills(self._files_root()).answer(str(data.get('id','')),action=='skill_approve',data['review'])
            elif action=='skill_create':
                steps=[s.strip() for s in (data.get('steps') or []) if str(s).strip()]
                result=Skills(self._files_root()).save_owner_skill(
                    str(data.get('name','')).strip(),str(data.get('description','')).strip(),steps,
                    str(data.get('scope') or 'user'),category=str(data.get('category') or '').strip(),
                    triggers=data.get('triggers'),notes=data.get('notes'),
                    required_tools=data.get('required_tools'),template=str(data.get('template') or ''))
                result['note']=f"Αποθηκεύτηκε η συνταγή /{result['name']}."
            elif action=='skill_delete':
                result=Skills(self._files_root()).delete_owner_skill(str(data.get('name','')).strip())
                result['note']=f"Διαγράφηκε η συνταγή /{result['name']}."
            else:return {'error':'Άγνωστη ενέργεια μνήμης.'}
            self.push('learning_changed')
            return result
        except (OSError,ValueError) as exc:
            return {'error':str(exc)}

    def set_preferences(self, data):
        if self.busy:return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        for key in ('autonomous','cautious_edits','checkpoints','advanced_view','supports_vision'):
            if key in data and type(data[key]) is not bool:return {'error':'Χρειάζεται boolean προτίμηση.'}
        for name in ('autonomous','cautious_edits','checkpoints','advanced_view'):
            if name in data:
                if type(data[name]) is not bool:return {'error':'Χρειάζεται boolean προτίμηση.'}
                setattr(self.settings,name,data[name])
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
            config = Config(
                model=picked_model,
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

    def reminders(self):
        if not self.agent: return []
        from .features import agenda_for,AGENDA_LOCK
        from .agenda import now
        with AGENDA_LOCK:
            return [t.to_dict() for t in agenda_for(self.agent.ws).open_tasks() if t.day<=now().date()][:50]

    def _auto_queue_path(self) -> Path | None:
        if not self.agent or not getattr(self.agent, 'ws', None):
            return None
        return self.agent.ws.resolve('.agent/auto_queue.json')

    def _load_auto_queue(self) -> list[dict]:
        qpath = self._auto_queue_path()
        items: list[dict] = []
        if qpath is not None and qpath.is_file():
            try:
                raw = json.loads(qpath.read_text(encoding='utf-8'))
                if isinstance(raw, list):
                    for row in raw:
                        if isinstance(row, dict) and row.get('title'):
                            items.append({
                                'id': str(row.get('id') or ''),
                                'title': str(row.get('title') or ''),
                                'prompt': str(row.get('prompt') or row.get('title') or ''),
                                'queued_at': str(row.get('queued_at') or ''),
                            })
            except (OSError, ValueError, TypeError):
                items = []
        self._pending_auto_tasks = [(x['title'], x['prompt']) for x in items]
        return items

    def _save_auto_queue(self, items: list[dict]) -> None:
        self._pending_auto_tasks = [(x['title'], x['prompt']) for x in items]
        qpath = self._auto_queue_path()
        if qpath is None:
            return
        qpath.parent.mkdir(parents=True, exist_ok=True)
        temp = qpath.with_name('.' + qpath.name + '.tmp')
        try:
            temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, qpath)
        finally:
            temp.unlink(missing_ok=True)

    def _drain_auto_tasks(self):
        if not self.agent or self.busy or self.restart_required or self.self_update:
            return
        tid = threading.get_ident()
        if self._drain_owner_tid == tid:
            return
        from .features import AGENDA_LOCK
        with self._auto_drain_lock:
            self._drain_owner_tid = tid
            try:
                if not self.agent or self.busy or self.restart_required or self.self_update:
                    return
                with AGENDA_LOCK:
                    items = self._load_auto_queue()
                    if not items:
                        return
                    head = items[0]
                res = self.send(f"[ΠΡΟΓΡΑΜΜΑΤΙΣΜΕΝΗ ΑΥΤΟΜΑΤΗ ΕΡΓΑΣΙΑ: {head['title']}] {head['prompt']}")
                if isinstance(res, dict) and res.get('ok'):
                    with AGENDA_LOCK:
                        cur = self._load_auto_queue()
                        for idx, item in enumerate(cur):
                            if item.get('id') == head.get('id') and item.get('queued_at') == head.get('queued_at'):
                                cur.pop(idx)
                                break
                        self._save_auto_queue(cur)
            except Exception:
                pass
            finally:
                self._drain_owner_tid = 0

    def reminder_tick(self):
        if not self.agent:
            return
        if time.monotonic()-getattr(self,'_reminder_tick_at',0)>=30:
            self._reminder_tick_at=time.monotonic()
            from .features import agenda_for,AGENDA_LOCK
            from .notify import notify
            with AGENDA_LOCK:
                due=agenda_for(self.agent.ws).take_due()
                if due:
                    queued = self._load_auto_queue()
                    existing = {(x.get('id',''), x.get('queued_at','')) for x in queued}
                    changed = False
                    for task,when in due:
                        threading.Thread(target=notify,args=('Akatikito — '+task.title,task.note or str(when)),daemon=True).start()
                        if getattr(task,'auto_run',False):
                            prompt_text = task.note.strip() if (task.note and task.note.strip()) else task.title
                            qkey = (task.id, str(when))
                            if qkey not in existing:
                                queued.append({
                                    'id': task.id,
                                    'title': task.title,
                                    'prompt': prompt_text,
                                    'queued_at': str(when),
                                })
                                existing.add(qkey)
                                changed = True
                    if changed:
                        self._save_auto_queue(queued)
        self._drain_auto_tasks()

    def export_chat(self, data=None):
        if not self.transcript:
            return {'error': 'Δεν υπάρχουν μηνύματα στη συνομιλία για εξαγωγή.'}
        from .workspace import Workspace
        root = self._files_root()
        if isinstance(data, dict):
            if data.get('project') and Path(data['project']).resolve() != root.resolve():
                return {'error': 'Το project άλλαξε. Ανανέωσε την προβολή.'}
            if 'session_id' in data and str(data.get('session_id') or '') != (self.session_id or ''):
                return {'error': 'Η συνομιλία άλλαξε. Ανανέωσε την προβολή.'}
        ws = Workspace(root)
        lines = [
            '# Συνομιλία Akatikito',
            f'- **Project:** {root}',
            f'- **Μοντέλο:** {" · ".join(x for x in (self.settings.provider, self.agent.config.model if self.agent else self.settings.model) if x) or "—"}',
            f'- **Ημερομηνία εξαγωγής:** {time.strftime("%Y-%m-%d %H:%M:%S")}',
            '---',
            '',
        ]
        for m in self.transcript:
            m_model = m.get('model') or ''
            who = '👤 **Χρήστης**' if m.get('role') == 'user' else (f'🤖 **Akatikito** ({m_model})' if m_model else '🤖 **Akatikito**')
            lines.append(f"### {who}\n\n{m.get('text') or ''}\n")
        markdown = '\n'.join(lines)
        filename = f"akatikito-chat-{time.strftime('%Y%m%d-%H%M%S')}.md"
        target = None
        used_dialog = False
        try:
            import webview
            windows = getattr(webview, 'windows', None) or []
            if windows:
                used_dialog = True
                save_mode = getattr(getattr(webview, 'FileDialog', None), 'SAVE', getattr(webview, 'SAVE_DIALOG', None))
                picked = windows[0].create_file_dialog(
                    save_mode,
                    directory=str(root),
                    save_filename=filename,
                    file_types=('Markdown (*.md)', 'All files (*.*)'),
                )
                if not picked:
                    return {'cancelled': True}
                chosen = picked[0] if isinstance(picked, (list, tuple)) else picked
                if not chosen:
                    return {'cancelled': True}
                target = Path(chosen).resolve()
        except Exception:
            if used_dialog:
                target = None
        if target is None:
            target = ws.resolve(f".agent/exports/{filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name('.' + target.name + '.tmp')
        try:
            temp.write_text(markdown, encoding='utf-8')
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        rel = ws.relative(target) if target.is_relative_to(ws.root) else str(target)
        return {'ok': True, 'path': rel, 'full_path': str(target), 'markdown': markdown}

    def _remove_from_auto_queue(self, ws, task_id: str) -> None:
        tid = str(task_id or '').strip()
        if not tid:
            return
        qpath = ws.resolve('.agent/auto_queue.json')
        if not qpath.is_file():
            return
        try:
            raw = json.loads(qpath.read_text(encoding='utf-8'))
            if not isinstance(raw, list):
                return
            filtered = [row for row in raw if not (isinstance(row, dict) and str(row.get('id') or '') == tid)]
            if len(filtered) != len(raw):
                temp = qpath.with_name('.' + qpath.name + '.tmp')
                try:
                    temp.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding='utf-8')
                    os.replace(temp, qpath)
                finally:
                    temp.unlink(missing_ok=True)
                if self.agent and getattr(self.agent, 'ws', None) and self.agent.ws.root == ws.root:
                    self._pending_auto_tasks = [(str(x.get('title') or ''), str(x.get('prompt') or x.get('title') or '')) for x in filtered if isinstance(x, dict)]
        except (OSError, ValueError, TypeError):
            pass

    def finish_reminder(self,key):
        if self.busy or not self.agent: return {'error':'Περίμενε να ολοκληρωθεί η εργασία.'}
        from .features import done_reminder, AGENDA_LOCK
        from .scheduler import queue_for
        try:
            res = done_reminder(self.agent.ws,key)
            with AGENDA_LOCK:
                self._remove_from_auto_queue(self.agent.ws, key)
                try:
                    queue_for(self).cancel_task(self.agent.ws.root, str(key or ''))
                except Exception:
                    pass
            return {'ok':True,'result':res}
        except (ValueError,KeyError) as exc: return {'error':str(exc)}

    def agenda_list(self):
        from .workspace import Workspace
        from .features import agenda_for, AGENDA_LOCK
        try:
            ws = Workspace(self._files_root())
            with AGENDA_LOCK:
                return {'tasks': [t.to_dict() for t in agenda_for(ws).tasks]}
        except Exception as exc:
            return {'error': str(exc), 'tasks': []}

    def agenda_add(self, data):
        if self.busy: return {'error': 'Περίμενε να ολοκληρωθεί η εργασία.'}
        from .workspace import Workspace
        from .features import agenda_for, AGENDA_LOCK
        from .scheduler import queue_for
        try:
            ws = Workspace(self._files_root())
            if data.get('project') and Path(data['project']).resolve() != ws.root.resolve():
                raise ValueError('Το project άλλαξε. Ανανέωσε την προβολή.')
            if 'session_id' in data and str(data.get('session_id') or '') != (self.session_id or ''):
                raise ValueError('Η συνομιλία άλλαξε. Ανανέωσε την προβολή.')
            title = str(data.get('title') or '').strip()
            date = str(data.get('date') or '').strip()
            tm = str(data.get('time') or '').strip()
            note = str(data.get('note') or '').strip()
            repeat = str(data.get('repeat') or 'none').strip()
            auto_run = bool(data.get('auto_run', False))
            auto_mode = str(data.get('auto_mode') or data.get('mode') or 'assistant').strip()
            if auto_mode not in ('ask', 'assistant', 'code', 'debug'):
                raise ValueError('Μη έγκυρη λειτουργία προγραμματισμού.')
            with AGENDA_LOCK:
                task = agenda_for(ws).add(title, date, tm, note, repeat=repeat, auto_run=auto_run, auto_mode=auto_mode)
                if auto_run:
                    try:
                        queue_for(self).authorize_task(ws.root, task)
                    except Exception:
                        pass
                return {'ok': True, 'task': task.to_dict(), 'tasks': [t.to_dict() for t in agenda_for(ws).tasks]}
        except Exception as exc:
            return {'error': str(exc)}

    def agenda_toggle(self, task_id, done=True):
        if self.busy: return {'error': 'Περίμενε να ολοκληρωθεί η εργασία.'}
        from .workspace import Workspace
        from .features import agenda_for, AGENDA_LOCK
        from .scheduler import queue_for
        try:
            ws = Workspace(self._files_root())
            with AGENDA_LOCK:
                task = agenda_for(ws).toggle(str(task_id or ''), bool(done))
                if bool(done):
                    self._remove_from_auto_queue(ws, str(task_id or ''))
                    try:
                        queue_for(self).cancel_task(ws.root, str(task_id or ''))
                    except Exception:
                        pass
                elif task.auto_run:
                    try:
                        queue_for(self).authorize_task(ws.root, task)
                    except Exception:
                        pass
                return {'ok': True, 'tasks': [t.to_dict() for t in agenda_for(ws).tasks]}
        except Exception as exc:
            return {'error': str(exc)}

    def agenda_delete(self, task_id):
        if self.busy: return {'error': 'Περίμενε να ολοκληρωθεί η εργασία.'}
        from .workspace import Workspace
        from .features import agenda_for, AGENDA_LOCK
        from .scheduler import queue_for
        try:
            ws = Workspace(self._files_root())
            with AGENDA_LOCK:
                agenda_for(ws).remove(str(task_id or ''))
                self._remove_from_auto_queue(ws, str(task_id or ''))
                try:
                    queue_for(self).cancel_task(ws.root, str(task_id or ''))
                except Exception:
                    pass
                return {'ok': True, 'tasks': [t.to_dict() for t in agenda_for(ws).tasks]}
        except Exception as exc:
            return {'error': str(exc)}

    def github_update_check(self):
        from .github_updater import check_github_update
        try:
            info = check_github_update()
            self._last_github_update = info
            return info
        except Exception as exc:
            return {'error': f'Αποτυχία ελέγχου ενημερώσεων GitHub: {exc}'}

    def github_update_apply(self, data=None):
        if self.busy or self.self_update:
            return {'error': 'Περίμενε να ολοκληρωθεί η τρέχουσα εργασία πριν την ενημέρωση.'}
        from .github_updater import apply_github_update
        try:
            cached = getattr(self, '_last_github_update', None) or {}
            zip_url = str((data or {}).get('zip_url') or cached.get('zip_url') or '').strip()
            sha_url = str((data or {}).get('sha_url') or cached.get('sha_url') or '').strip()
            zip_name = str((data or {}).get('zip_name') or cached.get('zip_name') or '').strip()
            if not zip_url or not sha_url:
                raise ValueError('Κάνε πρώτα «Έλεγχος Ενημερώσεων GitHub» για να βρεθεί έγκυρη έκδοση.')
            with self._lock:
                if self.busy:
                    return {'error': 'Η εφαρμογή είναι απασχολημένη.'}
                self.busy = True
            try:
                res = apply_github_update(APP_ROOT, zip_url=zip_url, sha_url=sha_url, zip_name=zip_name)
                if res.get('ok'):
                    self.restart_required = True
                    self.notice = f"Η ενημέρωση v{res.get('version') or ''} εγκαταστάθηκε με επιτυχία. Κλείσε και ξαναάνοιξε το Akatikito."
                return res
            finally:
                self.busy = False
        except Exception as exc:
            return {'error': str(exc)}

    def i18n_bundle(self, lang=None):
        from .i18n import SUPPORTED_LANGUAGES, normalize_lang, translations
        code = normalize_lang(lang or self.settings.ui_language)
        return {'lang': code, 'languages': SUPPORTED_LANGUAGES, 'strings': translations(code)}

    def diagnostics_health(self):
        from .diagnostics import health_report
        try:
            return health_report(self)
        except Exception as exc:
            return {'error': str(exc)}

    def diagnostics_export(self, data=None):
        from .diagnostics import export_diagnostics_zip
        try:
            allow_dialog = not bool((data or {}).get('no_dialog', False))
            return export_diagnostics_zip(self, allow_dialog=allow_dialog)
        except Exception as exc:
            return {'error': str(exc)}

    def scheduled_list(self):
        from .scheduler import queue_for
        try:
            return {'jobs': queue_for(self).view()}
        except Exception as exc:
            return {'error': str(exc), 'jobs': []}

    def scheduled_action(self, job_id, action_name):
        if self.busy or self.self_update:
            return {'error': 'Περίμενε να ολοκληρωθεί η εργασία ή η αυτοαναβάθμιση.'}
        from .scheduler import queue_for
        try:
            queue_for(self).action(str(job_id or ''), str(action_name or ''))
            return {'ok': True, 'jobs': queue_for(self).view()}
        except Exception as exc:
            return {'error': str(exc)}

    def scheduled_history(self, job_id):
        from .scheduler import queue_for
        from .sessions import SessionStore
        try:
            row = next((j for j in queue_for(self).view() if j['id'] == str(job_id or '')), None)
            if not row:
                raise ValueError('Δεν βρέθηκε η εργασία.')
            record = SessionStore(row['project']).load(row.get('session', ''))
            return {'transcript': (record or {}).get('transcript', []), 'readonly': True, 'project': row['project'], 'job': row}
        except Exception as exc:
            return {'error': str(exc)}

    def files_organize(self, action, data):
        from .file_organization import Organizer
        from .organization_tools import record_owner_changes
        from .process_lock import project_lock
        from .workspace import Workspace
        try:
            root = self._files_root()
            if data.get('project') and Path(data['project']).resolve() != root.resolve():
                raise ValueError('Το project άλλαξε. Ανανέωσε την προβολή.')
            if 'session_id' in data and str(data.get('session_id') or '') != (self.session_id or ''):
                raise ValueError('Η συνομιλία άλλαξε. Ανανέωσε την προβολή.')
            if action == 'history':
                return {'history': Organizer(Workspace(root)).history(), 'project': str(root), 'session_id': self.session_id or ''}
            if self.busy or self.self_update:
                raise ValueError('Περίμενε να ολοκληρωθεί η εργασία ή η αυτοαναβάθμιση.')
            self.stop_flag = False
            ws = Workspace(root)
            ws.search_stopped = lambda: self.stop_flag
            if self.agent:
                ws.write_guard = self.agent.ws.write_guard
            service = Organizer(ws)
            if action == 'plan':
                return service.preview(data.get('operations'))
            if action == 'undo_plan':
                return service.undo_preview(data.get('id'))
            if action not in ('apply', 'undo'):
                raise ValueError('Άγνωστη ενέργεια οργάνωσης αρχείων.')
            with self._lock:
                if data.get('project') and Path(data['project']).resolve() != self._files_root().resolve():
                    raise ValueError('Το project άλλαξε. Ανανέωσε την προβολή.')
                if self.busy:
                    raise ValueError('Η εφαρμογή είναι απασχολημένη.')
                self.busy = True
                self.stop_flag = False
            try:
                with project_lock(ws.root):
                    result = (service.apply if action == 'apply' else service.undo)(
                        data.get('id'), data.get('review'), lambda _preview: True
                    )
                record_owner_changes(self, result)
                return {
                    'ok': not getattr(result, 'recovery_required', False),
                    'error': str(result) if getattr(result, 'recovery_required', False) else '',
                    'message': str(result),
                    'paths': list(result.changed_paths),
                    'transaction_id': getattr(result, 'transaction_id', ''),
                    'recovery_required': getattr(result, 'recovery_required', False),
                    'files': list(self.files),
                }
            finally:
                self.busy = False
        except (OSError, ValueError, PermissionError, KeyError, TypeError) as exc:
            return {'error': str(exc)}

    def checkpoint_action(self,key,restore=False):
        if self.busy or self.self_update:return {'error':'Ολοκλήρωσε πρώτα την τρέχουσα εργασία ή το υποψήφιο.'}
        if not self.agent:return {'error':'Συνδέσου πρώτα σε project.'}
        from .checkpoints import Checkpoints
        try:
            store=Checkpoints(self.agent.ws.root)
            if restore:
                files=store.undo(key);self.notice='Επαναφέρθηκαν '+str(len(files))+' αρχεία.'
                self.agent.messages.append({'role':'user','_origin':'internal','content':'[ΕΝΕΡΓΕΙΑ ΧΡΗΣΤΗ — ΑΝΑΙΡΕΣΗ] Το checkpoint '+key+' αναιρέθηκε για: '+', '.join(files)+'. Παλιά αποτελέσματα αυτών των αρχείων δεν είναι τρέχοντα. Διάβασε τη νέα έκδοση πριν συνεχίσεις. Δεν είναι νέο αίτημα υλοποίησης.'})
                self.agent.activity.begin_turn()
                if key==self.agent.last_checkpoint:
                    self.agent.repair.clear();self.agent.task.status='active'
                    self.agent.task.todos=[{**row,'status':'pending'} for row in self.agent.task.todos]
                self.files=[row for row in self.files if (self.agent.ws.root/row['path']).is_file()]
                self._save_session()
                return {'ok':True,'files':files}
            return {'id':key,'changes':store.diff(key)}
        except (OSError,ValueError,KeyError,TypeError) as exc:return {'error':str(exc)}

    def _files_root(self):
        return self.agent.ws.root if self.agent else Path(self.settings.project or APP_ROOT).expanduser().resolve()

    def _remember_file(self, path, action):
        from .workspace import Workspace
        with self._lock:
            ws = Workspace(self._files_root())
            if not isinstance(path, str) or not path.strip():
                raise ValueError('Μη έγκυρη διαδρομή αρχείου.')
            relative = ws.relative(ws.resolve(path)).replace('\\', '/')
            if relative == '.' or ':' in relative:
                raise ValueError('Η διαδρομή πρέπει να δείχνει σε αρχείο του project.')
            old = next((row for row in self.files if os.path.normcase(row['path']) == os.path.normcase(relative)), {})
            if action == 'άνοιγμα' and old.get('action') in ('νέο','αλλαγή'):
                action = old['action']
            self.files = [{'path':relative, 'action':action}] + [row for row in self.files if os.path.normcase(row['path']) != os.path.normcase(relative)]
            self.files = self.files[:100]

    def open_file(self, path, offset=0, version='', project=None, session_id=None):
        from .file_view import rich_preview
        try:
            with self._lock:
                root = self._files_root()
                if ((project is not None and Path(project).resolve() != root) or
                        (session_id is not None and session_id != (self.session_id or ''))):
                    raise ValueError('Η συνομιλία ή το project άλλαξε. Άνοιξε ξανά το αρχείο.')
                result = rich_preview(root, path, offset, version)
                self._remember_file(result['path'], 'άνοιγμα')
                result.update(project=str(root), session_id=self.session_id or '', files=list(self.files))
                if self.session_id and not self.busy:
                    self._save_session()
                return result
        except (OSError, ValueError, TypeError, PermissionError) as exc:
            return {'error':str(exc)}

    def project_profile(self, data=None):
        from .project_profiles import PROFILES, ProjectProfiles
        try:
            root = self._files_root()
            store = ProjectProfiles(APP_ROOT / 'data' / 'memory' / 'project_profiles.json')
            req_profile = str((data or {}).get('profile') or '').strip().lower()
            if req_profile:
                return store.set(root, req_profile)
            return {'project': str(root), 'profile': store.get(root), 'profiles': PROFILES}
        except Exception as exc:
            return {'error': str(exc)}

    def audit_view(self, limit=60):
        from .audit_log import AuditLog
        try:
            root = self._files_root()
            log = AuditLog(root)
            return {
                'project': str(root),
                'entries': log.entries(limit=max(1, min(200, int(limit or 60)))),
                'verification': log.verify_chain(),
            }
        except Exception as exc:
            return {'error': str(exc), 'entries': [], 'verification': {'ok': False, 'count': 0, 'error': str(exc)}}

    def docs_search(self, query='', limit=8):
        from .doc_index import DocIndex
        try:
            root = self._files_root()
            res = DocIndex(root).search(str(query or ''), limit=max(1, min(30, int(limit or 8))), reindex=True)
            res['project'] = str(root)
            return res
        except Exception as exc:
            return {'error': str(exc), 'results': []}

    def _routines_store(self):
        from .routines import RoutinesStore
        return RoutinesStore(APP_ROOT / 'data' / 'memory' / 'routines.json')

    def routines_list(self):
        try:
            return {'routines': self._routines_store().list_routines()}
        except Exception as exc:
            return {'error': str(exc), 'routines': []}

    def routines_add(self, data):
        try:
            item = self._routines_store().add_routine(
                str((data or {}).get('name') or ''),
                str((data or {}).get('prompt') or ''),
                str((data or {}).get('icon') or 'star'),
            )
            return {'ok': True, 'routine': item, 'routines': self._routines_store().list_routines()}
        except Exception as exc:
            return {'error': str(exc)}

    def routines_delete(self, routine_id):
        try:
            deleted = self._routines_store().delete_routine(str(routine_id or ''))
            return {'ok': deleted, 'routines': self._routines_store().list_routines()}
        except Exception as exc:
            return {'error': str(exc)}

    def switch_project(self, project: str):
        """1-click switch to a recent or newly picked project folder using current provider/model."""
        if self.busy or self.self_update:
            return {'error': 'Περίμενε να ολοκληρωθεί η τρέχουσα εργασία.'}
        target = str(project or '').strip()
        if not target:
            return {'error': 'Διάλεξε φάκελο project.'}
        p = Path(target).expanduser().resolve()
        if not p.is_dir():
            return {'error': f'Ο φάκελος δεν βρέθηκε: {target}'}
        return self.connect(
            provider=self.settings.provider,
            base_url=self.settings.base_url,
            model=self.settings.model,
            api_key='',
            project=str(p),
            max_steps=self.settings.max_steps,
            verify=self.settings.verify_command,
        )

    def open_in_os(self, path: str, reveal: bool = False):
        """Safely open a workspace file with its default Windows app or reveal it in Explorer."""
        import subprocess
        from .workspace import Workspace

        _EXEC_BLOCKED = {
            '.exe', '.bat', '.cmd', '.ps1', '.vbs', '.vbe', '.js', '.jse',
            '.wsf', '.wsh', '.msi', '.msp', '.scr', '.com', '.pif', '.hta', '.reg',
        }
        try:
            if not isinstance(path, str) or not path.strip():
                raise ValueError('Δώσε διαδρομή αρχείου.')
            ws = Workspace(self._files_root())
            target = ws.resolve(path)
            if not target.exists():
                raise ValueError('Το αρχείο δεν βρέθηκε στο project.')
            if reveal:
                if os.name == 'nt':
                    subprocess.Popen(['explorer.exe', '/select,', str(target)])
                return {'ok': True, 'path': ws.relative(target), 'action': 'reveal'}
            if not target.is_file():
                raise ValueError('Μπορείς να ανοίξεις μόνο κανονικό αρχείο.')
            if target.suffix.lower() in _EXEC_BLOCKED:
                raise PermissionError(f'Για ασφάλεια δεν επιτρέπεται άμεση εκτέλεση αρχείου {target.suffix}. Χρησιμοποίησε «Φάκελος».')
            if os.name == 'nt':
                os.startfile(str(target))  # noqa: S606
            return {'ok': True, 'path': ws.relative(target), 'action': 'open'}
        except (OSError, ValueError, PermissionError) as exc:
            return {'error': str(exc)}

    def list_project_tree(self, path='.', project=None, offset=0, limit=100, version=''):
        from .file_view import list_tree
        try:
            with self._lock:
                root = self._files_root()
                if project is not None and Path(project).resolve() != root:
                    raise ValueError('Το project άλλαξε.')
                result = list_tree(root, path or '.', offset=offset, limit=limit, version=version)
                result['project'] = str(root)
                result['session_id'] = self.session_id or ''
                return result
        except (OSError, ValueError, TypeError, PermissionError) as exc:
            return {'error':str(exc)}

    def pick_file(self):
        """Choose a workspace file in the native window, without a model call."""
        root, session = self._files_root(), self.session_id or ''
        try:
            import webview
            windows = getattr(webview, 'windows', None) or []
            if not windows:
                return {'error':'Το παράθυρο επιλογής αρχείου είναι διαθέσιμο στην εφαρμογή. Μπορείς να γράψεις τη διαδρομή παρακάτω.'}
            result = windows[0].create_file_dialog(webview.FileDialog.OPEN, directory=str(root), allow_multiple=False)
            if not result:
                return {'cancelled':True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            return self.open_file(str(path), project=str(root), session_id=session)
        except Exception as exc:
            return {'error':f'Δεν άνοιξε το αρχείο: {exc}'}

    def pick_folder(self) -> dict:
        """Ανοίγει το φυσικό παράθυρο επιλογής φακέλου (pywebview)."""
        try:
            import webview

            windows = getattr(webview, "windows", None) or []
            if not windows:
                return {"path": "", "error": "Άνοιξε την εφαρμογή ως παράθυρο για να διαλέξεις φάκελο."}
            result = windows[0].create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            if not result:
                return {"path": ""}
            path = result[0] if isinstance(result, (list, tuple)) else result
            return {"path": str(path)}
        except Exception as exc:  # noqa: BLE001
            return {"path": "", "error": f"{type(exc).__name__}: {exc}"}

    def copy_text(self, text: str) -> dict:
        """Αντιγραφή στο πρόχειρο μέσω Python — δεν θέλει άδειες όπως το WebView."""
        try:
            import pyperclip

            pyperclip.copy(str(text or ""))
            return {"ok": True, "length": len(str(text or ""))}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    def paste_text(self) -> dict:
        """Διάβασμα του πρόχειρου μέσω Python."""
        try:
            import pyperclip

            return {"text": pyperclip.paste() or ""}
        except Exception as exc:  # noqa: BLE001
            return {"text": "", "error": f"{type(exc).__name__}: {exc}"}

    def poll(self, since: int) -> dict:
        self.reminder_tick()
        """Τραβάει τα νέα γεγονότα από την ουρά (το UI ρωτάει κάθε λίγο)."""
        collected: list[dict] = []
        drained: list[dict] = []
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            drained.append(item)
            if item["seq"] > since:
                collected.append(item)
        # ό,τι δεν στάλθηκε (παλιό) πετιέται — το UI το έχει ήδη δει
        state = self.status()
        state["events"] = collected
        state["last_seq"] = self.event_seq
        return state


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
            if not self._authorized(api=path not in ('/','/onboarding.js')):return
            if path == "/":
                body = UI_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
