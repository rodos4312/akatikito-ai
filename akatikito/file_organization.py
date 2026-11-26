"""Reviewed workspace organization: no overwrite, durable moves, verified rollback and undo."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from .editing import EditResult
from .fs_snapshot import MAX_ENTRIES, MAX_TOTAL, Budget, content, copy_tree, digest, exact_exists, snapshot
from .local_store import checked, load_json, save_json
from .process_lock import FileLock

CONTROL = {".git", ".agent", ".agent_backups", ".akatikito", ".venv", "venv", "node_modules", "__pycache__"}
DOCUMENTS = {
    ".md", ".txt", ".csv", ".json", ".pdf", ".docx", ".xlsx",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
    ".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mov", ".zip",
}
EMPTY = digest([{"path": "", "kind": "dir", "size": 0, "streams": []}])


def _id(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{32}", value):
        raise ValueError("Μη έγκυρο αναγνωριστικό οργάνωσης.")
    return value


def _overlap(a, b):
    a = a.casefold().rstrip("/")
    b = b.casefold().rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


class Organizer:
    def __init__(self, ws, scope="full"):
        self.ws = ws
        self.root = ws.root.resolve()
        self.scope = scope
        self.plans = self.root / ".agent/organizer/plans"
        self.store = self.root / ".agent_backups/organizer"
        self.lock = FileLock(self.root / ".agent/organizer.lock")

    def _policy(self, allow_undo=False):
        if os.name != "nt":
            raise PermissionError("Η ασφαλής οργάνωση αυτής της έκδοσης υποστηρίζεται στα Windows.")
        if getattr(self.ws, "update_validator", None):
            raise PermissionError("Οι μετακινήσεις δεν επιτρέπονται σε υποψήφιο αυτοαναβάθμισης.")
        guard = getattr(self.ws, "write_guard", None)
        if not allow_undo and getattr(getattr(guard, "__self__", None), "pending", False):
            raise PermissionError("Ολοκλήρωσε πρώτα την εκκρεμή διόρθωση ελέγχων.")

    def path(self, value, internal=False):
        if not isinstance(value, str) or not value or len(value) > 2000 or any(ord(c) < 32 for c in value):
            raise ValueError("Μη έγκυρη διαδρομή.")
        raw = Path(value)
        if ".." in raw.parts:
            raise PermissionError("Δεν επιτρέπεται έξοδος από το project.")
        path = raw if raw.is_absolute() else self.root / raw
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            raise PermissionError("Η οργάνωση περιορίζεται στο συγκεκριμένο project.") from None
        if rel == Path("."):
            raise PermissionError("Ο ριζικός φάκελος του project δεν μετακινείται.")
        for part in rel.parts:
            if (
                any(c in part for c in '<>:"|?*')
                or part.endswith((" ", "."))
                or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part, re.I)
            ):
                raise ValueError("Μη έγκυρο όνομα για Windows.")
            if not internal and part.casefold() in CONTROL:
                raise PermissionError("Προστατευμένος φάκελος της εφαρμογής/project.")
        checked(path)
        live = Path(__file__).resolve().parents[1]
        if not internal and (path == live or live in path.parents or path in live.parents):
            raise PermissionError("Ο κώδικας της εφαρμογής αλλάζει μόνο από την ελεγχόμενη αναβάθμιση.")
        if path.parent.exists() and path.parent.stat().st_dev != self.root.stat().st_dev:
            raise PermissionError("Δεν υποστηρίζεται μετακίνηση μεταξύ δίσκων.")
        return path

    def rel(self, path):
        return Path(path).relative_to(self.root).as_posix()

    def _canonical(self, path):
        current = self.root
        for part in path.relative_to(self.root).parts:
            checked(current)
            matches = [x.name for x in os.scandir(current) if x.name.casefold() == part.casefold()]
            if len(matches) != 1:
                raise ValueError("Η πηγή λείπει ή έχει αμφίσημο όνομα.")
            current = current / matches[0]
        return self.path(self.rel(current))

    def _scope(self, operations):
        if self.scope == "full":
            return
        for op in operations:
            for snap in [op.get("snapshot")]:
                if snap:
                    for row in snap["entries"]:
                        if row["kind"] == "file":
                            name = Path(op["source"]) / row["path"]
                            if name.suffix.casefold() not in DOCUMENTS:
                                raise PermissionError("Η οργάνωση κώδικα/εκτελέσιμων χρειάζεται Κώδικας ή Debug.")
            if op.get("destination") and op["action"] != "mkdir" and op.get("snapshot", {}).get("entries", [{}])[0].get("kind") == "file":
                if Path(op["destination"]).suffix.casefold() not in DOCUMENTS:
                    raise PermissionError("Ο Βοηθός οργανώνει μόνο έγγραφα και πολυμέσα.")

    def _validate_single_op(self, op, budget, prepare: bool, sources: list, destinations: list, mkdirs: set) -> tuple[int, int]:
        if not isinstance(op, dict) or op.get("action") not in ("move", "copy", "mkdir", "trash"):
            raise ValueError("Ενέργεια move, copy, mkdir ή trash.")
        action = op["action"]
        source = None
        added_bytes = 0
        added_entries = 0
        if action != "mkdir":
            source = self.path(op.get("source"))
            if prepare:
                source = self._canonical(source)
                op["source"] = self.rel(source)
                op["snapshot"] = snapshot(source, budget)
            expected = op.get("snapshot")
            if not expected or not exact_exists(source):
                raise ValueError("Η πηγή δεν υπάρχει πλέον.")
            sources.append(op["source"])
            added_bytes = expected["bytes"]
            added_entries = len(expected["entries"])
        if action != "trash":
            dest = self.path(op.get("destination"))
            if prepare:
                if dest.parent.exists():
                    parent = self.root if dest.parent == self.root else self._canonical(dest.parent)
                    dest = parent / dest.name
                op["destination"] = self.rel(dest)
            if source and op["source"] == op["destination"]:
                raise ValueError("Η πηγή και ο προορισμός είναι ίδιοι.")
            case_only = bool(source and action == "move" and op["source"].casefold() == op["destination"].casefold())
            if dest.exists() and not case_only:
                raise FileExistsError("Ο προορισμός υπάρχει ήδη: " + op["destination"])
            parent = self.rel(dest.parent)
            if not dest.parent.is_dir() and parent not in mkdirs:
                raise ValueError("Δημιούργησε πρώτα τον φάκελο προορισμού στο ίδιο σχέδιο.")
            destinations.append((op["destination"], action))
            if action == "mkdir":
                mkdirs.add(op["destination"])
        return added_bytes, added_entries

    @staticmethod
    def _check_op_conflicts(ops: list, sources: list, destinations: list) -> None:
        for i, a in enumerate(sources):
            if any(_overlap(a, b) for b in sources[i + 1 :]):
                raise ValueError("Επικαλυπτόμενες ή διπλές πηγές.")
        for i, (a, kind) in enumerate(destinations):
            for b, other in destinations[i + 1 :]:
                if a.casefold() == b.casefold() or (_overlap(a, b) and kind != "mkdir" and other != "mkdir"):
                    raise ValueError("Σύγκρουση προορισμών.")
            for source in sources:
                if _overlap(a, source) and not (
                    a.casefold() == source.casefold()
                    and any(o["action"] == "move" and o.get("source") == source and o.get("destination") == a for o in ops)
                ):
                    raise ValueError("Πηγή και προορισμός επικαλύπτονται.")

    def _validate_ops(self, ops, budget, prepare=False):
        if not isinstance(ops, list) or not 1 <= len(ops) <= 32:
            raise ValueError("Χρειάζονται 1–32 σαφείς ενέργειες.")
        sources, destinations, mkdirs = [], [], set()
        total = count = 0
        for op in ops:
            b, c = self._validate_single_op(op, budget, prepare, sources, destinations, mkdirs)
            total += b
            count += c
        if total > MAX_TOTAL or count > MAX_ENTRIES:
            raise ValueError("Το σύνολο ξεπερνά τα όρια οργάνωσης.")
        self._check_op_conflicts(ops, sources, destinations)
        self._scope(ops)

    def preview(self, operations):
        self._policy()
        budget = Budget(getattr(self.ws, "search_stopped", lambda: False))
        if not isinstance(operations, list):
            raise ValueError("Χρειάζεται λίστα ενεργειών.")
        ops = []
        for original in operations:
            if not isinstance(original, dict) or set(original) - {"action", "source", "destination"}:
                raise ValueError("Μη έγκυρα πεδία ενέργειας.")
            ops.append(dict(original))
        expanded = []
        planned = set()
        for op in ops:
            if op.get("action") == "mkdir":
                target = self.path(op.get("destination"))
                for parent in reversed(target.parents):
                    if parent == self.root or self.root not in parent.parents:
                        continue
                    rel = self.rel(parent)
                    if not parent.exists() and rel.casefold() not in planned:
                        expanded.append({"action": "mkdir", "destination": rel})
                        planned.add(rel.casefold())
                planned.add(op["destination"].casefold())
            expanded.append(op)
        ops = expanded
        with self.lock:
            self._validate_ops(ops, budget, True)
            signature = digest([str(self.root), self.scope, ops])
            index_path = checked(self.plans.parent / "pending-index.json")
            index = load_json(index_path, dict)
            if not isinstance(index, dict):
                raise ValueError("Μη έγκυρο ευρετήριο σχεδίων· δεν αντικαταστάθηκε.")
            prior = index.get(signature)
            if prior and time.time() < prior["expires"] and not self._folder(prior["id"]).exists():
                return self.summary(self._plan(prior["id"]))
            plan = {"id": uuid.uuid4().hex, "root": str(self.root), "created": time.time(), "scope": self.scope, "operations": ops}
            plan["review"] = digest(plan)
            raw = json.dumps(plan, ensure_ascii=False).encode()
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Πολύ μεγάλο σχέδιο. Χώρισε την εργασία.")
            save_json(checked(self.plans / (plan["id"] + ".json")), plan)
            index = {k: v for k, v in index.items() if isinstance(v, dict) and v.get("expires", 0) > time.time()}
            if len(index) >= 100:
                raise ValueError("Υπάρχουν 100 ενεργά σχέδια. Ολοκλήρωσε πρώτα τα εκκρεμή.")
            index[signature] = {"id": plan["id"], "expires": plan["created"] + 900}
            save_json(index_path, index)
        return self.summary(plan)

    def summary(self, plan):
        rows = []
        for op in plan["operations"]:
            snap = op.get("snapshot") or {}
            rows.append(
                {
                    "action": op["action"],
                    "source": op.get("source", ""),
                    "destination": op.get("destination", ""),
                    "files": snap.get("files", 0),
                    "bytes": snap.get("bytes", 0),
                    "kind": (snap.get("entries") or [{"kind": "dir"}])[0]["kind"],
                }
            )
        return {"id": plan["id"], "review": plan["review"], "operations": rows, "project": str(self.root), "expires": plan["created"] + 900}

    def _plan(self, id, expected=None):
        plan = load_json(checked(self.plans / (_id(id) + ".json")), dict, limit=8 * 1024 * 1024)
        proof = dict(plan)
        review = proof.pop("review", None)
        if not plan or plan.get("id") != id or plan.get("root") != str(self.root) or digest(proof) != review or (expected is not None and expected != review):
            raise ValueError("Το σχέδιο άλλαξε ή δεν ανήκει στο project.")
        for op in plan["operations"]:
            for key in ("source", "destination"):
                if key in op:
                    self.path(op[key])
        self._scope(plan["operations"])
        return plan

    def _folder(self, id):
        return checked(self.store / _id(id))

    def _pairs(self, plan):
        result = []
        folder = self._folder(plan["id"])
        for n, op in enumerate(plan["operations"]):
            stage = self.rel(folder / "staging" / str(n))
            source = op.get("source")
            dest = op.get("destination")
            if op["action"] == "move":
                result.extend([(source, stage), (stage, dest)])
            elif op["action"] == "trash":
                result.append((source, self.rel(folder / "trash" / str(n))))
            else:
                result.append((stage, dest))
        return result

    def _save(self, journal):
        save_json(checked(self._folder(journal["id"]) / "journal.json"), journal)

    def _load(self, id):
        return load_json(checked(self._folder(id) / "journal.json"), dict, limit=8 * 1024 * 1024)

    def _guard(self, plan):
        guard = getattr(self.ws, "write_guard", None)
        if not guard:
            return
        for op in plan["operations"]:
            marker = "[Οργάνωση χωρίς αλλαγή περιεχομένου]"
            for key in ("source", "destination"):
                if op.get(key):
                    guard(self.path(op[key]), marker, marker)

    def _watch(self, paths, budget):
        roots = []
        for name in sorted(set(paths), key=lambda p: (len(Path(p).parts), p)):
            if not any(name == p or name.startswith(p + "/") for p in roots):
                roots.append(name)
        return {name: ((snapshot(self.path(name, True), budget) or {}).get("version")) for name in roots}

    def _effective(self, plan, journal, budget):
        allowed = self._pairs(plan)
        moves = list(journal.get("moves", []))
        if len(moves) > len(allowed) or any((m.get("source"), m.get("destination")) != allowed[i] for i, m in enumerate(moves)):
            raise ValueError("Μη έγκυρο ιστορικό μετακινήσεων.")
        pending = journal.get("pending")
        if pending and journal.get("phase") == "apply":
            pair = (pending.get("source"), pending.get("destination"))
            if len(moves) >= len(allowed) or pair != allowed[len(moves)]:
                raise ValueError("Μη έγκυρη ενδιάμεση κίνηση.")
            source = snapshot(self.path(pair[0], True), budget)
            dest = snapshot(self.path(pair[1], True), budget)
            if source is None and dest and dest["identity"] == pending.get("identity"):
                moves.append(pending)
            elif source and source["identity"] == pending.get("identity") and (dest is None or dest["identity"] != pending.get("identity")):
                pass
            else:
                raise ValueError("Αμφίσημη κατάσταση διακοπής. Διατήρησε τα αντίγραφα και έλεγξε τις διαδρομές.")
        return moves

    def _relocate(self, source, dest, identity, expected_content, budget):
        budget.check()
        src = self.path(source, True)
        dst = self.path(dest, True)
        actual = snapshot(src, budget)
        if not actual or actual["identity"] != identity or actual["content"] != expected_content:
            raise ValueError("Το αρχείο άλλαξε πριν από τη μετακίνηση: " + source)
        if dst.exists():
            raise FileExistsError("Ο προορισμός εμφανίστηκε ενδιάμεσα: " + dest)
        checked(src)
        checked(dst)
        if src.stat().st_dev != dst.parent.stat().st_dev:
            raise PermissionError("Διαφορετικός δίσκος προορισμού.")
        os.rename(src, dst)
        moved = snapshot(dst, budget)
        if not moved or moved["identity"] != identity or moved["content"] != expected_content:
            raise ValueError("Η μετακίνηση χρειάζεται έλεγχο ανάκτησης.")

    def _virtual_reverse(self, moves, budget):
        paths = {p for m in moves for p in (m["source"], m["destination"])}
        nodes = {}
        for name in sorted(paths, key=lambda x: len(Path(x).parts)):
            snap = snapshot(self.path(name, True), budget)
            if snap:
                for row in snap["entries"]:
                    nodes[name + ("/" + row["path"] if row["path"] else "")] = dict(row)

        def payload(name):
            if name not in nodes:
                return None
            rows = []
            for key, row in nodes.items():
                if key == name or key.startswith(name + "/"):
                    rows.append({**row, "path": key[len(name) :].lstrip("/")})
            rows.sort(key=lambda x: x["path"])
            return nodes[name]["identity"], digest(content(rows))

        for move in reversed(moves):
            source, dest = move["source"], move["destination"]
            a, b = payload(source), payload(dest)
            expected = (move["identity"], move["content"])
            if a == expected and b is None:
                continue
            if b != expected or a is not None:
                raise ValueError("Η επαναφορά συγκρούεται με νεότερα ή άγνωστα αρχεία: " + source + " / " + dest)
            subtree = {key: row for key, row in nodes.items() if key == dest or key.startswith(dest + "/")}
            for key in subtree:
                del nodes[key]
            for key, row in subtree.items():
                nodes[source + key[len(dest) :]] = row

    def _rollback(self, moves):
        errors = []
        budget = Budget()
        for move in reversed(moves):
            try:
                source = snapshot(self.path(move["source"], True), budget)
                dest = snapshot(self.path(move["destination"], True), budget)
                if source and source["identity"] == move["identity"] and source["content"] == move["content"] and dest is None:
                    continue
                self._relocate(move["destination"], move["source"], move["identity"], move["content"], budget)
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def _result(self, plan, changed=True, undo=False, recovery=False, message=""):
        paths = []
        changes = []
        for op in plan["operations"]:
            source, dest = op.get("source"), op.get("destination")
            action = op["action"]
            changes.append({"action": action, "source": source or "", "destination": dest or "", "undo": undo})
            if source and action != "copy":
                paths.append(source)
            if dest:
                paths.append(dest)
            for row in (op.get("snapshot") or {}).get("entries", []):
                if row["kind"] != "file":
                    continue
                if source and action != "copy":
                    paths.append(source + ("/" + row["path"] if row["path"] else ""))
                if dest:
                    paths.append(dest + ("/" + row["path"] if row["path"] else ""))
        result = EditResult(
            message or ("ΕΓΙΝΕ: " + ("επαναφέρθηκε" if undo else "εφαρμόστηκε") + " η οργάνωση " + plan["id"] + "."),
            tuple(dict.fromkeys(paths)) if changed else (),
            recovery_required=recovery,
            recovery_paths=(str(self._folder(plan["id"])),),
        )
        result.organization_changes = changes if changed else []
        result.transaction_id = plan["id"]
        return result

    def _preflight_apply(self, id, expected, approve):
        plan = self._plan(id, expected)
        old = self._load(id)
        if old:
            if old.get("state") == "complete":
                return plan, self._result(plan, False, message="Η οργάνωση εφαρμόστηκε παλαιότερα· δεν εκτελέστηκε ξανά."), None
            raise ValueError("Υπάρχει ήδη ιστορικό γι’ αυτό το σχέδιο. Έλεγξε την επαναφορά ή δημιούργησε νέο σχέδιο.")
        if time.time() > plan["created"] + 900:
            raise ValueError("Η προεπισκόπηση έληξε. Δημιούργησε νέο σχέδιο.")
        if any(row["id"] != id and row["state"] in ("preparing", "applying", "undoing", "recovery_required") for row in self.history(limit=None)):
            raise ValueError("Υπάρχει εκκρεμής ανάκτηση αρχείων. Έλεγξε πρώτα το ιστορικό.")
        if not approve or not approve(self.summary(plan)):
            raise PermissionError("[OWNER_DENIED] Η οργάνωση δεν εγκρίθηκε.")
        budget = Budget(getattr(self.ws, "search_stopped", lambda: False))
        self._validate_ops(plan["operations"], budget)
        for op in plan["operations"]:
            if op.get("source") and snapshot(self.path(op["source"]), budget)["version"] != op["snapshot"]["version"]:
                raise ValueError("Η πηγή άλλαξε μετά την προεπισκόπηση: " + op["source"])
        if time.time() > plan["created"] + 900:
            raise ValueError("Η προεπισκόπηση έληξε κατά την έγκριση.")
        self._guard(plan)
        needed = sum((op.get("snapshot") or {}).get("bytes", 0) * (2 if op["action"] == "copy" else 1) for op in plan["operations"]) + 8 * 1024 * 1024
        if shutil.disk_usage(self.root).free < needed:
            raise ValueError("Δεν υπάρχει αρκετός ελεύθερος χώρος για ασφαλή αντίγραφα.")
        return plan, None, budget

    def _run_apply_ops(self, plan, folder: Path, journal: dict, budget) -> None:
        for n, op in enumerate(plan["operations"]):
            if op.get("source"):
                copy_tree(self.path(op["source"]), folder / "backup" / str(n), op["snapshot"], budget)
        for op in plan["operations"]:
            if op.get("source") and snapshot(self.path(op["source"]), budget)["version"] != op["snapshot"]["version"]:
                raise ValueError("Η πηγή άλλαξε κατά την προετοιμασία.")
        journal["state"] = "applying"
        self._save(journal)
        for n, op in enumerate(plan["operations"]):
            stage = folder / "staging" / str(n)
            action = op["action"]
            if action == "copy":
                copy_tree(folder / "backup" / str(n), stage, op["snapshot"], budget)
            elif action == "mkdir":
                stage.mkdir()
            pairs = (
                [(op["source"], self.rel(stage)), (self.rel(stage), op["destination"])]
                if action == "move"
                else [(op["source"], self.rel(folder / "trash" / str(n)))]
                if action == "trash"
                else [(self.rel(stage), op["destination"])]
            )
            for source, dest in pairs:
                snap = snapshot(self.path(source, True), budget)
                wanted = op["snapshot"]["content"] if action != "mkdir" else EMPTY
                if not snap or snap["content"] != wanted or (source == op.get("source") and snap["version"] != op["snapshot"]["version"]):
                    raise ValueError("Η πηγή άλλαξε πριν από την εφαρμογή.")
                move = {"source": source, "destination": dest, "identity": snap["identity"], "content": snap["content"]}
                journal["pending"] = move
                self._save(journal)
                self._relocate(source, dest, move["identity"], move["content"], budget)
                journal["moves"].append(move)
                journal["pending"] = None
                self._save(journal)

    def _recover_apply_failure(self, plan, journal: dict, failure: Exception):
        uncertain = []
        try:
            moves = self._effective(plan, journal, Budget())
        except Exception as recovery_error:  # quality: allow BLE001 — capture any journal recovery failure for safe rollback
            moves = journal["moves"]
            uncertain.append(str(recovery_error))
        errors = self._rollback(moves) + uncertain
        journal.update(
            state="recovery_required" if errors else "rolled_back",
            error=str(failure),
            rollback_errors=errors,
            moves=moves,
            pending=journal.get("pending") if uncertain else None,
        )
        try:
            self._save(journal)
        except OSError:
            errors.append("Δεν αποθηκεύτηκε η τελική κατάσταση ανάκτησης.")
        if errors:
            return self._result(plan, recovery=True, message="ΣΦΑΛΜΑ: ΑΤΕΛΗΣ ΕΠΑΝΑΦΟΡΑ. " + str(failure) + " · " + "; ".join(errors))
        raise ValueError("Η οργάνωση απέτυχε και οι μετακινήσεις αναιρέθηκαν: " + str(failure)) from failure

    def apply(self, id, expected, approve):
        self._policy()
        with self.lock:
            plan, existing_result, budget = self._preflight_apply(id, expected, approve)
            if existing_result is not None:
                return existing_result
            folder = self._folder(id)
            if folder.exists():
                raise ValueError("Η προετοιμασία αυτού του σχεδίου είχε διακοπεί. Δημιούργησε νέα προεπισκόπηση.")
            for name in ("backup", "staging", "trash"):
                checked(folder / name).mkdir(parents=True, exist_ok=False)
            journal = {
                "id": id, "review": plan["review"], "created": plan["created"],
                "operations": self.summary(plan)["operations"], "state": "preparing",
                "phase": "apply", "moves": [], "pending": None,
            }
            self._save(journal)
            try:
                self._run_apply_ops(plan, folder, journal, budget)
                journal["watch"] = self._watch([p for m in journal["moves"] for p in (m["source"], m["destination"])], budget)
                journal.update(state="complete", finished=time.time())
                self._save(journal)
                return self._result(plan)
            except Exception as failure:  # quality: allow BLE001 — any failure triggers verified journal rollback
                return self._recover_apply_failure(plan, journal, failure)

    def undo_preview(self, id):
        self._policy(allow_undo=True)
        budget = Budget()
        plan = self._plan(id)
        journal = self._load(id)
        if not journal:
            raise ValueError("Δεν υπάρχει εκτέλεση για επαναφορά.")
        if journal.get("state") == "undone":
            raise ValueError("Η οργάνωση έχει ήδη αναιρεθεί.")
        moves = self._effective(plan, journal, budget)
        if journal.get("state") == "complete" and self._watch(journal.get("watch", {}), budget) != journal.get("watch"):
            raise ValueError("Υπάρχουν νεότερες αλλαγές. Η αυτόματη επαναφορά δεν θα τις αντικαταστήσει.")
        self._virtual_reverse(moves, budget)
        watch = self._watch([p for m in moves for p in (m["source"], m["destination"])], budget)
        return {
            "id": id,
            "review": digest([journal, watch]),
            "operations": self.summary(plan)["operations"],
            "project": str(self.root),
            "state": journal["state"],
        }

    def undo(self, id, expected, approve):
        with self.lock:
            preview = self.undo_preview(id)
            if preview["review"] != expected:
                raise ValueError("Η προεπισκόπηση επαναφοράς άλλαξε.")
            if not approve or not approve(preview):
                raise PermissionError("[OWNER_DENIED] Η επαναφορά δεν εγκρίθηκε.")
            if self.undo_preview(id)["review"] != expected:
                raise ValueError("Τα αρχεία άλλαξαν κατά την έγκριση.")
            plan = self._plan(id)
            journal = self._load(id)
            moves = self._effective(plan, journal, Budget())
            undo_moves = []
            journal.update(phase="undo", state="undoing", moves=moves, pending=None)
            self._save(journal)
            try:
                for move in reversed(moves):
                    source = snapshot(self.path(move["source"], True))
                    dest = snapshot(self.path(move["destination"], True))
                    if source and source["identity"] == move["identity"] and source["content"] == move["content"] and dest is None:
                        continue
                    reverse = {**move, "source": move["destination"], "destination": move["source"]}
                    journal["pending"] = reverse
                    self._save(journal)
                    self._relocate(reverse["source"], reverse["destination"], reverse["identity"], reverse["content"], Budget(getattr(self.ws, "search_stopped", lambda: False)))
                    undo_moves.append(reverse)
                    journal["pending"] = None
                    self._save(journal)
                journal.update(state="undone", finished=time.time())
                self._save(journal)
                return self._result(plan, changed=bool(undo_moves), undo=True, message="" if undo_moves else "Η κατάσταση ήταν ήδη επαναφερμένη· δεν μετακινήθηκαν αρχεία.")
            except Exception as failure:
                if journal.get("pending"):
                    pending = journal["pending"]
                    a = snapshot(self.path(pending["source"], True))
                    b = snapshot(self.path(pending["destination"], True))
                    if a is None and b and b["identity"] == pending["identity"]:
                        undo_moves.append(pending)
                errors = self._rollback(undo_moves)
                journal.update(state="recovery_required", pending=None, error=str(failure), rollback_errors=errors)
                self._save(journal)
                return self._result(plan, recovery=True, message="ΣΦΑΛΜΑ: η επαναφορά διακόπηκε. " + str(failure) + " · " + "; ".join(errors))

    def history(self, limit=100):
        rows = []
        checked(self.store)
        if self.store.exists():
            for entry in sorted(self.store.iterdir(), key=lambda p: p.name):
                if not re.fullmatch("[a-f0-9]{32}", entry.name):
                    continue
                checked(entry)
                journal = self._load(entry.name)
                if journal:
                    rows.append(
                        {
                            "id": entry.name,
                            "state": journal.get("state"),
                            "error": journal.get("error", ""),
                            "backup": str(entry),
                            "at": journal.get("finished", journal.get("created", 0)),
                            "operations": journal.get("operations", []),
                        }
                    )
        rows.sort(key=lambda row: row["at"])
        return rows[-limit:] if limit is not None else rows
