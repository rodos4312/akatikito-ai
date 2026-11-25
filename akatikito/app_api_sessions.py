"""Session persistence and self-upgrade lifecycle methods extracted from app_api.py."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import modes
from .sessions import SessionStore

def _app_root() -> Path:
    from . import app_api

    return app_api.APP_ROOT


class SessionsUpgradeStateMixin:
    """Mixin for AppState: desktop sessions & self-upgrade candidate workflow."""

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
            request_cache={"key": self.agent.cache_id, "context": self.agent.context.snapshot_transport(self.agent.messages)},
            files=self.files,
        )

    def _session_roots(self) -> list[Path]:
        roots = [_app_root()]
        if self.settings.project:
            roots.append(Path(self.settings.project).expanduser().resolve())
        if self.store:
            roots.append(self.store.root)
        try:
            roots.extend(Path(row["project"]).resolve() for row in self.gateway.config.load().values())
        except (OSError, ValueError, KeyError):
            pass
        return list(dict.fromkeys(roots))

    def sessions(self) -> list[dict]:
        rows = []
        for root in self._session_roots():
            if self.self_update and root == self.self_update.stage:
                continue
            for row in SessionStore(root).list():
                rows.append({**row, "project": str(root), "project_name": root.name})
        return sorted(rows, key=lambda r: (bool(r.get("pinned")), r["updated_at"]), reverse=True)

    def pin_session(self, session_id: str, pinned: bool, project: str = "") -> dict:
        if not session_id or not str(session_id).isalnum():
            return {"error": "Μη έγκυρη συνεδρία."}
        root = Path(project).resolve() if project else (self.store.root if self.store else Path(self.settings.project or _app_root()).resolve())
        if root not in self._session_roots():
            return {"error": "Άγνωστο project."}
        rec = SessionStore(root).set_pinned(session_id, bool(pinned))
        if rec is None:
            return {"error": "Η συνομιλία δεν βρέθηκε."}
        return {"ok": True, "pinned": bool(rec.get("pinned")), "sessions": self.sessions()}

    def search_sessions(self, query: str, limit: int = 20) -> dict:
        q = str(query or "").strip()
        if not q:
            return {"query": "", "results": []}
        found: list[dict] = []
        for root in self._session_roots():
            if self.self_update and root == self.self_update.stage:
                continue
            for hit in SessionStore(root).search(q, limit=limit):
                found.append({**hit, "project": str(root), "project_name": root.name})
        found.sort(key=lambda r: (bool(r.get("pinned")), r.get("score", 0), r.get("updated_at", 0)), reverse=True)
        return {"query": q, "results": found[: max(1, min(100, int(limit or 20)))]}

    def open_session(self, session_id: str, project: str = "") -> dict:
        if self.busy or self.self_update:
            return {"error": "Περίμενε ή ολοκλήρωσε πρώτα την αυτοαναβάθμιση."}
        if not session_id or not session_id.isalnum():
            return {"error": "Μη έγκυρη συνεδρία."}
        root = Path(project).resolve() if project else (self.store.root if self.store else Path(self.settings.project or _app_root()).resolve())
        if root not in self._session_roots():
            return {"error": "Άγνωστο project."}
        data = SessionStore(root).load(session_id)
        if data is None:
            return {"error": "Η συνομιλία δεν βρέθηκε."}
        source = data.get("source") or {}
        if source.get("platform", "desktop") != "desktop" and not getattr(self, "_gateway_worker", False):
            return {"error": "Αυτή η συνεδρία ανήκει σε κανάλι. Άνοιξε την προεπισκόπηση χωρίς μεταφορά του ιστορικού στο chat."}
        if self.agent is None or self.agent.ws.root != root:
            result = self.connect(
                self.settings.provider, self.settings.base_url, self.settings.model,
                "", str(root), self.settings.max_steps, self.settings.verify_command,
            )
            if result.get("error"):
                return result
        self.agent.messages = list(data.get("messages") or [])
        self.agent.tokens = dict(data.get("tokens") or {"prompt_tokens": 0, "completion_tokens": 0})
        self.agent._verify_failures = 0
        self.agent.repair.restore(data.get("repair"))
        self.agent.task.restore(data.get("task_state"))
        cache = data.get("request_cache")
        cache = cache if isinstance(cache, dict) else {}
        identity = (self.agent.config.model, self.agent.config.base_url)
        self.agent.context.begin_turn()
        self.agent.context.restore_transport(cache.get("context"), self.agent.messages, identity)
        if isinstance(cache.get("key"), str) and len(cache["key"]) == 32 and all(c in "0123456789abcdef" for c in cache["key"]):
            self.agent.cache_id = cache["key"]
        if self.agent.messages and self.agent.messages[0].get("role") == "system":
            self.agent.messages[0] = {"role": "system", "content": self.agent.policy_prompt()}
        self.session_id = session_id
        self.transcript = list(data.get("transcript") or [])
        self.streaming = None
        self.files = []
        saved_files = data.get("files")
        if isinstance(saved_files, list):
            for row in reversed(saved_files[:100]):
                try:
                    if isinstance(row, dict) and row.get("action") in ("νέο", "αλλαγή", "άνοιγμα"):
                        self._remember_file(row["path"], row["action"])
                except (ValueError, TypeError, OSError, PermissionError, KeyError):
                    continue
        else:
            calls = {}
            for message in self.agent.messages:
                calls.update({c["id"]: c.get("function", {}) for c in message.get("tool_calls", [])})
                fn = calls.get(message.get("tool_call_id"), {})
                if message.get("role") == "tool" and fn.get("name") in ("write_file", "edit_file") and str(message.get("content", "")).startswith("ΕΓΙΝΕ"):
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                        self._remember_file(args["path"], "νέο" if fn["name"] == "write_file" else "αλλαγή")
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
        root = Path(project).resolve() if project else (self.store.root if self.store else _app_root())
        if root not in self._session_roots():
            return {"error": "Άγνωστο project."}
        remote = SessionStore(root).load(session_id) or {}
        if (remote.get("source") or {}).get("platform", "desktop") != "desktop":
            return {"error": "Οι συνεδρίες καναλιών διατηρούνται για ασφαλή συνέχεια. Χρησιμοποίησε /new στο κανάλι για νέα συνομιλία."}
        SessionStore(root).delete(session_id)
        if self.store and root == self.store.root and self.session_id == session_id:
            return self.new_session()
        return {"ok": True}

    def start_upgrade(self) -> dict:
        from . import advanced

        if not advanced.enabled(self.settings, "self_update"):
            return {"error": "Η αυτοαναβάθμιση είναι κλειστή. Ενεργοποίησέ την από Ρυθμίσεις → Advanced."}
        if self.busy or self.self_update or self.restart_required:
            return {"error": "Περίμενε να ολοκληρωθεί η εργασία ή κλείσε το προηγούμενο υποψήφιο."}
        if self.agent is None:
            return {"error": "Συνδέσου πρώτα με πάροχο και μοντέλο."}
        from dataclasses import replace
        from .agent import Akatikito
        from .self_update import PROTECTED, SelfUpdate

        try:
            update = SelfUpdate(_app_root())
            carried = update.restore_pending()
            config = replace(self.agent.config, verify_command="verify", auto_mode=False)
            agent = Akatikito(str(update.stage), config=config)
            agent.set_mode("code")
            agent.runtime_instructions = (
                "\nΑΥΤΟΑΝΑΒΑΘΜΙΣΗ ΕΝΕΡΓΗ: δουλεύεις σε υποψήφιο της τρέχουσας εγκατεστημένης εφαρμογής. "
                "ΜΠΟΡΕΙΣ να βελτιώσεις τον εαυτό σου: το agent.py, το app_api*.py, το ui.html με τα akatikito/ui/*.js και ui/style.css, τα εργαλεία και τα υπόλοιπα αρχεία της εφαρμογής. "
                "Προστατεύονται ΜΟΝΟ τα υπάρχοντα tests και τα self_update.py / verification.py που εγκρίνουν τις αλλαγές. "
                "Αυτός ο περιορισμός ΔΕΝ απαγορεύει την αυτοαναβάθμιση του υπόλοιπου κώδικα. Πρόσθεσε νέα tests για τις αλλαγές σου. "
                "Η εντολή verify τρέχει τον εξωτερικό ελεγκτή με τις εξαρτήσεις της εγκατεστημένης εφαρμογής. "
                "Αν αποτύχει, διάβασε το πραγματικό σφάλμα. Μην προτείνεις παράλειψη ελέγχων ή αλλαγή σε προστατευμένα αρχεία. "
                "Για ρητό αίτημα υλοποίησης προχώρησε στις επιτρεπόμενες αλλαγές χωρίς νέα επιβεβαίωση. "
                "Για ερώτηση/πρόταση απάντησε χωρίς αλλαγές. Η Εφαρμογή παραμένει ξεχωριστή ενέργεια του χρήστη μετά τους ελέγχους."
            )
            self._bind_permissions(agent)
            agent.ws.update_validator = update.validate
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
                        self.transcript.append({"role": "assistant", "text": answer, "model": self.agent.config.model, "provider": self.settings.provider})
                        self._save_session()
                    else:
                        self.push("verify", ok=ok, output=output)
                if ok:
                    self.notice = (
                        "Οι έλεγχοι πέρασαν. Μπορείς να εφαρμόσεις τις αλλαγές."
                        if self.self_update.status().get("ready")
                        else "Οι έλεγχοι πέρασαν. Δεν υπάρχουν αλλαγές προς εφαρμογή."
                    )
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
