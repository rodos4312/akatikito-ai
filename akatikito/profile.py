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


_STOPWORDS = frozenset({
    # Greek common stopwords (NEVER include negations like δεν/μην/όχι/ποτέ!)
    "ο", "η", "το", "οι", "τα", "του", "της", "των", "τον", "την", "τις", "τους",
    "ενα", "ενας", "μια", "και", "κι", "με", "σε", "απο", "για", "να", "θα", "οτι",
    "πως", "που", "στο", "στη", "στην", "στον", "στα", "στις", "στους", "εχει", "ειναι",
    "αλλα", "ομως", "πιο", "πολυ", "καθε", "αυτο", "αυτη", "αυτος",
    "χρηστης", "χρηστη", "θελει", "θελω", "προτιμα", "προτιμω", "πρεπει", "μπορει",
    # English common stopwords (NEVER include negations like not/no/never!)
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have",
    "has", "had", "but", "user", "wants", "prefer", "prefers", "should", "must",
})

_NEGATION_WORDS = frozenset({
    "δεν", "δε", "μην", "μη", "οχι", "ποτε", "χωρις", "αποφευγ", "απαγορευ",
    "not", "no", "never", "dont", "without", "avoid", "forbidden", "disable", "disabled",
})


def _has_negation(text: str) -> bool:
    norm = unicodedata.normalize("NFD", str(text).casefold())
    plain = "".join(c for c in norm if not unicodedata.combining(c))
    words = re.findall(r"[a-zα-ω0-9']+", plain)
    for w in words:
        clean = w.replace("'", "")
        if clean in _NEGATION_WORDS or any(clean.startswith(p) for p in ("αποφευγ", "απαγορευ")):
            return True
    return False


def _semantic_tokens(text: str) -> set[str]:
    norm = unicodedata.normalize("NFD", str(text).casefold())
    plain = "".join(c for c in norm if not unicodedata.combining(c))
    words = re.findall(r"[a-zα-ω0-9_]+", plain)
    out: set[str] = set()
    for w in words:
        if len(w) < 3 or w in _STOPWORDS:
            continue
        # Light stem (first 5 chars for longer inflected Greek/English words)
        out.add(w[:5] if len(w) > 5 and not w.isdigit() else w)
    return out


def _numbers_and_paths(text: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:[.,]\d+)?\b|[a-zA-Z]:\\[^\s]+|/[^\s/]+/[^\s]+", str(text)))


