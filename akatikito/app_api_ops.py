"""Agenda, reminders, learning, diagnostics, Doctor, and scheduled job methods extracted from app_api.py."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import memory as global_memory
from .sessions import SessionStore


class OpsStateMixin:
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
            elif action=='skill_import':
                result=Skills(self._files_root()).import_markdown(
                    str(data.get('markdown') or ''),
                    scope=str(data.get('scope') or 'user'),
                )
                result['note']=f"Εισήχθη με ασφάλεια η συνταγή /{result['name']}."
            elif action=='skill_delete':
                result=Skills(self._files_root()).delete_owner_skill(str(data.get('name','')).strip())
                result['note']=f"Διαγράφηκε η συνταγή /{result['name']}."
            else:return {'error':'Άγνωστη ενέργεια μνήμης.'}
            self.push('learning_changed')
            return result
        except (OSError,ValueError) as exc:
            return {'error':str(exc)}


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
                from . import app_api as _api
                res = apply_github_update(_api.APP_ROOT, zip_url=zip_url, sha_url=sha_url, zip_name=zip_name)
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

    def doctor_diagnose(self, data=None):
        from .doctor import Doctor
        try:
            check_api = bool((data or {}).get('check_api', False))
            return Doctor(state=self).diagnose(check_api=check_api)
        except Exception as exc:
            return {'error': str(exc)}

    def doctor_fix(self, data=None):
        from .doctor import Doctor
        try:
            payload = data or {}
            cid = str(payload.get('check_id', '') or '').strip()
            action = str(payload.get('action', '') or '').strip()
            if not cid:
                return {'ok': False, 'error': 'Δώσε check_id για επισκευή.'}
            return Doctor(state=self).apply_fix(cid, action=action)
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

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

