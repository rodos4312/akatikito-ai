"""Small request views over an unchanged, exactly retrievable conversation."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import threading
from pathlib import Path

ALLOWED = {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content", "reasoning_details"}
INTERNAL = ("[ΑΥΤΟΜΑΤΟΣ ΕΛΕΓΧΟΣ]", "[ΑΠΟΤΥΧΙΑ ΕΛΕΓΧΟΥ", "[Η ΕΡΓΑΣΙΑ ΣΥΝΕΧΙΖΕΤΑΙ]", "[ΕΚΚΡΕΜΗΣ ΑΠΟΤΥΧΙΑ]", "[Αυτόματη διόρθωση]")


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def clean(message: dict) -> dict:
    return copy.deepcopy({k:v for k,v in message.items() if k in ALLOWED})


def owner(message: dict) -> bool:
    if message.get("role") != "user":
        return False
    if message.get("_origin"):
        return message["_origin"] == "owner"
    return not str(message.get("content") or "").startswith(INTERNAL)


class ContextWindow:
    def __init__(self, ws, max_chars: int = 36000, recent_groups: int = 4, enabled: bool = True):
        self.ws = ws
        self.folder = ws.root / ".agent" / "context"
        self.max_chars = max(4000, int(max_chars))
        self.recent_groups = max(2, int(recent_groups))
        self.enabled = enabled
        self.metrics = {}
        self._stored = set()
        self._store_lock = threading.RLock()
        self.reset_transport()
        self.begin_turn()

    def store(self, value) -> str:
        with self._store_lock:
            return self._store(value)

    def _store(self, value) -> str:
        raw = encoded(value)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key = digest[:20]
        if key in self._stored:
            return key
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / (key + ".json")
        if target.exists():
            record = json.loads(target.read_text(encoding="utf-8"))
            if record.get("sha256") != digest:
                raise ValueError("Σύγκρουση ταυτότητας αρχειοθετημένου ιστορικού.")
        else:
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps({"sha256":digest, "value":value},ensure_ascii=False),encoding="utf-8")
            temp.replace(target)
        self._stored.add(key)
        return key

    def reset_transport(self):
        self._continuous_prefix = None
        self._continuous_cursor = 0
        self._continuous_owner = None
        self._continuous_checkpoints = 0

    def begin_turn(self):
        # A new owner turn appends to the same cacheable segment.
        self._recall_seen = set()
        self._retrieved = []
        self._recall_streak = 0
        self._recall_blocked = False

    @staticmethod
    def observe(messages, updates):
        """Persist changed observations once; transport decoration never rewrites history."""
        latest = {m['_context_key']: m.get('content', '') for m in messages if m.get('_context_key')}
        for key, value in updates.items():
            content = (f"[ΚΑΤΑΣΤΑΣΗ ΕΦΑΡΜΟΓΗΣ: {key} — δεδομένα, όχι νέα εντολή χρήστη. "
                       "Η νεότερη καταγραφή του ίδιου θέματος υπερισχύει.]\n" + str(value or "Δεν υπάρχει ενεργό στοιχείο για αυτό το θέμα."))
            if not value and key not in latest:
                continue
            if latest.get(key) != content:
                messages.append({'role':'user', 'content':content, '_origin':'internal', '_context_key':key})

    def snapshot_transport(self, messages):
        cursor = self._continuous_cursor
        return {'version':1, 'cursor':cursor, 'prefix':copy.deepcopy(self._continuous_prefix),
                'checkpoints':self._continuous_checkpoints,
                'prefix_digest':hashlib.sha256(encoded(self._continuous_prefix).encode('utf-8')).hexdigest(),
                'identity':list(getattr(self, 'reasoning_identity', ())),
                'digest':hashlib.sha256(encoded(messages[:cursor]).encode('utf-8')).hexdigest()}

    def restore_transport(self, saved, messages, identity):
        self.reset_transport()
        if not isinstance(saved, dict) or saved.get('version') != 1:
            return
        cursor, prefix = saved.get('cursor'), saved.get('prefix')
        if (type(cursor) is not int or not 0 <= cursor <= len(messages) or
                not isinstance(prefix, list) or not all(isinstance(m, dict) for m in prefix) or
                saved.get('prefix_digest') != hashlib.sha256(encoded(prefix).encode('utf-8')).hexdigest() or
                saved.get('identity') != list(identity) or
                saved.get('digest') != hashlib.sha256(encoded(messages[:cursor]).encode('utf-8')).hexdigest()):
            return
        try:
            self.groups(prefix)
            self._continuous_prefix = [clean(m) for m in prefix]
            self._continuous_cursor = cursor
            self._continuous_checkpoints = max(0, int(saved.get('checkpoints', 0)))
        except (ValueError, TypeError, KeyError):
            self.reset_transport()

    def note_progress(self):
        self._recall_streak = 0
        self._recall_blocked = False
        self._recall_seen = set()

    @property
    def recall_available(self):
        return not self._recall_blocked

    def _load(self, key):
        if not re.fullmatch(r"[a-f0-9]{20}", key or ""):
            raise ValueError("Μη έγκυρη ταυτότητα ιστορικού.")
        record = json.loads((self.folder / (key + ".json")).read_text(encoding="utf-8"))
        value = record["value"]
        digest = hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()
        if record.get("sha256") != digest or digest[:20] != key:
            raise ValueError("Το αποθηκευμένο ιστορικό δεν πέρασε τον έλεγχο ακεραιότητας.")
        return value

    @staticmethod
    def _arguments(call):
        try:
            args = json.loads(call.get("function", {}).get("arguments") or "{}")
            return args if isinstance(args, dict) else {}
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _without_recall(group):
        first = group[0]
        calls = first.get("tool_calls") or []
        if not calls or not any(c.get("function",{}).get("name")=="recall_context" for c in calls):
            return group
        remaining = [c for c in calls if c.get("function",{}).get("name")!="recall_context"]
        if not remaining:
            return []
        kept_ids = {c["id"] for c in remaining}
        message = dict(first);message["tool_calls"] = remaining
        return [message] + [m for m in group[1:] if m.get("tool_call_id") in kept_ids]

    def _readable(self, value):
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and not value.get("tool_calls") and isinstance(value.get("content"),str):
            return value["content"]
        if isinstance(value,list) and value and all(isinstance(m,dict) and "role" in m for m in value):
            calls={c["id"]:c for m in value for c in m.get("tool_calls",[])}
            parts=[]
            for m in value:
                if m.get("role")=="tool":
                    call=calls.get(m.get("tool_call_id"),{});name=call.get("function",{}).get("name","")
                    if name=="recall_context":continue
                    parts.append(f"[{name}]\n"+str(m.get("content") or ""))
                elif m.get("tool_calls"):
                    if m.get("content") and any(c.get("function",{}).get("name")!="recall_context" for c in m["tool_calls"]):
                        parts.append(str(m["content"]))
                    for call in m["tool_calls"]:
                        name=call.get("function",{}).get("name","")
                        if name=="recall_context":continue
                        args=self._arguments(call)
                        parts.append(f"[{name}]\n"+"\n".join(f"{k}: {v}" for k,v in args.items()))
                elif m.get("content"):
                    parts.append(str(m["content"]))
            return "\n\n".join(parts)
        return json.dumps(value,ensure_ascii=False,indent=2)

    def _resolve(self, key, seen):
        if key in seen:return {}
        if len(seen)>=64:raise ValueError("Υπερβολικά εμφωλευμένο ιστορικό. Συνέχισε από την κατάσταση εργασίας και τον τρέχοντα κώδικα.")
        seen.add(key);value=self._load(key)
        messages=value if isinstance(value,list) else [value]
        calls=[c for m in messages if isinstance(m,dict) for c in m.get("tool_calls",[])]
        pure_recall=bool(calls) and all(c.get("function",{}).get("name")=="recall_context" for c in calls)
        refs=[]
        if pure_recall:
            refs=[self._arguments(c).get("id") for c in calls]
        elif isinstance(value,dict) and isinstance(value.get("content"),str):
            try: payload=json.loads(value["content"])
            except (ValueError,TypeError):payload={}
            if isinstance(payload,dict) and {"id","text","total","start"}<=payload.keys():
                refs=[payload["id"]]
        if refs:
            leaves={}
            for ref in dict.fromkeys(refs):
                if not isinstance(ref,str) or not re.fullmatch(r"[a-f0-9]{20}",ref):continue
                try:leaves.update(self._resolve(ref,seen))
                except FileNotFoundError:continue
            if not leaves:raise ValueError("Το αρχείο περιέχει μόνο παλιές ανακτήσεις χωρίς διαθέσιμη πρωτογενή πληροφορία. Χρησιμοποίησε την κατάσταση εργασίας ή διάβασε τον τρέχοντα κώδικα.")
            return leaves
        return {key:self._readable(value)}

    def recall(self, id: str, start: int = 0, length: int = 6000, query: str = "") -> dict:
        leaves=self._resolve(id,set())
        source_ids=sorted(leaves)
        text=leaves[source_ids[0]] if len(source_ids)==1 else "\n\n".join(f"[Πρωτογενές αρχείο {key}]\n{leaves[key]}" for key in source_ids)
        start,length=max(0,int(start)),max(1,min(8000,int(length)))
        found=re.search(re.escape(query),text[start:],re.I) if query else None
        match=start+found.start() if found else -1
        if query:
            if match<0:return {"id":id,"source_ids":source_ids,"found":False,"total":len(text),"text":""}
            start=max(start,match-300)
        end=min(len(text),start+length)
        return {"id":id,"source_ids":source_ids,"start":start,"end":end,"total":len(text),"next":end if end<len(text) else None,"text":text[start:end]}

    def request_recall(self, **args):
        if self._recall_blocked:
            return {"blocked":True,"text":"Η ανάκτηση έχει ήδη δοθεί ή δεν υπάρχει πρόοδος. Χρησιμοποίησε τα ανακτημένα στοιχεία/την κατάσταση εργασίας ή τον τρέχοντα κώδικα."}
        try:page=self.recall(**args)
        except (ValueError,FileNotFoundError) as exc:
            self._recall_blocked=True
            return {"blocked":True,"text":str(exc)}
        key=(tuple(page["source_ids"]),page.get("start",0),page.get("end",0),page.get("found",True))
        sequential=any(p.get("source_ids")==page["source_ids"] and p.get("end")==page.get("start") for p in self._retrieved)
        if key in self._recall_seen or self._recall_streak>=4 and not sequential:
            self._recall_blocked=True
            return {"blocked":True,"text":"Αυτά τα στοιχεία έχουν ήδη ανακτηθεί ή έγιναν 4 ανακτήσεις χωρίς άλλη πρόοδο. Είναι διαθέσιμα στην ενεργή κατάσταση· μην ανακτάς ανακτήσεις. Συνέχισε την εργασία ή απάντησε."}
        self._recall_seen.add(key);self._recall_streak+=1
        if page.get("text"):
            self._retrieved.append(page)
            self._retrieved=self._retrieved[-3:]
        return page

    @staticmethod
    def recall_text(page):
        if page.get("blocked"):return "ΑΠΟΤΡΟΠΗ ΕΠΑΝΑΛΗΨΗΣ: "+page["text"]
        if page.get("found") is False:return "Δεν βρέθηκε το ζητούμενο στοιχείο στο πρωτογενές ιστορικό. Δοκίμασε συγκεκριμένο άλλο στοιχείο ή τον τρέχοντα κώδικα."
        sources=", ".join(page["source_ids"])
        return f"Πρωτογενής πληροφορία {sources} · χαρακτήρες {page['start']}–{page['end']} από {page['total']} · επόμενη θέση {page['next']}\n"+page["text"]

    def memory_excerpt(self, name: str, text: str, limit: int, task: str = "") -> str:
        if not text or len(text) <= limit:
            return text
        key = self.store(text)
        terms = set(re.findall(r"[\w/-]{4,}",task.casefold()))
        lines = text.splitlines()
        ranked = sorted(range(len(lines)),key=lambda i:(sum(term in lines[i].casefold() for term in terms),i),reverse=True)
        chosen, size = [], 0
        for i in ranked:
            if size+len(lines[i])+1 <= max(0,limit):
                chosen.append(i);size+=len(lines[i])+1
        excerpt = "\n".join(lines[i] for i in sorted(chosen))
        if not excerpt and limit>80:
            excerpt = text[:limit//2] + "\n…\n" + text[-limit//2:]
        return excerpt + f"\n[Πλήρες {name}: recall_context id={key}. Ανακτάται ακριβώς, χωρίς απώλεια.]"

    @staticmethod
    def groups(messages: list[dict]) -> list[tuple[int,list[dict]]]:
        groups=[];i=0
        while i<len(messages):
            msg=messages[i];group=[msg]
            if msg.get("role")=="assistant" and msg.get("tool_calls"):
                ids={c["id"] for c in msg["tool_calls"]};j=i+1
                while j<len(messages) and messages[j].get("role")=="tool":
                    group.append(messages[j]);j+=1
                actual={m.get("tool_call_id") for m in group[1:]}
                if actual!=ids:
                    raise ValueError("Ελλιπές ζεύγος κλήσης/αποτελέσματος εργαλείου στο ιστορικό.")
            elif msg.get("role")=="tool":
                raise ValueError("Αποτέλεσμα εργαλείου χωρίς την κλήση του.")
            groups.append((i,group));i+=len(group)
        return groups

    def outcome(self, text: str, limit: int = 600) -> str:
        lines=text.splitlines()
        important=[line for line in lines if re.search(r"exit=|^returncode=|Error:|Exception:|^FAILED |^OK:|ΑΠΟΡΡΙΨΗ|ΣΦΑΛΜΑ|Αρχείο ελέγχων:",line)]
        if important:
            value="\n".join(important)
        else:
            value=text
        if len(value)>limit:
            value=value[:limit//2]+"\n…\n"+value[-limit//2:]
        return value

    def path_key(self, path):
        if not isinstance(path, str):
            return path
        try:
            return self.ws.relative(self.ws.resolve(path))
        except (OSError, ValueError, PermissionError):
            return path

    def summarize(self, group: list[dict], stale_paths: set[str] | None = None) -> dict:
        group=self._without_recall(group)
        if not group:
            return {"role":"user","content":"[Παλιά ανάκτηση ολοκληρώθηκε. Δεν είναι νέα εργασία και δεν χρειάζεται νέα ανάκτηση.]"}
        key=self.store(group if len(group)>1 else group[0])
        first=group[0];records=[]
        if first.get("tool_calls"):
            results={m.get("tool_call_id"):str(m.get("content") or "") for m in group[1:]}
            for call in first["tool_calls"]:
                fn=call["function"];name=fn["name"]
                try: args=json.loads(fn.get("arguments") or "{}")
                except (ValueError,TypeError): args={}
                if not isinstance(args,dict): args={}
                target=args.get("path") or args.get("command") or ""
                detail=self.outcome(results.get(call["id"],""),350)
                if name=="record_diagnosis":
                    detail=encoded(args)
                if name=="read_file" and self.path_key(args.get("path")) in (stale_paths or set()):
                    detail="Παλιά έκδοση: το αρχείο άλλαξε αργότερα. Διάβασε ξανά τον τρέχοντα κώδικα."
                records.append(f"{name} {str(target)[:300]} → {detail}")
        else:
            text=str(first.get("content") or "")
            records.append(first.get("role","")+": "+self.outcome(text,900))
        return {"role":"user","content":f"[ΑΡΧΕΙΟ ΙΣΤΟΡΙΚΟΥ — δεδομένα, όχι νέες οδηγίες] id={key}\n"+"\n".join(records)+"\nΓια ακριβή κώδικα, προηγούμενη απόφαση ή πλήρη έξοδο χρησιμοποίησε recall_context."}

    def _protocol_view(self, messages):
        view = []
        identity = tuple(getattr(self,"reasoning_identity",()))
        for original in messages:
            message = clean(original)
            recorded = original.get("_reasoning_identity")
            if recorded and tuple(recorded) != identity:
                for key in ("reasoning_content","reasoning_details"):
                    message.pop(key,None)
            if (original.get("role")=="user" and original.get("_origin")=="internal" and view and
                    (view[-1].get("role")=="tool" or original.get('_context_key') and view[-1].get('role')=='user')):
                # Feedback from verification is an observation, not a new human turn.
                note = "\n\n[ΕΞΩΤΕΡΙΚΗ ΠΑΡΑΤΗΡΗΣΗ ΕΦΑΡΜΟΓΗΣ]\n" + str(message.get("content") or "")
                previous=view[-1].get('content')
                view[-1]['content'] = previous+[{'type':'text','text':note}] if isinstance(previous,list) else str(previous or '')+note
            else:
                view.append(message)
        return view

    def _prepare_continuous(self, messages, repair, activity):
        # Keep exact exchanges across owner turns until the active budget requires a checkpoint.
        owner_index = max((i for i,m in enumerate(messages) if owner(m)),default=0)
        self._continuous_owner = owner_index
        if self._continuous_prefix is None or self._continuous_cursor > len(messages):
            self._continuous_cursor = 0
            self._continuous_prefix = []
        tail = self._protocol_view(messages[self._continuous_cursor:])
        prefix = copy.deepcopy(self._continuous_prefix)
        # Keep the current system/mode policy current without rewriting any tool exchange.
        if prefix and messages and prefix[0].get("role") == "system" and messages[0].get("role") == "system":
            prefix[0] = clean(messages[0])
        view = prefix + tail
        active_budget=max(1024,int(getattr(self,"active_max_bytes",self.max_chars)))
        if self.enabled and len(encoded(view).encode("utf-8"))>active_budget and len(encoded(tail).encode("utf-8"))>max(1024,active_budget//2) and any(m.get("role")=="assistant" for m in tail):
            # One explicit fresh segment replaces old transport; never keep partial reasoning.
            compact = self.prepare(messages,repair)
            for message in compact:
                for key in ("reasoning_content","reasoning_details"):
                    message.pop(key,None)
            compact.append({"role":"user","content":"[ΣΥΝΕΧΕΙΑ ΙΔΙΟΥ ΑΙΤΗΜΑΤΟΣ ΜΕΤΑ ΑΠΟ ΣΥΜΠΤΥΞΗ — δεν είναι νέα εργασία]\n" + str(messages[owner_index].get("content") or "") + "\nΣυνέχισε από τις πραγματικές αλλαγές και τα αποτελέσματα ελέγχων παραπάνω. Μην ξεκινάς την εξερεύνηση από την αρχή."})
            self._continuous_prefix = compact
            self._continuous_cursor = len(messages)
            self._continuous_checkpoints += 1
            view = copy.deepcopy(compact)
        from .image_eviction import evict_old_outbound_images

        view, evicted_images = evict_old_outbound_images(view)
        # Live observations were persisted by observe() before this view was built.
        # They follow the new user/tool message; the system prefix remains policy-only.
        raw_chars = len(encoded([clean(m) for m in messages]))
        sent_chars = len(encoded(view))
        self.metrics = {"original_chars":raw_chars,"sent_chars":sent_chars,"reduction_percent":round(max(0,1-sent_chars/max(1,raw_chars))*100,1),"over_budget":len(encoded(view).encode("utf-8"))>active_budget,"full_history_preserved":True,"retrieval_available":bool(self._retrieved or re.search(r"id=[a-f0-9]{20}",encoded(view))),"protocol_checkpoints":self._continuous_checkpoints,"active_bytes":len(encoded(view).encode("utf-8")),"active_budget_bytes":active_budget,"native_context_tokens":getattr(self,"native_context_tokens",0),"evicted_images":evicted_images}
        return view

    def _index_group_reads_writes(self, groups):
        from .execution import mutation_paths

        last_writes, last_reads = {}, {}
        for i, group in groups:
            calls = group[0].get("tool_calls") or []
            results = {m.get("tool_call_id"): str(m.get("content") or "") for m in group[1:]}
            mutations = {m.get("tool_call_id"): m.get("_written_paths", []) for m in group[1:]}
            for call in calls:
                fn = call["function"]
                args = self._arguments(call)
                if not args:
                    continue
                path = self.path_key(args.get("path"))
                written = mutations.get(call["id"]) or mutation_paths(fn["name"], args, results.get(call["id"], ""))
                for changed in written:
                    last_writes[self.path_key(changed)] = i
                if fn["name"] == "read_file":
                    last_reads[(path, args.get("start"), args.get("end"))] = i
        real_groups = [
            i for i, g in groups
            if g[0].get("tool_calls")
            and any(c["function"]["name"] not in ("recall_context", "remember", "set_mode") for c in g[0]["tool_calls"])
        ]
        latest_context = {g[0]["_context_key"]: i for i, g in groups if g[0].get("_context_key")}
        return last_writes, last_reads, set(real_groups[-2:]), latest_context

    def _group_staleness(self, group, index: int, last_writes: dict, last_reads: dict) -> tuple[set, bool]:
        stale, redundant = set(), False
        for call in group[0].get("tool_calls") or []:
            fn = call["function"]
            if fn["name"] != "read_file":
                continue
            args = self._arguments(call)
            if not args:
                continue
            path = self.path_key(args.get("path"))
            if last_writes.get(path, -1) > index:
                stale.add(path)
            if last_reads.get((path, args.get("start"), args.get("end")), index) > index:
                redundant = True
        return stale, redundant

    def _trim_command_outputs(self, first: dict, view: list[dict], active_failure: int) -> None:
        # Successful process logs are redundant; current source reads stay exact.
        callmap = {c["id"]: c["function"]["name"] for c in first["tool_calls"]}
        for msg in view[1:]:
            text = str(msg.get("content") or "")
            is_cmd = callmap.get(msg.get("tool_call_id")) == "run_command"
            if is_cmd and len(text) > 1400 and (text.startswith("returncode=0") or active_failure >= 0):
                key = self.store(msg)
                msg["content"] = self.outcome(text, 650) + f"\n[Πλήρης ακριβής έξοδος: recall_context id={key}]"

    def _build_entry(self, number: int, i: int, group: list[dict], recent_start: int,
                     pinned_ids: set[int], recent_real: set[int], latest_context: dict,
                     last_writes: dict, last_reads: dict, active_failure: int) -> dict:
        first = group[0]
        ctx_key = first.get("_context_key")
        pin = (
            first.get("role") == "system"
            or owner(first)
            or i in pinned_ids
            or bool(ctx_key and latest_context.get(ctx_key) == i)
        )
        view = [clean(m) for m in group]
        stale, redundant = self._group_staleness(group, i, last_writes, last_reads)
        if i in recent_real and not stale:
            pin = True
        old = number < recent_start
        huge = len(encoded(view)) > max(10000, self.max_chars // 2)
        if not pin and (old or stale or redundant or huge):
            summary = self.summarize(group, stale)
            if len(encoded(summary)) < len(encoded(view)):
                view = [summary]
        elif first.get("tool_calls"):
            self._trim_command_outputs(first, view, active_failure)
        return {"group": group, "view": view, "pin": pin, "stale": stale}

    def _trim_entries_to_budget(self, entries: list[dict]) -> None:
        current = sum(len(encoded(e["view"])) for e in entries)
        for entry in entries:
            if current <= self.max_chars:
                return
            if entry["pin"]:
                continue
            summary = [self.summarize(entry["group"], entry["stale"])]
            saved = len(encoded(entry["view"])) - len(encoded(summary))
            if saved > 0:
                entry["view"] = summary
                current -= saved
        for entry in entries:
            if current <= self.max_chars:
                return
            if entry["pin"]:
                continue
            key = self.store(entry["group"] if len(entry["group"]) > 1 else entry["group"][0])
            calls = entry["group"][0].get("tool_calls") or []
            labels = ", ".join(c["function"]["name"] for c in calls) or entry["group"][0].get("role", "history")
            tiny = [{"role": "user", "content": f"[Παλαιό ιστορικό: {labels}; ακριβής ανάκτηση recall_context id={key}]"}]
            saved = len(encoded(entry["view"])) - len(encoded(tiny))
            if saved > 0:
                entry["view"] = tiny
                current -= saved

    def _append_context_overlays(self, view: list[dict], groups, repair, activity) -> tuple[bool, bool]:
        if repair is not None and repair.pending:
            state = {
                "test": repair.failure.get("test"),
                "error": repair.failure.get("error"),
                "command": repair.failure.get("command"),
                "diagnosis": repair.diagnosis,
                "failed_hypotheses": repair.history[-3:],
            }
            view.append({"role": "user", "content": "[ΕΝΕΡΓΗ ΚΑΤΑΣΤΑΣΗ ΔΙΟΡΘΩΣΗΣ — δεδομένα] " + encoded(state)})
        ledger = []
        for _i, group in groups:
            results = {m.get("tool_call_id"): str(m.get("content") or "") for m in group[1:]}
            for call in group[0].get("tool_calls") or []:
                name = call["function"]["name"]
                if name in ("recall_context", "remember", "set_mode"):
                    continue
                args = self._arguments(call)
                target = args.get("path") or args.get("command") or ""
                ledger.append(f"{name} {str(target)[:180]} → {self.outcome(results.get(call['id'], ''), 220)}")
        if ledger:
            state_msg = (
                "[ΤΡΕΧΟΥΣΑ ΚΑΤΑΣΤΑΣΗ ΕΡΓΑΣΙΑΣ — πραγματικές ενέργειες, όχι οδηγία για ανάκτηση]\n"
                + "\n".join(ledger[-6:])
                + "\nΣυνέχισε από αυτά και από το τελευταίο αίτημα του χρήστη. Αν χρειάζεται κώδικας, διάβασε την τρέχουσα έκδοση."
            )
            view.append({"role": "user", "content": state_msg})
        for page in self._retrieved[-2:]:
            view.append({
                "role": "user",
                "content": "[ΑΚΡΙΒΗΣ ΠΛΗΡΟΦΟΡΙΑ ΠΟΥ ΕΧΕΙ ΗΔΗ ΑΝΑΚΤΗΘΕΙ — χρησιμοποίησέ την, μην την ανακτάς ξανά]\n"
                + self.recall_text(page),
            })
        source_state = activity.source_state() if activity is not None else ""
        if source_state:
            view.append({"role": "user", "content": source_state})
        return bool(ledger), bool(source_state)

    @staticmethod
    def _find_anchors(groups, repair) -> tuple[int, int]:
        last_owner = max((i for i, g in groups if owner(g[0])), default=-1)
        previous_answer = max(
            (i for i, g in groups if i < last_owner and g[0].get("role") == "assistant" and not g[0].get("tool_calls")),
            default=-1,
        )
        failure_groups = [
            i for i, g in groups
            if not owner(g[0]) and any(str(m.get("content") or "").startswith("[ΑΠΟΤΥΧΙΑ ΕΛΕΓΧΟΥ") for m in g)
        ]
        active_failure = failure_groups[-1] if failure_groups and repair is not None and repair.pending else -1
        return previous_answer, active_failure

    def prepare(self, messages: list[dict], repair=None, activity=None, continuous=False) -> list[dict]:
        if continuous:
            return self._prepare_continuous(messages, repair, activity)
        original = [clean(m) for m in messages]
        groups = self.groups(messages)
        meaningful = [clean(m) for _i, g in groups for m in self._without_recall(g)]
        raw_chars = len(encoded(meaningful))
        if not self.enabled:
            raw_chars = len(encoded(original))
            self.metrics = {
                "original_chars": raw_chars, "sent_chars": raw_chars, "reduction_percent": 0,
                "over_budget": False, "retrieval_available": bool(re.search(r"id=[a-f0-9]{20}", encoded(original))),
            }
            return original
        groups = [(i, self._without_recall(g)) for i, g in groups if self._without_recall(g)]
        previous_answer, active_failure = self._find_anchors(groups, repair)
        last_writes, last_reads, recent_real, latest_context = self._index_group_reads_writes(groups)
        recent_start = max(0, len(groups) - self.recent_groups)
        pinned_ids = {previous_answer, active_failure}
        entries = [
            self._build_entry(
                number, i, group, recent_start, pinned_ids, recent_real,
                latest_context, last_writes, last_reads, active_failure,
            )
            for number, (i, group) in enumerate(groups)
        ]
        self._trim_entries_to_budget(entries)
        view = [m for entry in entries for m in entry["view"]]
        has_ledger, has_source_state = self._append_context_overlays(view, groups, repair, activity)
        from .image_eviction import evict_old_outbound_images

        view, evicted_images = evict_old_outbound_images(view)
        sent_chars = len(encoded(view))
        # Never enlarge an ordinary small conversation just to announce compression.
        if (sent_chars >= raw_chars and not self._retrieved and not has_ledger
                and not has_source_state and not evicted_images and not (repair is not None and repair.pending)):
            view = meaningful
            sent_chars = raw_chars
        self.metrics = {
            "original_chars": raw_chars,
            "sent_chars": sent_chars,
            "reduction_percent": round(max(0, 1 - sent_chars / max(1, raw_chars)) * 100, 1),
            "over_budget": sent_chars > self.max_chars,
            "full_history_preserved": True,
            "retrieval_available": bool(self._retrieved or re.search(r"id=[a-f0-9]{20}", encoded(view))),
            "evicted_images": evicted_images,
        }
        return view
