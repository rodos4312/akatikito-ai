"""Project files, checkpoints, routines, audit view, OS open, and polling methods extracted from app_api.py."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path


def _app_root() -> Path:
    from . import app_api as _api
    return _api.APP_ROOT


class FilesStateMixin:
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
        return self.agent.ws.root if self.agent else Path(self.settings.project or _app_root()).expanduser().resolve()

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
            store = ProjectProfiles(_app_root() / 'data' / 'memory' / 'project_profiles.json')
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
        return RoutinesStore(_app_root() / 'data' / 'memory' / 'routines.json')

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

    def mobile_bridge_status(self):
        return self.mobile_bridge.status()

    def mobile_bridge_start(self, port=0):
        try:
            return self.mobile_bridge.start(port=int(port or 0))
        except Exception as exc:
            return {'error': str(exc), 'active': False}

    def mobile_bridge_stop(self):
        return self.mobile_bridge.stop(user_initiated=True)

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