def is_near_duplicate(text_a: str, text_b: str) -> bool:
    """Return True when two multi-word memory notes express essentially the same rule/preference.
    Short values (like 'Ναι' / 'Αθήνα') on different topics are NEVER merged.
    """
    # Never merge opposite instructions (positive vs negative polarity).
    if _has_negation(text_a) != _has_negation(text_b):
        return False
    # Never merge entries that carry different concrete numbers or file paths.
    np_a, np_b = _numbers_and_paths(text_a), _numbers_and_paths(text_b)
    if (np_a or np_b) and np_a != np_b:
        return False
    tok_a, tok_b = _semantic_tokens(text_a), _semantic_tokens(text_b)
    if len(tok_a) < 2 or len(tok_b) < 2:
        return False
    if text_key(text_a).casefold() == text_key(text_b).casefold():
        return True
    inter = len(tok_a & tok_b)
    if inter < 2:
        return False
    jaccard = inter / len(tok_a | tok_b)
    containment = inter / min(len(tok_a), len(tok_b))
    return jaccard >= 0.45 or containment >= 0.65


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

    @staticmethod
    def _validate_proposal(topic, text, kind, status) -> tuple[str, str]:
        topic, text = str(topic).strip(), str(text).strip()
        if not topic or not text or len(topic) > 120 or len(text) > 4000:
            raise ValueError("Δώσε θέμα έως 120 και κείμενο έως 4000 χαρακτήρες.")
        if kind not in ("fact", "belief") or status not in ("confirmed", "unconfirmed"):
            raise ValueError("Χρειάζονται kind=fact/belief και status=confirmed/unconfirmed.")
        if kind == "fact" and status != "confirmed":
            raise ValueError("Ένα ανεπιβεβαίωτο στοιχείο δηλώνεται ως belief/unconfirmed.")
        _guard(topic, text)
        return topic, text

    @staticmethod
    def _find_duplicate_proposal(data: dict, same: list, near_matches: list, topic: str, text: str, kind: str, status: str, replaces) -> str | None:
        if not replaces and any(text_key(r["text"]) == text_key(text) and r["kind"] == kind and r["status"] == status for r in same):
            return "Υπάρχει ήδη αυτή η μνήμη· δεν γράφτηκε ξανά."
        if not replaces and near_matches:
            new_toks = _semantic_tokens(text)
            for nm in near_matches:
                if len(new_toks - _semantic_tokens(nm["text"])) <= 1 and len(nm["text"]) >= len(text):
                    return f"Υπάρχει ήδη παρόμοια μνήμη («{nm['topic']}»)· δεν γράφτηκε ξανά."
        for row in data["proposals"]:
            if (row.get("target") != "global"
                    and ((topic_key(row["topic"]) == topic_key(topic) and text_key(row["text"]) == text_key(text))
                         or is_near_duplicate(row["text"], text))
                    and row["kind"] == kind and row["status"] == status):
                return "Υπάρχει ήδη παρόμοια πρόταση μνήμης· περιμένει έγκριση."
        return None

    def propose(self, topic, text, kind="belief", status="unconfirmed", replaces=None):
        topic, text = self._validate_proposal(topic, text, kind, status)
        with LOCK:
            data = self.load()
            same = [r for r in data["entries"] if topic_key(r["topic"]) == topic_key(topic)]
            near_matches = [
                r for r in data["entries"]
                if r["kind"] == kind and r["status"] == status and is_near_duplicate(r["text"], text)
            ]
            dup_msg = self._find_duplicate_proposal(data, same, near_matches, topic, text, kind, status, replaces)
            if dup_msg is not None:
                return dup_msg
            replaced = sorted({r["id"] for r in same} | {r["id"] for r in near_matches} | set(replaces or []))
            if not set(replaced) <= {r["id"] for r in data["entries"]}:
                raise ValueError("Η αναθεώρηση αναφέρεται σε μνήμη που δεν υπάρχει πια.")
            # Οι προτάσεις γενικής μνήμης έχουν δικό τους κύκλο· ένα προφίλ δεν τις αντικαθιστά.
            pending = [r for r in data["proposals"] if topic_key(r["topic"]) == topic_key(topic) and r.get("target") != "global"]
            for row in pending:
                data["revisions"].append({"action": "superseded_proposal", "at": time.time(), "before": row})
            data["proposals"] = [r for r in data["proposals"] if r not in pending]
            if not replaced and len(data["entries"]) + len(data["proposals"]) >= 500:
                raise ValueError("Η ενεργή μνήμη έφτασε τα 500 στοιχεία. Χρειάζεται τακτοποίηση, όχι διαγραφή ιστορικού.")
            data["proposals"].append({
                "id": uuid.uuid4().hex, "topic": same[0]["topic"] if same else topic,
                "text": text, "kind": kind, "status": status, "replaces": replaced,
                "source": "agent (πρόταση)", "created": time.time(),
                "before": [copy.deepcopy(r) for r in data["entries"] if r["id"] in replaced],
            })
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
            # Second pass: merge near-duplicate entries (keeping the richest/longest text)
            deduped = []
            for row in kept:
                matched_idx = None
                for i, existing in enumerate(deduped):
                    if existing["kind"] == row["kind"] and existing["status"] == row["status"] and is_near_duplicate(existing["text"], row["text"]):
                        matched_idx = i
                        break
                if matched_idx is not None:
                    if len(row["text"]) > len(deduped[matched_idx]["text"]):
                        merged.append(deduped[matched_idx])
                        deduped[matched_idx] = row
                    else:
                        merged.append(row)
                else:
                    deduped.append(row)
            kept = deduped
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
