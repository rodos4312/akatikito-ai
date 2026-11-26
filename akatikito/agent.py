"""Ο Akatikito: βρόχος μοντέλο → εργαλείο → παρατήρηση → ξανά, μέχρι να βρεθεί η λύση."""

from __future__ import annotations

import threading
import uuid
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
from .repair import RepairState, verification_blocker
from .task_state import TaskState
from .tools import dispatch
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
        self.ws.notes_folder = str(getattr(self.config, "notes_folder", "") or "").strip()
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
        self._pending_steer: list[str] = []
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
        from .session_frame import CacheGuard
        self.checkpoint_store = Checkpoints(self.ws.root) if self.config.checkpoints else None
        self.cache_id = uuid.uuid4().hex  # σταθερό routing key ανά συνεδρία· τα hits εξαρτώνται από το κείμενο
        self.cache_guard = CacheGuard()
        self.cache_frame_fingerprint: str = ""

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

    def steer(self, text: str) -> bool:
        """Queue a mid-flight owner instruction for the next step boundary."""
        clean = str(text or "").strip()
        if not clean:
            return False
        with self._sub_lock:
            self._pending_steer.append(clean)
        self.events.emit("steer_queued", text=clean)
        return True

    def _drain_steer(self) -> None:
        with self._sub_lock:
            if not self._pending_steer:
                return
            joined = "\n".join(self._pending_steer)
            self._pending_steer.clear()
        self.messages.append({"role": "user", "content": "[Οδηγία του χρήστη κατά την εκτέλεση]\n" + joined, "_origin": "owner"})
        self.events.emit("steer_applied", text=joined)

    def reset(self) -> None:
        with self._sub_lock:
            self._turn_id += 1
            self._pending_steer.clear()
        self._session_route_tier = ""; self._ask_unlocked_tools = set()
        self.messages = []
        self.cache_id = uuid.uuid4().hex
        self.cache_guard.reset_for_new_session()
        self.cache_frame_fingerprint = ""
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

    def defer_setting_change(self, label: str, *, apply_now: bool = False) -> dict:
        """Record a mid-session setting change without breaking prompt cache unless apply_now=True."""
        res = self.cache_guard.on_setting_change(label, apply_now=apply_now)
        if apply_now and res.get("fingerprint"):
            self.cache_frame_fingerprint = res["fingerprint"]
        self.events.emit("cache_notice", label=label, **res)
        return res

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
        blocked = getattr(self, '_blocked_advanced_tools', None)
        if blocked:
            tools = tools - blocked
        if not str(getattr(self.config, 'notes_folder', '') or '').strip():
            tools = tools - {'search_notes', 'propose_note_edit'}
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
        from . import agent_dispatch as _dispatch

        return _dispatch.execute_call(self, call, turn_tools, dispatch)

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
        from . import agent_loop as _loop

        return _loop.run_chat_turn(self, text, internal)

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
