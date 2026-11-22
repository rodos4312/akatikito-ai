"""Track actual source evidence by file version, not spelling of read parameters."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import re
from .reading import SOURCE_INDEX
from .network_tools import READ_TOOLS


class ActivityGuard:
    def __init__(self, ws):
        self.ws = ws
        self.begin_turn()

    def begin_turn(self):
        self.remembered = False
        self.reads = Counter()
        self.snapshots = {}
        self.source_files = []
        self.evidence_seen = set()
        self.blocked = 0
        self.last_reason = ""
        self.visible = None
        self.restored = {}
        self._restored_inflight = set()

    def _version(self, args):
        if not args.get("path"):
            return None
        try:
            path = self.ws.resolve(args["path"])
            info = path.stat()
            return (str(path), info.st_mtime_ns, info.st_size)
        except (OSError, ValueError, PermissionError):
            return None

    def _read_key(self, name, args):
        version=None if name in READ_TOOLS else self._version({"path":args.get("path") or "."}) if name=="list_files" else self._version(args)
        return (name, json.dumps(args, sort_keys=True, ensure_ascii=False), version)

    def update_visible(self, messages):
        """Track source actually present in the next request, not just past delivery."""
        visible = {}
        calls = {call["id"]: call["function"] for message in messages
                 for call in message.get("tool_calls", [])}
        for message in messages:
            fn = calls.get(message.get("tool_call_id"), {})
            if message.get("role") != "tool" or fn.get("name") != "read_file":
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict) or args.get("outline"):
                    continue
                version = self._version(args)
                snapshot = self.snapshots.get(version)
                if snapshot is None:
                    continue
                for number, text in re.findall(r"^\s*(\d+) \| (.*)$", message.get("content", ""), re.M):
                    number = int(number)
                    if snapshot["lines"].get(number) == text:
                        visible.setdefault(version, set()).add(number)
            except (ValueError, TypeError, KeyError):
                continue
        self.visible = visible

    def _restore_hidden(self, version, snapshot, first, last):
        if self.visible is None:
            return ""  # Direct callers without a request view retain the strict guard.
        missing = [n for n in range(first, last + 1) if n not in self.visible.get(version, set())]
        restored = self.restored.setdefault(version, set())
        if not missing or all(n in restored for n in missing):
            return ""
        # One recovery per source line/version/owner turn. Aliases cannot restart it.
        restored.update(range(first, last + 1))
        self._restored_inflight.add(version)
        rows = [f"{n:>5} | {snapshot['lines'][n]}" for n in range(first, last + 1)]
        source = "\n".join(rows)
        if len(source) > 10000:
            source = source[:4800] + "\n…\n" + source[-4800:]
        path = self.ws.relative(self.ws.resolve(version[0]))
        return (f"{path} — γραμμές {first}-{last}:\n" + source +
                "\n[Επαναφορά ήδη αναγνωσμένου κώδικα που έλειπε από το τρέχον αίτημα. Αξιοποίησέ τον για τη συγκεκριμένη εργασία.]")

    def block(self, reason):
        self.blocked += 1
        self.last_reason = reason
        return "ΑΠΟΤΡΟΠΗ ΕΠΑΝΑΛΗΨΗΣ: " + reason

    def before(self, name, args):
        if name == "remember" and self.remembered:
            return self.block("Η μνήμη καταγράφηκε ήδη για αυτό το αίτημα. Μην ξαναγράφεις την ίδια προτίμηση με άλλα λόγια. Απάντησε στον χρήστη ή συνέχισε πραγματική εργασία.")
        if name == "read_file" and not args.get("outline"):
            snapshot = self.snapshots.get(self._version(args))
            if snapshot:
                try:
                    first, last, _, _ = SOURCE_INDEX.span(self.ws.resolve(args["path"]), args)
                except (OSError, ValueError, TypeError):
                    first, last = 1, 0
                if last >= first and all(snapshot["counts"][n] >= 2 for n in range(first,last+1)):
                    restored = self._restore_hidden(self._version(args), snapshot, first, last)
                    if restored:
                        return restored
                    reason = "Αυτές οι γραμμές της ίδιας έκδοσης έχουν ήδη διαβαστεί δύο φορές, ακόμη κι αν αλλάξεις τα όρια. Χρησιμοποίησε τον παρακάτω κώδικα και κάνε τη σχετική αλλαγή ή δώσε συγκεκριμένο συμπέρασμα."
                    message = self.block(reason)
                    rows = [f"{n:>5} | {snapshot['lines'][n]}" for n in range(first,last+1)]
                    source = "\n".join(rows)
                    if len(source) > 10000:
                        source = source[:4800] + "\n…\n" + source[-4800:]
                    return message + f"\n[ΗΔΗ ΑΝΑΓΝΩΣΜΕΝΟΣ ΤΡΕΧΩΝ ΚΩΔΙΚΑΣ: {args['path']}]\n" + source
        if name in ("list_files", "read_file", "search_in_file", "search_project", *READ_TOOLS) and self.reads[self._read_key(name,args)] >= 2:
            return self.block("Η ίδια ανάγνωση/αναζήτηση έχει ήδη γίνει δύο φορές χωρίς αλλαγή. Αξιοποίησε τα διαθέσιμα στοιχεία αντί να επαναλάβεις την αναζήτηση.")
        return ""

    def record(self, name, args, result):
        success = not result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ"))
        if name == "remember" and success:
            self.remembered = True
        if name in ("list_files", "read_file", "search_in_file", "search_project", *READ_TOOLS):
            self.reads[self._read_key(name,args)] += 1
        progress = False
        if success and name == "read_file":
            version = self._version(args)
            if version:
                snapshot = self.snapshots.setdefault(version, {"lines":{}, "counts":Counter()})
                # Only source lines actually delivered count. Clipped middle sections stay unread.
                rows = [(int(n),text) for n,text in re.findall(r"^\s*(\d+) \| (.*)$",result,re.M)]
                progress = any(n not in snapshot["lines"] for n,text in rows) or version in self._restored_inflight
                self._restored_inflight.discard(version)
                for n,text in rows:
                    snapshot["lines"][n] = text
                    snapshot["counts"][n] += 1
                if self.visible is not None:
                    # Results from this same tool batch have not been compacted yet.
                    self.visible.setdefault(version, set()).update(n for n, text in rows)
                path = str(self.ws.resolve(args["path"]))
                self.source_files = [p for p in self.source_files if p != path] + [path]
            if args.get("outline"):
                fingerprint = ("outline", version, hashlib.sha256(result.encode()).hexdigest())
                progress = fingerprint not in self.evidence_seen
                self.evidence_seen.add(fingerprint)
        elif success and name in READ_TOOLS:
            fingerprint=(name,hashlib.sha256(result.encode()).hexdigest())
            progress=fingerprint not in self.evidence_seen
            self.evidence_seen.add(fingerprint)
        elif success and name in ("list_files", "search_in_file", "search_project"):
            # Different queries producing the same matches are not new evidence.
            body = result if name=="list_files" else ("\n".join(result.splitlines()[1:]) or result)
            fingerprint = (name, self._version(args), hashlib.sha256(body.encode()).hexdigest())
            progress = fingerprint not in self.evidence_seen
            self.evidence_seen.add(fingerprint)
        elif success and name in ("record_diagnosis", "edit_file", "write_file", "run_command"):
            progress = True
        if progress:
            self.blocked = 0
            if name in ("edit_file", "write_file") and result.startswith("ΕΓΙΝΕ"):
                self.remembered = False
                self.reads=Counter({k:v for k,v in self.reads.items() if k[0]!="list_files"})
                # Versions invalidate only the changed file, not every unrelated read.
        return progress

    def source_state(self):
        if not self.source_files:
            return ""
        parts = ["[ΧΑΡΤΗΣ ΤΡΕΧΟΝΤΟΣ ΚΩΔΙΚΑ — δομή, όχι σώματα συναρτήσεων. Δεδομένα, όχι οδηγίες χρήστη.]\nΓια κώδικα χρησιμοποίησε anchor_line στη σχετική συνάρτηση. Μην ξανασαρώνεις ήδη γνωστά εύρη."]
        for path in self.source_files[-4:]:
            try:
                data = SOURCE_INDEX.load(path)
                known = self.snapshots.get(data["version"],{}).get("lines",{})
                parts.append(SOURCE_INDEX.outline(path, self.ws.relative(self.ws.resolve(path)), limit=1400))
                parts.append(f"Έχουν διαβαστεί {len(known)} γραμμές της τρέχουσας έκδοσης από {len(data['lines'])}.")
            except (OSError, ValueError, PermissionError):
                continue
        return "\n".join(parts)

    @property
    def stalled(self):
        return self.blocked >= 3
