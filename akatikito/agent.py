"""Ο Akatikito: βρόχος μοντέλο → εργαλείο → παρατήρηση → ξανά, μέχρι να βρεθεί η λύση."""

from __future__ import annotations

import json
import threading
import uuid
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import memory as global_memory
from . import modes
from .config import Config
from .activity import ActivityGuard
from .context import ContextWindow
from .events import Events
from .model import OpenAIModel, ModelStopped
from .streaming import DeltaBatcher
from .execution import arguments as call_arguments, read_resource
from .repair import RepairState, is_test_command, verification_blocker
from .task_state import TaskState
from .intent import implementation_requested, read_only, normal
from .tools import TOOL_SPECS, dispatch, validate_arguments
from .workspace import Workspace, detect_verify
from . import agent_prompts as _prompts
from .agent_prompts import SYSTEM_PROMPT


class Akatikito:
    def __init__(self, root: str, config: Config | None = None, model=None) -> None:
        self.config = config or Config()
        self.config.verify_command=(self.config.verify_command or "").strip()
        if not self.config.verify_command:
            self.config.verify_command = detect_verify(root)
        self.ws = Workspace(root)
        self.ws.config = self.config
        self.ws.search_stopped = lambda: self._stop
        self.context = ContextWindow(self.ws, self.config.context_max_chars, self.config.context_recent_groups, self.config.context_economy)
        self.activity = ActivityGuard(self.ws)
        self.task = TaskState()
        self.ws.task_state = self.task
        from .features import RepoMap
        self.repo_map = RepoMap(self.ws.root)
        self._pending_owner_goal = ""
        self._pending_completion = None
        self.turn_usage = {"prompt_tokens":0,"completion_tokens":0,"model_calls":0,"recall_calls":0,"remember_calls":0}
        self._current_task = ""
        self.ws.context_store = self.context.store
        self.repair = RepairState(self.ws)
        self.ws.write_guard = self.repair.guard
        self.model = model or OpenAIModel(self.config)
        self._default_model_client = self.model
        self._default_model_name = self.config.model
        self._default_base_url = self.config.base_url
        self._default_api_key = self.config.api_key
        self._session_route_tier: str = ""; self._ask_unlocked_tools: set[str] = set()
        self.events = Events()
        self.messages: list[dict] = []
        self.tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        }
        self._stop = False
        self._verify_failures = 0
        self.mode = modes.DEFAULT_MODE
        self.mode_reason = ""
        self._manual_mode = False
        self.runtime_instructions = ""
        self.subtasks=[]
        self._subtask_tokens_total: int = 0
        self._child_depth=0
        self._active_child=None
        self._active_children: list = []
        self._sub_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._shared_command_lock: threading.Lock | None = None
        self._task_writes: set[str] = set()
        self._task_reads: set[str] = set()
        self._task_checks: int = 0
        self._mutation_generation: int = 0
        self._verified_generation: int = -1
        self._turn_id: int = 0
        self.last_checkpoint = ''
        from .checkpoints import Checkpoints
        self.checkpoint_store = Checkpoints(self.ws.root) if self.config.checkpoints else None
        self.cache_id = uuid.uuid4().hex  # σταθερό routing key ανά συνεδρία· τα hits εξαρτώνται από το κείμενο

    # ------------------------------------------------------------------ έλεγχος

    def stop(self) -> None:
        self._stop = True
        with self._sub_lock:
            children = list(self._active_children)
        if self._active_child is not None and self._active_child not in children:
            children.append(self._active_child)
        for child in children:
            try:
                child.stop()
            except Exception:
                pass
        cancel = getattr(self.model, "cancel_stream", None)
        if callable(cancel):
            cancel()

    def reset(self) -> None:
        with self._sub_lock:
            self._turn_id += 1
        self._session_route_tier = ""; self._ask_unlocked_tools = set()
        self.messages = []
        self.cache_id = uuid.uuid4().hex
        self.context.reset_transport()
        self.context.begin_turn()
        self.activity.begin_turn()
        self.repair.clear()
        self.task.clear()
        self._pending_owner_goal = ""
        self._pending_completion = None
        self._current_task = ""
        self.turn_usage = {"prompt_tokens":0,"completion_tokens":0,"model_calls":0,"recall_calls":0,"remember_calls":0}
        self.context.metrics = {}
        self.tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        }
        self._verify_failures = 0
        if not self._manual_mode:
            self.mode = modes.DEFAULT_MODE
            self.mode_reason = ""

    def _set_mode(self, mode: str, reason: str = "") -> None:
        if mode not in modes.MODES or mode == self.mode:
            return
        self.mode = mode
        self.mode_reason = reason
        self.events.emit("mode", mode=mode, label=modes.label(mode), reason=reason)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": self.policy_prompt()}

    def set_mode(self, name: str) -> bool:
        if name == "auto":
            self._manual_mode = False
            self.config.auto_mode = True
            return True
        if name in modes.MODES:
            self._manual_mode = True
            self._set_mode(name, reason="χειροκίνητα")
            return True
        return False

    def change_model(self, name: str) -> None:
        """Αλλάζει μοντέλο χωρίς νέα σύνδεση — η συζήτηση μένει ως έχει."""
        name = (name or "").strip()
        if not name or name == self.config.model:
            return
        from .model import OpenAIModel

        self.config.model = name
        self.config.base_url = self._default_base_url
        self.model = OpenAIModel(self.config)
        self._default_model_client = self.model
        self._default_model_name = name
        self.events.emit("model", model=name)

    # ------------------------------------------------------------------ prompt

    def system_prompt(self) -> str:
        return _prompts.system_prompt(self)

    def _fixed_prompt_parts(self, about_user: str, include_task: bool = True) -> list[str]:
        return _prompts.fixed_prompt_parts(self, about_user, include_task=include_task)

    def policy_prompt(self) -> str:
        return "\n".join(self._fixed_prompt_parts('', include_task=False))

    def _observe_request_state(self):
        _prompts.observe_request_state(self)

    def _memory_prompt_parts(self, remaining: int, general: str) -> list[str]:
        return _prompts.memory_prompt_parts(self, remaining, general)

    # ------------------------------------------------------------------ βρόχος

    def allowed_tools(self, mode=None):
        mode=mode or self.mode
        tools=set(modes.allowed_tools(mode)) | set(self.mcp.names(mode) if getattr(self,'mcp',None) else [])
        ceiling=getattr(self,'capability_ceiling',None)
        return tools if ceiling is None else tools & ceiling

    def _run_subtask(self, task, mode, max_steps=8, declared_files=None, child_id=None):
        from .agent_subtasks import run_subtask

        return run_subtask(self, task, mode, max_steps=max_steps, declared_files=declared_files, child_id=child_id)

    def _run_subtasks(self, tasks: list[dict]) -> str:
        from .agent_subtasks import run_subtasks

        return run_subtasks(self, tasks)

    def _tool_guard_error(self, name: str, turn_tools: set[str], allow_mode_change: bool = False) -> str | None:
        """Ενιαίος έλεγχος εργαλείων για σειριακή και παράλληλη εκτέλεση."""
        if getattr(self,'_filesystem_failure',''):
            return 'ΑΠΟΡΡΙΨΗ: η προηγούμενη επαναφορά δεν ολοκληρώθηκε. Δες την αναφορά ανάκτησης.'
        if allow_mode_change and name == "set_mode":
            return None
        allowed = self.allowed_tools()
        if name not in allowed or (turn_tools and name not in turn_tools):
            return self.activity.block(
                f"Το «{name}» δεν επιτρέπεται στη λειτουργία {modes.label(self.mode)}. Χρησιμοποίησε διαθέσιμο εργαλείο ή απάντησε."
            )
        return None

    def _execute_call(self, call: dict, turn_tools: set[str]) -> tuple[str, bool]:
        name = call["name"]
        if self._stop or getattr(self,"_owner_denied",False):
            return "ΑΠΟΡΡΙΨΗ: ο χρήστης σταμάτησε ή δεν ενέκρινε την εκτέλεση.", False
        blocked = self._tool_guard_error(name, turn_tools, allow_mode_change=True)
        if blocked:
            return blocked, False
        try:
            args = call_arguments(call)
            invalid = validate_arguments(name, args)
            if invalid:
                return f"ΣΦΑΛΜΑ: {invalid}", False
            if name in ('propose_memory','propose_skill','patch_skill'):
                from .profile import topic_key
                proposals=getattr(self,'_learning_proposals',set())
                kind_group = 'skill' if name in ('propose_skill','patch_skill') else 'memory'
                key=(kind_group,topic_key(args.get('topic') or args.get('name') or ''))
                if key in proposals or sum(k[0]==kind_group for k in proposals)>=(1 if kind_group=='skill' else 3):
                    return self.activity.block('Η πρόταση καταγράφηκε ήδη ή έφτασε το όριο αυτού του αιτήματος.'),False
            blocked = self.activity.before(name,args)
            if blocked:return blocked,False
            if name == 'run_subtask':return self._run_subtask(**args),False
            if name == 'run_subtasks':return self._run_subtasks(**args),False
            if name == "complete_task":
                return self._accept_completion(args), False
            if name == "set_mode":
                target = args.get("mode") or args.get("name") or ""
                ok = target in modes.MODES and set(modes.allowed_tools(target)) <= turn_tools
                if self.repair.pending and target not in ("code", "debug"):
                    ok = False
                if ok:
                    self._set_mode(target, reason="πρόταση μοντέλου")
                    return f"Λειτουργία: {modes.label(self.mode)}", False
                return "ΑΠΟΡΡΙΨΗ: η λειτουργία υπερβαίνει την εργασία ή αφήνει εκκρεμή διόρθωση tests.", False
            if name == "recall_context":
                page = self.context.request_recall(**args)
                if page.get("blocked"):
                    return self.activity.block(page["text"]),False
                return self.context.recall_text(page),False
            if name == "record_diagnosis":
                result = self.repair.diagnose(**args)
                if result.startswith("ΔΙΑΓΝΩΣΗ"):
                    self.events.emit("repair", phase="diagnosis", text=args["cause"], paths=args["paths"])
                return result, False
            targets = ([item.get('path','') for item in args.get('files',[])]
                       if isinstance(args.get('files'),list) else [args.get('path','')])
            if self.mode == "architect" and name in ("write_file", "edit_file"):
                if any(self.ws.resolve(path) != self.ws.root / "PLAN.md" for path in targets):
                    return "ΑΠΟΡΡΙΨΗ: ο Αρχιτέκτονας γράφει μόνο το PLAN.md.", False
            if self.mode == 'assistant' and name in ('write_file','edit_file','convert_data_file'):
                check_targets = ([args.get('output_path', '')] if name == 'convert_data_file' else targets)
                for target in check_targets:
                    if not target:
                        continue
                    path=self.ws.resolve(target)
                    if path.suffix.lower() not in ('.md','.txt','.csv','.json') or any(part.startswith('.') for part in path.relative_to(self.ws.root).parts):
                        return 'ΑΠΟΡΡΙΨΗ: ο Βοηθός γράφει μόνο απλά έγγραφα στον φάκελο project.',False
            if self.mode == 'assistant' and name in ('create_word_document','create_excel_sheet','create_pdf_document'):
                for target in targets:
                    if not target:
                        continue
                    path=self.ws.resolve(target)
                    if any(part.startswith('.') for part in path.relative_to(self.ws.root).parts):
                        return 'ΑΠΟΡΡΙΨΗ: ο Βοηθός δεν γράφει σε κρυφούς φακέλους/αρχεία.',False
            command = (args.get("command") or "").strip()
            checking = name == "run_command" and bool(command) and (command == self.config.verify_command.strip() or is_test_command(command))
            if self.repair.pending and name == "run_command":
                required = self.repair.failure.get("command") or self.config.verify_command
                if not checking or command != required:
                    return "ΑΠΟΡΡΙΨΗ: όσο εκκρεμεί αποτυχία, διάβασε με τα εργαλεία ανάγνωσης και τρέξε μόνο τον καθορισμένο έλεγχο.", False
                if not self.repair.can_verify():
                    return "ΑΠΟΡΡΙΨΗ: ο ίδιος έλεγχος ήδη απέτυχε. Τεκμηρίωσε τη διάγνωση και κάνε διόρθωση πριν τον επανέλεγχο.", False
            from .project_profiles import ProjectProfiles
            from .injection_guard import guard_untrusted_text
            from .audit_log import AuditLog

            proj_profile = getattr(self.ws, "project_profile", None) or ProjectProfiles().get(self.ws.root)
            profile_denial = ProjectProfiles.check_tool(proj_profile, name)
            if profile_denial:
                try:
                    AuditLog(self.ws.root).record("tool_denied", tool=name, mode=self.mode, status="denied", summary=profile_denial)
                except Exception:
                    pass
                return profile_denial, False

            self.ws.organization_scope = "full" if (self.mode in ("code", "debug") and proj_profile == "developer") else "documents"
            if name == "run_command" and self._shared_command_lock is not None:
                with self._shared_command_lock:
                    result = dispatch(self.ws, name, args)
            else:
                result = self.mcp.call(name,args,self) if getattr(self,'mcp',None) and name in self.mcp.entries else dispatch(self.ws, name, args)
            if name in ("web_search", "web_fetch", "browser_fetch", "web_news", "youtube_transcript", "read_document") or name.startswith("mcp_"):
                result = guard_untrusted_text(result, source=name)
            if name in ('propose_memory','propose_skill','patch_skill') and not result.startswith(('ΣΦΑΛΜΑ','ΑΠΟΡΡΙΨΗ','ΑΠΟΤΡΟΠΗ','ΕΝΤΟΠΙΣΤΗΚΕ ΠΑΡΟΜΟΙΟ SKILL')):
                proposals.add(key)
                self._learning_proposals=proposals
            if "[OWNER_DENIED]" in result:self._owner_denied=True
            checking = checking and result.startswith("returncode=")
            if checking:
                self._last_verify_command = command
            if name == "read_file" and not args.get("outline") and not result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
                self.repair.note_read(args["path"])
            from .execution import mutation_paths
            changed = mutation_paths(name, args, result)
            try:
                for path in changed:
                    self.repair.note_change(path)
            except Exception as exc:
                from .editing import EditResult
                result=EditResult("ΣΦΑΛΜΑ ΜΕΤΑ ΤΗΝ ΕΓΓΡΑΦΗ: τα αρχεία γράφτηκαν αλλά η καταγραφή απέτυχε: "+
                                  str(exc),changed,True,getattr(result,'recovery_paths',()))
            if getattr(result,'recovery_required',False):
                self._filesystem_failure=str(result)
            try:
                status_tag = "denied" if str(result).startswith("ΑΠΟΡΡΙΨΗ") else ("error" if str(result).startswith("ΣΦΑΛΜΑ") else "ok")
                AuditLog(self.ws.root).record("tool_call", tool=name, mode=self.mode, status=status_tag, paths=changed, summary=str(result)[:220])
            except Exception:
                pass
            return result, checking
        except PermissionError as exc:
            return f"ΑΠΟΡΡΙΨΗ: {exc}",False
        except Exception as exc:
            return f"ΣΦΑΛΜΑ: {type(exc).__name__}: {exc}", False

    def _execute_calls(self, calls, turn_tools, step):
        """Consecutive independent reads overlap; all side effects remain serial barriers."""
        workers = max(1, min(8, int(self.config.parallel_tool_workers)))
        index = 0
        while index < len(calls):
            group = []
            resources = set()
            while index + len(group) < len(calls) and len(group) < workers:
                call = calls[index + len(group)]
                resource = read_resource(self.ws, call)
                if resource is None or resource in resources:
                    break
                resources.add(resource)
                group.append(call)
            if len(group) < 2 or self._stop:
                call = calls[index]
                self.events.emit("tool_call", name=call["name"], arguments=call["arguments"], step=step)
                self._call_is_last = index == len(calls) - 1
                yield call, *self._execute_call(call, turn_tools)
                index += 1
                continue
            # Only dispatch runs in workers. Guards, events, repair and history are ordered here.
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="akatikito-read") as pool:
                jobs = []
                for call in group:
                    self.events.emit("tool_call", name=call["name"], arguments=call["arguments"], step=step)
                    args = {}
                    error = ""
                    try:
                        args = call_arguments(call)
                        error = self._tool_guard_error(call["name"], turn_tools) or ""
                        if not error and self._stop:
                            error = "ΑΠΟΡΡΙΨΗ: ο χρήστης σταμάτησε την εκτέλεση."
                        elif not error:
                            error = self.activity.before(call["name"], args)
                    except Exception as exc:
                        error = f"ΣΦΑΛΜΑ: {type(exc).__name__}: {exc}"
                    def read(name=call["name"], params=args):
                        if self._stop:
                            return "ΑΠΟΡΡΙΨΗ: ο χρήστης σταμάτησε την εκτέλεση."
                        return dispatch(self.ws, name, params)
                    jobs.append((call, args, None if error else pool.submit(read), error))
                for call, args, future, error in jobs:
                    try:
                        result = error or future.result()
                    except Exception as exc:
                        result = f"ΣΦΑΛΜΑ: {type(exc).__name__}: {exc}"
                    if call["name"] == "read_file" and not args.get("outline") and not result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
                        self.repair.note_read(args["path"])
                    yield call, result, False
            index += len(group)

    def _model_reply(self, request, tools, step):
        stream = getattr(self.model, "chat_stream", None)
        if not self.config.stream_responses or not callable(stream):
            try:
                return self.model.chat(request, tools, cache_key=self.cache_id)
            except Exception as exc:
                self._progress_report("Η κλήση μοντέλου απέτυχε: " + str(exc))
                raise
        stream_id = uuid.uuid4().hex
        self.events.emit("stream_start", stream_id=stream_id, step=step, model=self.config.model)
        batcher = DeltaBatcher(
            lambda text: self.events.emit("stream_delta", stream_id=stream_id, text=text)
        )
        try:
            reply, usage = stream(request, tools, cache_key=self.cache_id, on_delta=batcher.feed,
                                  should_stop=lambda: self._stop)
            batcher.flush()
            self.events.emit("stream_end", stream_id=stream_id,
                             status="tools" if reply.get("tool_calls") else "complete")
            return reply, usage
        except ModelStopped:
            self._stop = True
            batcher.flush()
            self.events.emit("stream_end", stream_id=stream_id, status="stopped")
            return {"content": "", "tool_calls": []}, {}
        except Exception as exc:
            batcher.flush()
            self.events.emit("stream_end", stream_id=stream_id, status="error")
            self._progress_report("Η ροή απάντησης απέτυχε: " + str(exc))
            raise

    def _has_implementation_changes(self):
        return _prompts.has_implementation_changes(self)

    def _accept_completion(self, args):
        return _prompts.accept_completion(self, args)

    def _finish_completion(self):
        return _prompts.finish_completion(self)

    def _check_feedback(self, ok: bool, output: str, step: int, command: str | None = None) -> str:
        self.events.emit("verify", ok=ok, output=output, step=step)
        if ok:
            self._task_checks = getattr(self, "_task_checks", 0) + 1
            self._verified_generation = getattr(self, "_mutation_generation", 0)
            was_repairing = self.repair.pending
            self.repair.passed()
            self._verify_failures = 0
            if was_repairing:
                self.events.emit("repair", phase="passed", text="Η διόρθωση επαληθεύτηκε από τον ίδιο έλεγχο.")
            return "[ΑΥΤΟΜΑΤΟΣ ΕΛΕΓΧΟΣ] πέρασε=True\n" + output
        if '[OWNER_DENIED]' in output:
            self._owner_denied=True
            self._trusted_blocker = output
            return '[ΑΥΤΟΜΑΤΟΣ ΕΛΕΓΧΟΣ] Δεν εκτελέστηκε: ' + output
        if "record_diagnosis" in self._turn_tools:
            self._set_mode("debug", reason="απέτυχε ο έλεγχος — αυτόματη διάγνωση")
        feedback = self.repair.failed(output)
        if getattr(self.ws, "update_validator", None):
            self._trusted_blocker = verification_blocker(output)
            if self._trusted_blocker:
                self.repair.failure["external_blocker"] = True
        self.repair.failure["command"] = command or self.config.verify_command
        feedback += "\nΥποχρεωτικός επανέλεγχος: " + self.repair.failure["command"]
        self._verify_failures = self.repair.attempts
        self.events.emit("repair", phase="investigate", text=self.repair.failure["error"], test=self.repair.failure["test"])
        return feedback

    def _verification_cycle(self, step: int) -> str:
        ok, output = self._verify()
        command = self._last_verify_command
        feedback = self._check_feedback(ok, output, step, command)
        if ok and command != self.config.verify_command and self.config.verify_command:
            ok, output = self._verify()
            feedback += "\n" + self._check_feedback(ok, output, step, self._last_verify_command)
        return feedback

    def chat(self, text: str, internal: bool = False, attachments=None) -> str:
        self._attachment_blocks = list(attachments or [])
        self.last_checkpoint = ''
        key = ''
        if self.checkpoint_store is not None:
            try:
                key = self.checkpoint_store.begin()
                self.last_checkpoint = key
            except Exception as exc:
                return self._progress_report('Δεν δημιουργήθηκε checkpoint: ' + str(exc))
        if not internal and not getattr(self,'_child_depth',0):
            self._learning_proposals=set()
        try:
            answer=self._chat(text,internal)
            if (not internal and not getattr(self,'_child_depth',0) and not self._stop
                    and not self._owner_denied and not self.repair.pending
                    and not self._filesystem_failure
                    and any(e.kind=='answer' for e in self.events.log[self._turn_event_start:])):
                try:
                    from .skills import Skills
                    Skills(self.ws.root).capture(self.events.log[self._turn_event_start:],self.task.goal,
                                                 allow_readback=self.mode in ('assistant','architect'),completed=True,
                                                 traits=getattr(getattr(self,'mcp',None),'entries',{}),
                                                 used_skills=getattr(self,'_used_skills_in_turn',()))
                except (OSError,ValueError) as exc:
                    self.events.emit('learning_error',text=str(exc))
            return answer
        finally:
            if key:
                try:self.checkpoint_store.finish(key)
                except Exception as exc:self.events.emit('checkpoint_error',text=str(exc))

    def _chat(self, text: str, internal: bool = False) -> str:
        with self._sub_lock:
            self._turn_id += 1
        self.turn_usage = {"prompt_tokens":0,"completion_tokens":0,"model_calls":0,"recall_calls":0,"remember_calls":0}
        self._used_skills_in_turn = set()
        self._skill_healing_hints = {}
        is_slash_learn = not internal and bool(re.match(r"^\s*/learn\b", text or "", re.I))
        is_learn_intent = is_slash_learn or (not internal and bool(
            re.search(r"\b(?:φτιαξε|δημιουργησε|αποθηκευσε|κρατα|μαθε|κανε|προτεινε)\b.*\b(?:skill|συνταγη)\b", normal(text or ""))
        ))
        self.ws.learn_requested = is_learn_intent
        is_direct_skill = False
        direct_skill_info = None
        if is_slash_learn:
            learn_topic = re.sub(r"^\s*/learn\b", "", text or "", flags=re.I).strip()
            text = (
                f"[ΕΝΤΟΛΗ ΕΚΜΑΘΗΣΗΣ /learn] Αποστάξε (distill) τη διαδικασία/λύση"
                f"{(' για: ' + learn_topic) if learn_topic else ' από την τρέχουσα συνομιλία'} σε Skill.\n"
                f"1) Έλεγξε πρώτα αν υπάρχει ήδη παρόμοιο skill (list_skills).\n"
                f"2) Αν υπάρχει παρόμοιο skill, διάβασέ το με use_skill και βελτίωσέ το με patch_skill ή propose_skill.\n"
                f"3) Αλλιώς, κάλεσε propose_skill με σαφή steps (3+ βήματα), category, triggers και notes (παγίδες/σημειώσεις)."
            )
        elif not internal:
            m_skill = re.match(r"^\s*/([a-z][a-z0-9-]{1,63})\b(.*)$", text or "", re.S)
            if m_skill:
                from .skills import Skills
                s_name, s_rest = m_skill.group(1), m_skill.group(2).strip()
                s_store = Skills(self.ws.root)
                s_entry = s_store._entries().get(s_name)
                if s_entry is not None:
                    is_direct_skill = True
                    self._used_skills_in_turn.add(s_name)
                    direct_skill_info = (s_store, s_name, s_rest, s_entry.get("description", ""))
                    text = s_rest or f"Εκτέλεσε τα βήματα της συνταγής /{s_name}: {s_entry.get('description', '')}"
        if internal:
            effective = self.task.goal or self.task.begin("Διόρθωσε την εκκρεμή αποτυχία του ελέγχου", self.messages)
        else:
            effective = self.task.begin(self._pending_owner_goal or text, self.messages)
        self._pending_owner_goal = ""
        self._current_task = effective
        self._pending_completion = None
        self._trusted_blocker = ""
        self._owner_denied = False
        self._filesystem_failure = ''
        self._task_writes, self._task_reads = set(), set()
        self._task_actions=set()
        self._task_checks = self._mutation_generation = 0
        self._verified_generation = -1
        self.subtasks=[]
        self._subtask_tokens_total = 0
        self._turn_event_start = len(self.events.log)
        self.context.begin_turn()
        self.activity.begin_turn()
        self._stop = False
        self._verify_failures = 0
        self.repair.attempts = 0
        fresh = not self.messages
        if self.config.auto_mode and not self._manual_mode:
            self._set_mode("assistant" if is_slash_learn else modes.classify(effective), reason="αυτόματα από την εργασία")
        from .agent_routing import apply_model_routing

        apply_model_routing(self, effective, fresh=fresh, is_direct_skill=is_direct_skill)
        self._turn_tools = set(self.allowed_tools())
        if read_only(effective) and not is_learn_intent and not is_direct_skill:
            self._turn_tools &= set(modes.allowed_tools("ask"))
        self.ws.turn_allowed_tools = self._turn_tools
        if direct_skill_info is not None:
            s_store, s_name, s_rest, _ = direct_skill_info
            preloaded = s_store.read(s_name, available_tools=self._turn_tools)
            text = (
                f"[ΑΜΕΣΗ ΕΚΤΕΛΕΣΗ SKILL /{s_name}]\n{preloaded}\n\n"
                f"Οδηγία χρήστη: {s_rest or ('Εκτέλεσε τα βήματα της συνταγής /' + s_name)}"
            )
        can_repair = {"edit_file", "run_command", "record_diagnosis"} <= self._turn_tools
        self._turn_requires_completion = bool(can_repair and implementation_requested(effective) and getattr(self.model, "requires_completion_evidence", False))
        if fresh:
            self.messages.append({"role": "system", "content": self.policy_prompt()})
        elif self.messages[0].get("role") == "system":
            self.messages[0] = {"role":"system", "content":self.policy_prompt()}
        self.messages.append({"role": "user", "content": ([{"type":"text","text":text}]+self._attachment_blocks if getattr(self,"_attachment_blocks",None) else text), "_origin": "internal" if internal else "owner"})
        if self.repair.pending and can_repair:
            self._set_mode("debug", reason="συνέχεια εκκρεμούς διόρθωσης")
            self.messages.append({"role": "user", "content": "[ΕΚΚΡΕΜΗΣ ΑΠΟΤΥΧΙΑ] " + self.repair.terminal() + "\nΔιάβασε ξανά τα σχετικά αρχεία και συνέχισε τη διάγνωση.", "_origin": "internal"})
        self.events.emit("user", text=text)
        self.events.emit("mode", mode=self.mode, label=modes.label(self.mode), reason=self.mode_reason)
        dirty = False
        for step in range(1, self.config.max_steps + 1):
            if self._filesystem_failure:
                self.events.emit("checkpoint")
                return self._progress_report(self._filesystem_failure)
            if self._stop:
                self.events.emit("stopped", step=step)
                return self._stopped_report()
            self.events.emit("step", step=step, of=self.config.max_steps, phase="think")
            self.events.emit("plan_step", step=step)
            allowed = set(self.allowed_tools()) & self._turn_tools
            self.context.reasoning_identity = (self.config.model, self.config.base_url)
            continuous=bool(getattr(self.model,"keeps_active_history",False) or getattr(self.model,"preserves_tool_reasoning",False))
            if continuous:
                native=max(0,int(getattr(self.model,"context_window_tokens",0) or 0))
                configured=max(0,int(self.config.context_active_max_bytes))
                # UTF-8 bytes conservatively bound text token counts; reserve room for tools/output.
                automatic=max(1024,int(native*0.6)) if native else 160000
                self.context.active_max_bytes=min(configured,automatic) if configured else automatic
                self.context.native_context_tokens=native

            # Never reuse old permissions for the sake of a cache hit.
            self.messages[0] = {"role":"system", "content":self.policy_prompt()}
            self._observe_request_state()
            request = self.context.prepare(self.messages, self.repair, self.activity,
                                           continuous=continuous)
            self.activity.update_visible(request)
            advertised = modes.select_ask_tools(self._current_task, allowed, self._ask_unlocked_tools) if self.mode == "ask" else allowed
            tools = [t for t in TOOL_SPECS + (self.mcp.specs() if getattr(self,'mcp',None) else []) if t["function"]["name"] in advertised]
            self.events.emit("context", **self.context.metrics)
            self.events.emit("checkpoint")
            prev_ri = self.runtime_instructions
            reason=getattr(self,'before_model',lambda:'')()
            if reason:return self._progress_report(reason)
            if self.runtime_instructions != prev_ri:
                self.messages[0] = {"role": "system", "content": self.policy_prompt()}
                if request and request[0].get("role") == "system":
                    request[0] = dict(self.messages[0])
            reply, usage = self._model_reply(request, tools, step)
            for key in self.tokens:
                self.tokens[key] += usage.get(key, 0)
            self.turn_usage["model_calls"] += 1
            self.turn_usage["prompt_tokens"] += usage.get("prompt_tokens",0)
            self.turn_usage["completion_tokens"] += usage.get("completion_tokens",0)
            getattr(self,"usage_hook",lambda u:None)(usage)
            if self._stop:
                self.events.emit("stopped", step=step)
                return self._stopped_report()
            self.events.emit("usage", step=step, **{k: usage.get(k, 0) for k in self.tokens})
            reasoning = (reply.get("reasoning") or "").strip()
            if reasoning:
                self.events.emit("reasoning", text=reasoning, step=step)
            calls = reply.get("tool_calls") or []
            content = (reply.get("content") or "").strip()
            assistant = {"role": "assistant", "content": content}
            for field in ("reasoning_content", "reasoning_details"):
                if field in reply:
                    assistant[field] = reply[field]
            if any(k in assistant for k in ("reasoning_content", "reasoning_details")):
                assistant["_reasoning_identity"] = [self.config.model, self.config.base_url]

            if calls:
                assistant["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
            self.messages.append(assistant)
            if calls:
                self.events.emit("act_step", step=step, count=len(calls))
            if not calls:
                if dirty and can_repair and self.config.verify_command:
                    self.messages.append({"role": "user", "content": self._verification_cycle(step), "_origin": "internal"})
                    dirty = False
                if self.repair.pending and can_repair:
                    if self.repair.attempts >= max(1, self.config.max_retries):
                        return self._repair_report()
                    self.messages.append({"role": "user", "content": "[Η ΕΡΓΑΣΙΑ ΣΥΝΕΧΙΖΕΤΑΙ] Ο έλεγχος αποτυγχάνει: " + self.repair.failure.get("error", "") + ". Μην δηλώσεις ολοκλήρωση και μην ζητήσεις νέα έγκριση. Διάβασε, κάλεσε record_diagnosis, διόρθωσε και επανέλεγξε.", "_origin": "internal"})
                    continue
                if self._turn_requires_completion:
                    verified = not self.config.verify_command or self._verified_generation == self._mutation_generation
                    if not self._has_implementation_changes() or not verified:
                        return self._progress_report("Ο agent τερμάτισε χωρίς τεκμηριωμένη υλοποίηση.\nΤελευταία ανεπιβεβαίωτη απάντηση: " + content)
                    self.task.status = "complete"
                verified = not self.config.verify_command or self._verified_generation == self._mutation_generation
                if self._has_implementation_changes() and verified or self._task_checks and not implementation_requested(effective):
                    self.task.status = "complete"
                if self.mode=="assistant" and self._task_actions:self.task.status="complete"
                if self.mode == "architect" and "PLAN.md" in self._task_writes:
                    self.task.status = "complete"
                self.events.emit("report_step", step=step)
                self.events.emit("answer", text=content, steps=step, tokens=dict(self.tokens))
                return content
            if content and not any(call["name"] == "complete_task" for call in calls):
                self.events.emit("thinking", text=content)
            feedback = []
            self._batch_failed = False
            for call, result, checking in self._execute_calls(calls, self._turn_tools, step):
                try:args = json.loads(call["arguments"] or "{}") if isinstance(call["arguments"], str) else dict(call["arguments"] or {})
                except (ValueError,TypeError):args = {}
                from .execution import mutation_paths
                paths=mutation_paths(call["name"],args,result)
                self.events.emit("tool_result", name=call["name"], path=paths[0] if len(paths)==1 else (args.get('path') or args.get('output_path') or ''),
                                 paths=list(paths),recovery_required=getattr(result,'recovery_required',False),
                                 diff=getattr(result,'diff',''),
                                 organization_changes=getattr(result,'organization_changes',[]),
                                 transaction_id=getattr(result,'transaction_id',''),
                                 result=result, step=step)
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result,
                                      "_written_paths":list(paths)})
                if call["name"] == "recall_context":self.turn_usage["recall_calls"] += 1
                if call["name"] == "remember":self.turn_usage["remember_calls"] += 1
                if call["name"] == "use_skill" and isinstance(args, dict) and args.get("name") and not result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
                    self._used_skills_in_turn.add(str(args["name"]).strip())
                if call["name"] in ("patch_skill", "propose_skill") and isinstance(args, dict) and args.get("name") and result.startswith("ΠΡΟΤΑΣΗ SKILL"):
                    self._skill_healing_hints.pop(str(args["name"]).strip(), None)
                if result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
                    self._batch_failed = True
                    for sname in getattr(self, "_used_skills_in_turn", ()):
                        self._skill_healing_hints.setdefault(sname, f"{call['name']}: {result.splitlines()[0][:100]}")
                if isinstance(args, dict) and call["name"] == "read_file" and not args.get("outline") and not result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
                    self._task_reads.add(args.get("path", ""))
                if call['name'] in ('add_reminder','done_reminder','propose_skill','patch_skill','create_word_document','create_excel_sheet','create_pdf_document','convert_data_file') and (result.startswith('ΕΓΙΝΕ') or result.startswith('ΠΡΟΤΑΣΗ SKILL')):self._task_actions.add(call['name'])
                if isinstance(args,dict) and self.activity.record(call["name"],args,result):
                    self.context.note_progress()
                if paths:
                    dirty = True
                    self._task_writes.update(paths)
                    self._mutation_generation += 1
                if checking:
                    self.events.emit("verify_step", step=step, command=self._last_verify_command)
                    command = self._last_verify_command
                    ok = result.startswith("returncode=0")
                    if not ok:
                        for sname in getattr(self, "_used_skills_in_turn", ()):
                            self._skill_healing_hints.setdefault(sname, "Αποτυχία ελέγχου verify")
                    feedback.append(self._check_feedback(ok, result, step, command))
                    if ok and command != self.config.verify_command and self.config.verify_command:
                        feedback.append(self._verification_cycle(step))
                    dirty = False
            if self._filesystem_failure:
                self.events.emit("checkpoint")
                return self._progress_report(self._filesystem_failure)
            if self._stop:
                self.events.emit("stopped", step=step)
                return self._stopped_report()
            # Evidence follows all tool responses, preserving the tool-call protocol.
            for message in feedback:
                self.messages.append({"role": "user", "content": message, "_origin": "internal"})
            if dirty and can_repair and self.config.verify_command:
                self.messages.append({"role": "user", "content": self._verification_cycle(step), "_origin": "internal"})
                dirty = False
            self.events.emit("checkpoint")
            if self._owner_denied:return self._progress_report("Ο χρήστης δεν ενέκρινε την ενέργεια. Δεν εκτελώ άλλες ενέργειες σε αυτό το αίτημα.")
            if self._trusted_blocker:
                return self._progress_report("Ο έλεγχος εμποδίζεται από το περιβάλλον ή την κατάσταση του υποψηφίου: " + self._trusted_blocker)
            if self._pending_completion and not self.repair.pending:
                return self._finish_completion()
            if self.activity.stalled:
                self.events.emit("loop_guard", text=self.activity.last_reason)
                return self._progress_report("Σταμάτησα επαναλαμβανόμενες κλήσεις χωρίς νέα στοιχεία.")
            if self.repair.pending and self.repair.attempts >= max(1, self.config.max_retries):
                self.events.emit("give_up", attempts=self.repair.attempts, error=self.repair.failure.get("error"))
                return self._repair_report()
        self.events.emit("limit", steps=self.config.max_steps)
        if self.repair.pending:
            return self._repair_report()
        return self._progress_report(f"Έφτασα το όριο των {self.config.max_steps} βημάτων.")

    def _repair_report(self):
        if self.task.goal:
            self.task.status = "blocked"
        report = self.repair.terminal()
        self.messages.append({"role":"assistant","content":report})
        return report

    def _stopped_report(self):
        report = "Σταμάτησα όπως ζήτησες."
        self.messages.append({"role": "assistant", "content": report})
        return report

    def _progress_report(self, reason: str) -> str:
        return _prompts.progress_report(self, reason)

    def _verify(self) -> tuple[bool, str]:
        command = (self.repair.failure.get("command") if self.repair.pending else None) or self.config.verify_command
        self._last_verify_command = command
        output = dispatch(self.ws, "run_command", {"command": command})
        return output.startswith("returncode=0"), output
