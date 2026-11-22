"""Facts, explicit hypotheses and lossless revisions. The owner approves proposals."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
import uuid

from .local_store import LOCK, checked, load_json, save_json

CONSOLIDATE_SECONDS = 7 * 86400


def topic_key(text):
    text = unicodedata.normalize("NFD", str(text).casefold())
    return " ".join(re.findall(r"\w+", "".join(c for c in text if not unicodedata.combining(c))))


def text_key(text):
    # Preserve case and punctuation in facts such as identifiers, paths and codes.
    return " ".join(unicodedata.normalize("NFC", str(text)).split())


def _guard(*texts):
    from . import memory_guard
    for text in texts:
        reason = memory_guard.scan(text)
        if reason:
            raise ValueError(memory_guard.refusal(reason))


def _empty():
    return {"version": 1, "entries": [], "proposals": [], "revisions": [],
            "legacy": {}, "last_consolidated": 0}


class Profile:
    def __init__(self, directory=None):
        if directory is None:
            from . import memory
            directory = memory.DIR
        self.directory = Path(directory)
        self.path = self.directory / "profile.json"

    def _load(self):
        data = load_json(self.path, _empty, limit=16_000_000)
        if (not isinstance(data, dict) or data.get("version") != 1
                or not all(isinstance(data.get(k), list) for k in ("entries", "proposals", "revisions"))
                or not isinstance(data.get("legacy"), dict)):
            raise ValueError("Μη έγκυρη δομή profile.json· διατηρήθηκε χωρίς αλλαγές.")
        for row in data["entries"] + data["proposals"]:
            if (not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("id", "topic", "text"))
                    or row.get("kind") not in ("fact", "belief")
                    or row.get("status") not in ("confirmed", "unconfirmed")):
                raise ValueError("Μη έγκυρη εγγραφή προφίλ· διατηρήθηκε χωρίς αλλαγές.")
        if not all(isinstance(r,dict) and isinstance(r.get("action"),str) for r in data["revisions"]):
            raise ValueError("Μη έγκυρο ιστορικό αναθεωρήσεων· διατηρήθηκε χωρίς αλλαγές.")
        for row in data["proposals"]:
            if (not isinstance(row.get("replaces"),list) or not isinstance(row.get("before"),list)
                    or not all(isinstance(i,str) for i in row["replaces"])):
                raise ValueError("Μη έγκυρη πρόταση μνήμης· διατηρήθηκε χωρίς αλλαγές.")
        return data

    def _save(self, data):
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 16_000_000:
            raise ValueError("Η μνήμη έφτασε τα 16 MB. Δεν αφαιρέθηκε κανένα στοιχείο.")
        save_json(self.path, data)

    def _legacy(self, data):
        path = checked(self.directory / "USER.md")
        if not path.is_file():
            return False
        raw = path.read_bytes()
        signature = hashlib.sha256(raw).hexdigest()
        if data["legacy"].get("USER.md") == signature:
            return False
        text = raw.decode("utf-8-sig")
        known = {text_key(row["text"]) for row in data["entries"]}
        # Appending to USER.md later must not resurrect a deliberately forgotten note.
        for revision in data["revisions"]:
            if revision.get("action") == "legacy_import":
                known.update(text_key(line.strip().lstrip("-* ").strip())
                             for line in revision.get("original","").splitlines())
        # Never erase the original or truncate it. Original lines are also archived.
        for number, line in enumerate(text.splitlines(), 1):
            note = line.strip().lstrip("-* ").strip()
            if not note or text_key(note) in known:
                continue
            data["entries"].append({
                "id": uuid.uuid4().hex, "topic": f"Παλιά σημείωση {number}",
                "text": note, "kind": "fact", "status": "confirmed",
                "source": "USER.md (ιστορική σημείωση)", "created": time.time(),
            })
            known.add(text_key(note))
        data["revisions"].append({"action": "legacy_import", "at": time.time(),
                                  "source": "USER.md", "sha256": signature, "original": text})
        data["legacy"]["USER.md"] = signature
        return True

    def load(self):
        with LOCK:
            data = self._load()
            if self._legacy(data):
                self._save(data)
            return data

    def propose(self, topic, text, kind="belief", status="unconfirmed", replaces=None):
        topic, text = str(topic).strip(), str(text).strip()
        if not topic or not text or len(topic) > 120 or len(text) > 4000:
            raise ValueError("Δώσε θέμα έως 120 και κείμενο έως 4000 χαρακτήρες.")
        if kind not in ("fact", "belief") or status not in ("confirmed", "unconfirmed"):
            raise ValueError("Χρειάζονται kind=fact/belief και status=confirmed/unconfirmed.")
        if kind == "fact" and status != "confirmed":
            raise ValueError("Ένα ανεπιβεβαίωτο στοιχείο δηλώνεται ως belief/unconfirmed.")
        _guard(topic, text)
        with LOCK:
            data = self.load()
            same = [r for r in data["entries"] if topic_key(r["topic"]) == topic_key(topic)]
            if not replaces and any(text_key(r["text"]) == text_key(text) and r["kind"] == kind and r["status"] == status
                   for r in same):
                return "Υπάρχει ήδη αυτή η μνήμη· δεν γράφτηκε ξανά."
            for row in data["proposals"]:
                if (row.get('target') != 'global' and topic_key(row["topic"]) == topic_key(topic) and text_key(row["text"]) == text_key(text)
                        and row["kind"] == kind and row["status"] == status):
                    return "Υπάρχει ήδη η ίδια πρόταση μνήμης· περιμένει έγκριση."
            replaced = sorted({r["id"] for r in same} | set(replaces or []))
            known = {r["id"] for r in data["entries"]}
            if not set(replaced) <= known:
                raise ValueError("Η αναθεώρηση αναφέρεται σε μνήμη που δεν υπάρχει πια.")
            # Οι προτάσεις γενικής μνήμης έχουν δικό τους κύκλο· ένα προφίλ δεν τις αντικαθιστά.
            pending = [r for r in data["proposals"] if topic_key(r["topic"]) == topic_key(topic)
                       and r.get("target") != "global"]
            for row in pending:
                data["revisions"].append({"action": "superseded_proposal", "at": time.time(), "before": row})
            data["proposals"] = [r for r in data["proposals"] if r not in pending]
            if not replaced and len(data["entries"]) + len(data["proposals"]) >= 500:
                raise ValueError("Η ενεργή μνήμη έφτασε τα 500 στοιχεία. Χρειάζεται τακτοποίηση, όχι διαγραφή ιστορικού.")
            data["proposals"].append({"id": uuid.uuid4().hex, "topic": same[0]["topic"] if same else topic,
                                      "text": text, "kind": kind, "status": status, "replaces": replaced,
                                      "source": "agent (πρόταση)", "created": time.time(),
                                      "before": [copy.deepcopy(r) for r in data["entries"] if r["id"] in replaced]})
            self._save(data)
        return "ΠΡΟΤΑΣΗ ΜΝΗΜΗΣ: " + topic + " — " + text + " (απαιτεί έγκριση χρήστη)."

    def propose_global(self, note):
        """Σημείωση για ΟΛΑ τα projects: δεν γράφεται στο MEMORY.md πριν την έγκριση του ιδιοκτήτη."""
        text = " ".join(str(note).split())
        if not text or len(text) > 4000:
            raise ValueError("Δώσε σημείωση έως 4000 χαρακτήρες.")
        _guard(text)
        with LOCK:
            from . import memory
            if memory.contains(text,self.directory):
                return 'Υπάρχει ήδη στη γενική μνήμη· δεν δημιουργήθηκε νέα πρόταση.'
            data = self.load()
            if any(r.get("target") == "global" and memory.note_key(r["text"]) == memory.note_key(text) for r in data["proposals"]):
                return "Υπάρχει ήδη η ίδια πρόταση γενικής μνήμης· περιμένει έγκριση."
            if len(data["entries"]) + len(data["proposals"]) >= 500:
                raise ValueError("Η ενεργή μνήμη έφτασε τα 500 στοιχεία. Χρειάζεται τακτοποίηση, όχι διαγραφή ιστορικού.")
            # Το target είναι μέρος της γραμμής, άρα καλύπτεται από το review_hash.
            data["proposals"].append({"id": uuid.uuid4().hex, "topic": text[:120], "text": text,
                                      "kind": "fact", "status": "confirmed", "replaces": [],
                                      "source": "agent (πρόταση)", "created": time.time(),
                                      "before": [], "target": "global"})
            self._save(data)
        return "ΠΡΟΤΑΣΗ ΓΕΝΙΚΗΣ ΜΝΗΜΗΣ (ισχύει για ΟΛΑ τα projects): " + text + " (απαιτεί έγκριση χρήστη)."

    @staticmethod
    def review_hash(row):
        return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()

    def answer(self, proposal_id, approve, edited_text=None, expected=None):
        with LOCK:
            global_written = False
            data = self.load()
            row = next((r for r in data["proposals"] if r["id"] == proposal_id), None)
            if row is None:
                raise ValueError("Η πρόταση δεν υπάρχει πια· ανανέωσε την προεπισκόπηση.")
            if expected is not None and expected != self.review_hash(row):
                raise ValueError("Η πρόταση άλλαξε μετά την προεπισκόπηση.")
            if approve:
                current = [r for r in data["entries"] if r["id"] in row["replaces"]]
                if current != row["before"]:
                    raise ValueError("Η προηγούμενη μνήμη άλλαξε· χρειάζεται νέα πρόταση.")
                accepted = {k: v for k, v in row.items() if k not in ("before", "replaces")}
                if edited_text is not None:
                    value = str(edited_text).strip()
                    if not value or len(value) > 4000:
                        raise ValueError("Η διορθωμένη μνήμη χρειάζεται 1–4000 χαρακτήρες.")
                    accepted["text"] = value
                # Ο ιδιοκτήτης μπορεί να επεξεργάστηκε το κείμενο· ελέγχεται ό,τι θα γραφτεί πραγματικά.
                _guard(accepted['topic'],accepted["text"])
                accepted["source"] = "owner-approved"
                if row.get("target") == "global":
                    from . import memory
                    result=memory.append("memory", accepted["text"],directory=self.directory)
                    if result.startswith(('ΑΠΟΡΡΙΨΗ','ΑΠΟΡΡΙΦΘΗΚΕ','ΣΦΑΛΜΑ','Κενή')):
                        raise ValueError(result)
                    global_written=True
                    data["revisions"].append({"action": "accept", "at": time.time(), "before": current, "after": accepted})
                else:
                    data["entries"] = [r for r in data["entries"] if r["id"] not in row["replaces"]]
                    data["entries"].append(accepted)
                    data["revisions"].append({"action": "revision" if current else "accept",
                                              "at": time.time(), "before": current, "after": accepted})
            else:
                data["revisions"].append({"action": "reject", "at": time.time(), "before": row})
            data["proposals"] = [r for r in data["proposals"] if r["id"] != proposal_id]
            try:
                self._save(data)
            except (OSError,ValueError) as exc:
                if global_written:
                    try:persisted=self._load()
                    except (OSError,ValueError):persisted=None
                    if persisted==data:
                        return {'ok':True,'note':'Η αποθήκευση μνήμης και ιστορικού επιβεβαιώθηκε παρά το σφάλμα εισόδου/εξόδου.'}
                    pending=persisted is not None and any(r['id']==proposal_id for r in persisted['proposals'])
                    detail=('Η εκκρεμής πρόταση διατηρήθηκε· επανάλαβε την έγκριση χωρίς διπλή εγγραφή.' if pending else
                            'Ανανέωσε την προεπισκόπηση για να ελέγξεις την κατάσταση της πρότασης.')
                    raise ValueError('Η γενική μνήμη αποθηκεύτηκε, αλλά δεν επιβεβαιώθηκε το ιστορικό έγκρισης. '+detail) from exc
                raise
        return {"ok": True}

    def forget(self, entry_id):
        with LOCK:
            data = self.load()
            row = next((r for r in data["entries"] if r["id"] == entry_id), None)
            if row is None:
                raise ValueError("Δεν υπάρχει αυτή η μνήμη.")
            data["entries"].remove(row)
            data["revisions"].append({"action": "forget", "at": time.time(), "before": row})
            self._save(data)
        return {"ok": True}

    def consolidate(self, due_only=False, now=None):
        now = time.time() if now is None else now
        with LOCK:
            data = self.load()
            if due_only and (not self.path.exists() or now - data.get("last_consolidated", 0) < CONSOLIDATE_SECONDS):
                return {"ok": True, "merged": 0, "due": False}
            seen, kept, merged = set(), [], []
            for row in data["entries"]:
                key = (topic_key(row["topic"]), text_key(row["text"]), row["kind"], row["status"])
                if key in seen:
                    merged.append(row)
                else:
                    kept.append(row)
                    seen.add(key)
            if merged:
                data["entries"] = kept
                data["revisions"].append({"action": "consolidate_exact_duplicates", "at": now, "before": merged})
            data["last_consolidated"] = now
            self._save(data)
            conflicts = {}
            for row in kept:
                conflicts.setdefault(topic_key(row["topic"]), []).append(row["id"])
            return {"ok": True, "merged": len(merged),
                    "conflicts": [ids for ids in conflicts.values() if len(ids) > 1],
                    "note": "Οι διαφορετικές τιμές διατηρήθηκαν· χρειάζονται εγκεκριμένη αναθεώρηση."}

    def prompt(self):
        data = self.load()
        rows = sorted(data["entries"], key=lambda r: (topic_key(r["topic"]), r["id"]))
        labels = {"confirmed": "επιβεβαιωμένο", "unconfirmed": "ΑΝΕΠΙΒΕΒΑΙΩΤΗ ΥΠΟΘΕΣΗ"}
        return "\n".join(f"- [{r['kind']} / {labels[r['status']]}] {r['topic']}: {r['text']}" for r in rows)

    def listing(self, offset=0, limit=30):
        data = self.load()
        rows = sorted(data["entries"], key=lambda r: (topic_key(r["topic"]), r["id"]))
        start = max(0, int(offset))
        limit = max(1, min(100, int(limit)))
        return {"entries": rows[start:start+limit], "total": len(rows),
                "next": start + limit if start + limit < len(rows) else None,
                "proposals": [{**r,"review_hash":self.review_hash(r)} for r in data["proposals"]],
                "revision_count": len(data["revisions"]),
                "last_consolidated": data["last_consolidated"]}
