"""Capture → owner-reviewed draft → reuse → revision, with no model calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
import uuid

from .local_store import LOCK, atomic_bytes, checked, digest, load_json, save_json
from .profile import text_key
from .skills_format import (
    MAX_BODY,
    NAME,
    VALID_CATEGORIES,
    _STOPWORDS,
    _check_metadata,
    _empty,
    _format_skill_body,
    _jaccard,
    _meaningful_tokens,
    _parse_skill_text,
    _strip_accents,
    _validate_quality,
)

APP_ROOT = Path(__file__).resolve().parents[1]


class Skills:
    def __init__(self, root):
        from . import memory
        self.project = Path(root).resolve()
        self.directory = Path(memory.DIR)
        self.path = self.directory / "skill_proposals.json"
        self.user_root = self.directory / "skills"

    def _load(self):
        data = load_json(self.path, _empty, limit=16_000_000)
        if (not isinstance(data, dict) or data.get("version") != 1
                or not all(isinstance(data.get(k), list) for k in ("proposals", "history", "workflows"))
                or not isinstance(data.get("uses"), dict)):
            raise ValueError("Μη έγκυρο αρχείο συνταγών· διατηρήθηκε χωρίς αλλαγές.")
        if not all(isinstance(r,dict) and isinstance(r.get("action"),str) for r in data["history"]):
            raise ValueError("Μη έγκυρο ιστορικό συνταγών· διατηρήθηκε χωρίς αλλαγές.")
        for row in data["workflows"]:
            if (not isinstance(row,dict) or not isinstance(row.get("id"),str)
                    or not isinstance(row.get("count"),int) or row["count"]<0
                    or not isinstance(row.get("tools"),list)
                    or not all(isinstance(x,str) for x in row["tools"])):
                raise ValueError("Μη έγκυρη καταγραφή διαδικασίας· διατηρήθηκε χωρίς αλλαγές.")
        for row in data["proposals"]:
            if (not isinstance(row,dict) or not all(isinstance(row.get(k),str)
                    for k in ("id","name","body","signature","target","project","scope"))):
                raise ValueError("Μη έγκυρη πρόταση συνταγής· διατηρήθηκε χωρίς αλλαγές.")
        telemetry = data.get("telemetry", {})
        if not isinstance(telemetry, dict) or any(
            not isinstance(v, dict)
            or any(type(v.get(k, 0)) is not int or v.get(k, 0) < 0 for k in ("successes", "failures", "recoveries"))
            for v in telemetry.values()
        ):
            raise ValueError("Μη έγκυρη καταγραφή χρήσης συνταγών· διατηρήθηκε χωρίς αλλαγές.")
        return data

    def _save(self, data):
        if len(json.dumps(data,ensure_ascii=False).encode("utf-8"))>16_000_000:
            raise ValueError("Το ιστορικό συνταγών έφτασε τα 16 MB. Δεν αφαιρέθηκε κανένα στοιχείο.")
        save_json(self.path, data)

    def _entries(self):
        sources = [(APP_ROOT / "skills", "builtin"),
                   (self.user_root, "user")]
        if self.project != APP_ROOT:
            sources.append((self.project / "skills", "legacy-project"))
        sources.append((self.project / ".akatikito" / "skills", "project"))
        entries = {}
        for folder, source in sources:
            try:
                checked(folder)
                for path in sorted(folder.glob("*.md"))[:100]:
                    checked(path)
                    if not path.is_file() or path.stat().st_size > MAX_BODY:
                        continue
                    raw = path.read_bytes()
                    text = raw.decode("utf-8-sig")
                    parsed = _parse_skill_text(text)
                    entries[path.stem] = {
                        "name": path.stem,
                        "title": parsed["title"] or path.stem,
                        "description": parsed["description"],
                        "category": parsed["category"],
                        "triggers": parsed["triggers"],
                        "required_tools": parsed["required_tools"],
                        "steps": parsed["steps"],
                        "notes": parsed["notes"],
                        "template": parsed["template"],
                        "source": source,
                        "path": path,
                        "text": text,
                        "revision": hashlib.sha256(raw).hexdigest(),
                    }
            except (OSError, UnicodeError, ValueError):
                continue
        return entries

    def catalog(self):
        try:
            loaded = self._load()
            uses = loaded.get("uses", {})
            telemetry = loaded.get("telemetry", {}) if isinstance(loaded.get("telemetry"), dict) else {}
        except Exception:
            uses = {}
            telemetry = {}
        result = []
        for _, row in sorted(self._entries().items()):
            item = {k: row[k] for k in ("name", "title", "description", "source", "revision")}
            if row.get("category"):
                item["category"] = row["category"]
            if row.get("triggers"):
                item["triggers"] = row["triggers"]
            if row.get("required_tools"):
                item["required_tools"] = row["required_tools"]
            if row.get("steps"):
                item["steps"] = row["steps"]
            if row.get("notes"):
                item["notes"] = row["notes"]
                item["notes_count"] = len(row["notes"])
            if row.get("template"):
                item["template"] = row["template"]
            use_count = uses.get(str(row["path"]), 0)
            if use_count:
                item["uses"] = use_count
            tel = telemetry.get(row["name"])
            if isinstance(tel, dict):
                item["telemetry"] = tel
            result.append(item)
        return result

    def listing(self, offset=0, limit=10, query="", category=""):
        rows = self.catalog()
        if category:
            cat_cf = str(category).strip().casefold()
            rows = [r for r in rows if str(r.get("category", "")).casefold() == cat_cf]
        if query:
            q_cf = query.casefold().strip()
            parts = [p for p in q_cf.split() if p]
            filtered = []
            for r in rows:
                hay = " ".join([
                    r["name"], r["description"],
                    str(r.get("category", "")),
                    " ".join(r.get("triggers", [])),
                ]).casefold()
                if q_cf in hay or (len(parts) > 1 and all(p in hay for p in parts)):
                    filtered.append(r)
            rows = filtered
        start = max(0, int(offset))
        limit = max(1, min(20, int(limit)))
        return {
            "skills": rows[start:start + limit],
            "total": len(rows),
            "next": start + limit if start + limit < len(rows) else None,
        }

    def find_similar(self, name: str = "", description: str = "",
                     steps: list[str] | None = None, triggers: list[str] | None = None,
                     threshold: float = 0.38) -> list[dict]:
        """Detect existing skills that overlap with a candidate skill."""
        cand_name_tokens = _meaningful_tokens(str(name).replace("-", " "))
        cand_meta_tokens = _meaningful_tokens(str(description) + " " + " ".join(triggers or []))
        cand_step_tokens = _meaningful_tokens(" ".join(steps or []))
        cand_all = cand_name_tokens | cand_meta_tokens | cand_step_tokens
        if not cand_all:
            return []

        matches = []
        for existing_name, row in sorted(self._entries().items()):
            if existing_name == name:
                continue
            ex_name_tokens = _meaningful_tokens(existing_name.replace("-", " "))
            ex_meta_tokens = _meaningful_tokens(
                row.get("description", "") + " " + " ".join(row.get("triggers", []))
            )
            ex_step_tokens = _meaningful_tokens(" ".join(row.get("steps", [])))

            name_sim = _jaccard(cand_name_tokens, ex_name_tokens)
            if name and existing_name and (name in existing_name or existing_name in name):
                name_sim = max(name_sim, 0.75)
            meta_sim = _jaccard(cand_meta_tokens | cand_name_tokens, ex_meta_tokens | ex_name_tokens)
            step_sim = _jaccard(cand_step_tokens, ex_step_tokens) if (cand_step_tokens and ex_step_tokens) else 0.0

            score = max(
                0.45 * name_sim + 0.35 * meta_sim + 0.20 * step_sim,
                0.70 * name_sim + 0.30 * meta_sim,
                0.60 * meta_sim + 0.40 * step_sim if cand_step_tokens else 0.0,
            )
            if score >= threshold:
                matches.append({
                    "name": existing_name,
                    "description": row.get("description", ""),
                    "category": row.get("category", ""),
                    "source": row.get("source", "user"),
                    "score": round(score, 2),
                })
        matches.sort(key=lambda x: (-x["score"], x["name"]))
        return matches[:5]

    def match_for_task(self, task: str, limit: int = 3) -> list[dict]:
        """Find existing skills relevant to the current user request."""
        if not task or not str(task).strip():
            return []
        task_tokens = _meaningful_tokens(task)
        task_cf = _strip_accents(task)
        if not task_tokens:
            return []
        scored = []
        for existing_name, row in sorted(self._entries().items()):
            score = 0.0
            slug_words = _meaningful_tokens(existing_name.replace("-", " "))
            if slug_words and slug_words <= task_tokens:
                score += 0.65
            elif slug_words & task_tokens:
                score += 0.35 * (len(slug_words & task_tokens) / len(slug_words))
            for trig in row.get("triggers", []):
                trig_clean = _strip_accents(trig)
                if trig_clean and trig_clean in task_cf:
                    score += 0.55
            meta_tokens = _meaningful_tokens(row.get("description", "") + " " + " ".join(row.get("triggers", [])))
            if meta_tokens:
                overlap = len(task_tokens & meta_tokens)
                if overlap >= 2:
                    score += min(0.5, overlap * 0.18)
                elif overlap == 1 and len(meta_tokens) <= 4:
                    score += 0.25
            if score >= 0.32:
                scored.append({
                    "name": existing_name,
                    "description": row.get("description", ""),
                    "score": round(min(1.0, score), 2),
                })
        scored.sort(key=lambda x: (-x["score"], x["name"]))
        return scored[:limit]

    def prompt(self, budget=4000, task="", healing_hints: dict[str, str] | None = None):
        rows = self.catalog()
        lines = []
        used = 120
        if healing_hints:
            for s_name, err_text in list(healing_hints.items())[:2]:
                heal_line = (
                    f"⚠️ ΑΥΤΟ-ΕΠΙΔΙΟΡΘΩΣΗ SKILL: Το '/{s_name}' συνάντησε σφάλμα ({err_text[:120]}). "
                    f"Μόλις λύσεις το πρόβλημα, κάλεσε patch_skill(name='{s_name}', add_notes=[...]) "
                    f"για να αποθηκεύσεις την παγίδα/λύση!"
                )
                if used + len(heal_line) <= budget:
                    lines.append(heal_line)
                    used += len(heal_line) + 1
        if task:
            matched = self.match_for_task(task)
            if matched:
                hint_line = "💡 Σχετικά υπάρχοντα skills για την εργασία: " + ", ".join(
                    f"/{m['name']}" for m in matched
                ) + " (κάλεσε use_skill για ανάγνωση ή patch_skill για βελτίωση αντί νέου διπλότυπου)."
                if used + len(hint_line) <= budget:
                    lines.append(hint_line)
                    used += len(hint_line) + 1
        for row in rows:
            cat = f" ({row['category']})" if row.get("category") else ""
            heal_tag = " [⚠️ χρειάζεται patch]" if (row.get("telemetry") or {}).get("needs_healing") else ""
            line = "/" + row["name"] + cat + heal_tag + ": " + row["description"][:200] + " [" + row["revision"][:12] + "]"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line) + 1
        catalog_lines = sum(1 for l in lines if l.startswith("/"))
        if catalog_lines < len(rows):
            lines.append(f"+{len(rows)-catalog_lines} συνταγές: list_skills (query ή offset). Τα πλήρη βήματα φορτώνονται με use_skill.")
        return "\n".join(lines)

    def read(self, name, available_tools: set[str] | list[str] | None = None):
        with LOCK:
            row = self._entries().get(name)
            if row is None:
                raise ValueError("Άγνωστο skill.")
            data = self._load()
            key = str(row["path"])
            data["uses"][key] = data["uses"].get(key, 0) + 1
            self._save(data)
            out = "[Τοπική συνταγή — δεν παραχωρεί δικαιώματα εκτέλεσης]\n" + row["text"]
            req_tools = row.get("required_tools") or []
            if req_tools and available_tools is not None:
                avail_set = set(available_tools)
                missing = [t for t in req_tools if t not in avail_set]
                if missing:
                    out += (
                        "\n[⚠️ Προσοχή: Το skill απαιτεί τα εργαλεία: "
                        + ", ".join(req_tools)
                        + " — Μη διαθέσιμα στην τρέχουσα λειτουργία: "
                        + ", ".join(missing)
                        + ". Άλλαξε λειτουργία (π.χ. Κώδικας / Βοηθός) ή σύνδεσε το αντίστοιχο MCP.]"
                    )
            return out

    def record_telemetry(self, skill_name: str, had_error: bool, recovered: bool, last_error: str = "") -> None:
        """Track execution health of a skill used during a turn (Self-Healing Telemetry)."""
        with LOCK:
            if skill_name not in self._entries():
                return
            data = self._load()
            telemetry = data.get("telemetry")
            if not isinstance(telemetry, dict):
                telemetry = {}
                data["telemetry"] = telemetry
            entry = telemetry.get(skill_name)
            if not isinstance(entry, dict):
                entry = {"successes": 0, "failures": 0, "needs_healing": False, "last_error": ""}
            if had_error:
                entry["failures"] = int(entry.get("failures", 0)) + 1
                entry["needs_healing"] = True
                if last_error:
                    entry["last_error"] = str(last_error)[:240]
            else:
                entry["successes"] = int(entry.get("successes", 0)) + 1
            telemetry[skill_name] = entry
            self._save(data)

    def capture(self, events, task="", allow_readback=False, completed=False, traits=None, used_skills=()):
        self._capture_skill_telemetry(events, completed, initial_skills=used_skills)
        if any(e.kind=='tool_result' and e.data.get('name') in ('edit_file','write_file') for e in events):
            return self._capture_edits(events,task,allow_readback)
        if not completed:return
        from .workflows import observed
        evidence=observed(events,traits,self.project)
        if evidence is None:return
        with LOCK:
            data=self._load()
            row=next((r for r in data['workflows'] if r['id']==evidence['id']),None)
            if row is None:
                row={**evidence,'count':0};data['workflows'].append(row)
            row['count']+=1;row['last_seen']=time.time()
            self._save(data)

    def _capture_skill_telemetry(self, events, completed: bool, initial_skills=()) -> None:
        used_skills = [str(s).strip() for s in (initial_skills or ()) if str(s).strip()]
        had_error_after = {s: "" for s in used_skills}
        patched_in_turn = set()
        for ev in events:
            if ev.kind == "tool_call":
                tname = ev.data.get("name", "")
                try:
                    args = json.loads(ev.data.get("arguments") or "{}") if isinstance(ev.data.get("arguments"), str) else (ev.data.get("arguments") or {})
                except Exception:
                    args = {}
                if tname == "use_skill" and isinstance(args, dict) and args.get("name"):
                    sname = str(args["name"]).strip()
                    if sname and sname not in used_skills:
                        used_skills.append(sname)
                        had_error_after[sname] = ""
                elif tname in ("patch_skill", "propose_skill") and isinstance(args, dict) and args.get("name"):
                    patched_in_turn.add(str(args["name"]).strip())
            elif ev.kind == "tool_result" and used_skills:
                res = str(ev.data.get("result") or "")
                tname = ev.data.get("name", "")
                if res.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")) or (tname == "run_command" and not res.startswith("returncode=0")):
                    for sname in used_skills:
                        if not had_error_after.get(sname):
                            had_error_after[sname] = f"{tname}: {res.splitlines()[0][:140]}"
            elif ev.kind == "verify" and used_skills and not ev.data.get("ok"):
                for sname in used_skills:
                    if not had_error_after.get(sname):
                        had_error_after[sname] = "Αποτυχία ελέγχου verify"
        for sname in used_skills:
            if sname in patched_in_turn:
                continue
            err = had_error_after.get(sname, "")
            try:
                self.record_telemetry(sname, had_error=bool(err), recovered=completed, last_error=err)
            except Exception:
                pass

    def _capture_edits(self, events, task="", allow_readback=False):
        """Record verified procedural evidence, not prompts, source or chat text."""
        names,extensions,modified = [],set(),set()
        verified=False
        allowed = {"list_files", "read_file", "search_project", "search_in_file", "read_document",
                   "edit_file", "write_file", "run_command", "web_search", "web_fetch", "browser_fetch",
                   "web_news", "youtube_transcript", "github_read",
                   "create_word_document", "create_excel_sheet", "create_pdf_document", "convert_data_file"}
        for event in events:
            if event.kind=="verify" and event.data.get("ok"):
                verified=True
                if not names or names[-1]!="run_command":
                    names.append("run_command")
                continue
            if event.kind != "tool_result" or event.data.get("name") not in allowed:
                continue
            result = str(event.data.get("result") or "")
            if result.startswith(("ΣΦΑΛΜΑ", "ΑΠΟΡΡΙΨΗ", "ΑΠΟΤΡΟΠΗ")):
                continue
            name = event.data["name"]
            raw_path=str(event.data.get("path") or "")
            path=str((self.project/raw_path).resolve()) if raw_path else ""
            paths=event.data.get("paths") or ([raw_path] if raw_path else [])
            if name in ("edit_file","write_file") and result.startswith("ΕΓΙΝΕ"):
                modified.update(str((self.project/p).resolve()) for p in paths)
                verified=False
            elif name in ("create_word_document","create_excel_sheet","create_pdf_document","convert_data_file") and result.startswith("ΕΓΙΝΕ"):
                modified.update(str((self.project/p).resolve()) for p in paths)
                verified=True
            elif allow_readback and name in ("read_file","read_document") and path and path in modified:
                verified=True
            elif name=="run_command" and result.startswith("returncode=0"):
                verified=True
            suffix=Path(str(event.data.get("path") or "")).suffix.lower()
            if suffix:
                extensions.add(suffix)
            extensions.update(Path(p).suffix.lower() for p in paths if Path(p).suffix)
            if not names or names[-1] != name:
                names.append(name)
        inspect={"list_files","read_file","search_project","search_in_file","read_document","web_search","web_fetch","browser_fetch","web_news","youtube_transcript","github_read"}
        modify_tools={"edit_file", "write_file", "create_word_document", "create_excel_sheet", "create_pdf_document", "convert_data_file"}
        if (len(names) < 3 or not verified or not modify_tools & set(names)
                or not inspect & set(names)):
            return
        names = names[:30]
        # Extra searches and repeated read ranges should not hide the same workflow.
        phases=["inspect","modify","verify"]
        key = hashlib.sha256("|".join(phases+sorted(extensions)).encode()).hexdigest()[:16]
        with LOCK:
            data = self._load()
            row = next((r for r in data["workflows"] if r["id"] == key), None)
            if row is None:
                row = {"id": key, "tools": names, "phases":phases,
                       "extensions":sorted(extensions),"examples":[], "count": 0}
                data["workflows"].append(row)
            row["count"] += 1
            if task:
                row["examples"]=(row.get("examples",[])+[str(task)[:180]])[-2:]
            row["last_seen"] = time.time()
            self._save(data)

    def hints(self):
        with LOCK:
            return [{"id": r["id"], "tools": r["tools"], "count": r["count"],
                     "examples":r.get("examples",[]),"extensions":r.get("extensions",[]),
                     'category':r.get('category','editing'),'evidence':r.get('evidence','verified-edit'),
                     'source_count':r.get('source_count',0)}
                    for r in self._load()["workflows"] if r["count"] >= 2][-8:]

    @staticmethod
    def _validate_propose_inputs(name, description, steps, scope, triggers, required_tools, notes):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Όνομα skill: 2–64 λατινικά πεζά, αριθμοί και παύλες.")
        description = str(description).strip()
        if not description or len(description) > 500 or "\n" in description or "\r" in description:
            raise ValueError("Η περιγραφή χρειάζεται μία γραμμή έως 500 χαρακτήρες.")
        if (not isinstance(steps, list) or not 3 <= len(steps) <= 30
                or not all(isinstance(s, str) and s.strip() and len(s) <= 2000 for s in steps)):
            raise ValueError("Χρειάζονται 3–30 συγκεκριμένα βήματα, έως 2000 χαρακτήρες το καθένα.")
        _validate_quality(steps)
        if scope not in ("user", "project"):
            raise ValueError("Η εμβέλεια είναι user ή project.")
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",") if t.strip()]
        if isinstance(required_tools, str):
            required_tools = [t.strip() for t in required_tools.split(",") if t.strip()]
        if isinstance(notes, str):
            notes = [n.strip() for n in notes.splitlines() if n.strip()]
        return description, triggers, required_tools, notes

    def _match_existing_skill(self, entries, data, name, current, candidate_body,
                              description, steps, triggers, force_new):
        cand_key = text_key(candidate_body.split("---", 2)[-1])
        for existing_name, entry in entries.items():
            if existing_name != name and text_key(entry["text"].split("---", 2)[-1].strip()) == cand_key:
                return name, current, "", "Χρησιμοποίησε την υπάρχουσα συνταγή: " + existing_name
        if current is not None or force_new:
            return name, current, "", None
        similar = self.find_similar(name, description, steps, triggers, threshold=0.52)
        if not similar:
            return name, current, "", None
        best = similar[0]
        sim_entry = entries.get(best["name"])
        if not sim_entry or sim_entry["source"] == "builtin":
            return name, current, "", None
        if data["uses"].get(str(sim_entry["path"])):
            return best["name"], sim_entry, name, None
        warn = (
            f"ΕΝΤΟΠΙΣΤΗΚΕ ΠΑΡΟΜΟΙΟ SKILL: Υπάρχει ήδη η συνταγή '{best['name']}' "
            f"({best['description']}, ομοιότητα {int(best['score']*100)}%). "
            f"Διάβασέ την πρώτα με use_skill(name='{best['name']}') και έπειτα βελτίωσέ την "
            f"με patch_skill ή propose_skill(name='{best['name']}'), αντί να φτιάξεις διπλότυπο."
        )
        return name, current, "", warn

    def _resolve_propose_target(self, current, data, name, scope, workflow_id, direct_learn):
        if current and current["source"] == "builtin":
            raise ValueError("Η έτοιμη συνταγή είναι κλειδωμένη· πρότεινε διαφορετική συνταγή.")
        if current:
            if not data["uses"].get(str(current["path"])):
                raise ValueError("Διάβασε πρώτα τη συνταγή με use_skill πριν προτείνεις βελτίωση.")
            resolved_scope = "project" if current["source"] in ("project", "legacy-project") else "user"
            return current["path"], resolved_scope
        workflow = next((r for r in data["workflows"] if r["id"] == workflow_id and r["count"] >= 2), None)
        if workflow is None and not direct_learn and workflow_id not in ("direct", "learn"):
            raise ValueError("Νέο skill χρειάζεται καταγεγραμμένη επανάληψη επιτυχημένης διαδικασίας 3+ βημάτων.")
        base_dir = self.project / ".akatikito" / "skills" if scope == "project" else self.user_root
        return base_dir / (name + ".md"), scope

    def _record_skill_proposal(self, data, name, description, body, scope, target, current,
                               workflow_id, category, triggers, required_tools, auto_refined_from):
        if current and current["text"] == body:
            return "Η συνταγή είναι ήδη ίδια· δεν δημιουργήθηκε διπλότυπο."
        signature = hashlib.sha256(body.encode("utf-8")).hexdigest()
        proj = str(self.project)
        for row in data["proposals"]:
            if row["name"] == name and row["project"] == proj and row["signature"] == signature:
                return "Υπάρχει ήδη η ίδια πρόταση skill· περιμένει έγκριση."
        old = [r for r in data["proposals"] if r["name"] == name and r["project"] == proj]
        data["history"].extend({"action": "superseded_proposal", "before": r, "at": time.time()} for r in old)
        data["proposals"] = [r for r in data["proposals"] if r not in old]
        if len(data["proposals"]) >= 100:
            raise ValueError("Υπάρχουν 100 εκκρεμείς προτάσεις· χρειάζεται πρώτα έλεγχος.")
        proposal_row = {
            "id": uuid.uuid4().hex, "name": name, "description": description,
            "body": body, "scope": scope, "project": proj,
            "target": str(target), "base": digest(target), "signature": signature,
            "created": time.time(), "workflow_id": workflow_id,
            "mode": "refine" if current is not None else "create",
        }
        if category:
            proposal_row["category"] = str(category).strip()[:40]
        if triggers:
            proposal_row["triggers"] = [str(t).strip() for t in triggers if str(t).strip()][:20]
        if required_tools:
            proposal_row["required_tools"] = [str(t).strip() for t in required_tools if str(t).strip()][:20]
        if auto_refined_from:
            proposal_row["merged_from"] = auto_refined_from
        data["proposals"].append(proposal_row)
        self._save(data)
        if auto_refined_from:
            return f"ΠΡΟΤΑΣΗ SKILL (συγχωνεύτηκε στο υπάρχον παρόμοιο '{name}'): απαιτεί προεπισκόπηση και έγκριση χρήστη."
        return "ΠΡΟΤΑΣΗ SKILL: " + name + " — απαιτεί προεπισκόπηση και έγκριση χρήστη."

    def propose(self, name, description, steps, workflow_id="", scope="user",
                category="", triggers=None, notes=None, required_tools=None,
                template="", direct_learn=False, force_new=False):
        description, triggers, required_tools, notes = self._validate_propose_inputs(
            name, description, steps, scope, triggers, required_tools, notes
        )
        with LOCK:
            entries, data = self._entries(), self._load()
            current = entries.get(name)
            candidate_body = _format_skill_body(
                name, description, steps, category, triggers, notes, required_tools, template
            )
            name, current, auto_refined_from, early = self._match_existing_skill(
                entries, data, name, current, candidate_body, description, steps, triggers, force_new
            )
            if early is not None:
                return early
            body = _format_skill_body(
                name, description, steps, category, triggers, notes, required_tools, template
            )
            if len(body.encode("utf-8")) > MAX_BODY:
                raise ValueError("Η συνταγή ξεπερνά τα 40000 bytes.")
            from .skills_guard import format_block_message, scan_skill_text

            scan = scan_skill_text(name, body)
            if scan.verdict == "dangerous":
                raise ValueError(format_block_message(scan))
            target, scope = self._resolve_propose_target(
                current, data, name, scope, workflow_id, direct_learn
            )
            res = self._record_skill_proposal(
                data, name, description, body, scope, target, current,
                workflow_id, category, triggers, required_tools, auto_refined_from
            )
            if scan.verdict == "caution" and data["proposals"]:
                data["proposals"][-1]["security_warnings"] = [f.description for f in scan.findings[:5]]
                self._save(data)
            return res

    def patch(self, name: str, add_steps: list[str] | None = None,
              replace_step: dict | None = None, add_notes: list[str] | None = None,
              description: str = "", category: str = "", triggers: list[str] | None = None,
              required_tools: list[str] | None = None, template: str | None = None) -> str:
        """Surgical update of an existing skill without rewriting all steps from scratch."""
        with LOCK:
            entries = self._entries()
            current = entries.get(name)
            if current is None:
                similar = self.find_similar(name=name, description=description, steps=add_steps, threshold=0.45)
                hint = f" Μήπως εννοείς το '{similar[0]['name']}';" if similar else ""
                raise ValueError(f"Δεν βρέθηκε υπάρχον skill '{name}' για patch.{hint}")
            if current["source"] == "builtin":
                raise ValueError("Η έτοιμη συνταγή είναι κλειδωμένη· δεν επιτρέπεται τροποποίηση.")
            data = self._load()
            key = str(current["path"])
            data["uses"][key] = data["uses"].get(key, 0) + 1
            self._save(data)

        steps = list(current.get("steps") or [])
        notes = list(current.get("notes") or [])
        new_desc = (description or "").strip() or current.get("description", "")
        new_cat = (category or "").strip() or current.get("category", "")
        new_triggers = list(triggers) if triggers is not None else list(current.get("triggers") or [])
        new_tools = list(required_tools) if required_tools is not None else list(current.get("required_tools") or [])
        new_template = template if template is not None else current.get("template", "")

        if replace_step and isinstance(replace_step, dict):
            idx = int(replace_step.get("index", 0))
            txt = str(replace_step.get("text", "")).strip()
            if not (1 <= idx <= len(steps)) or not txt:
                raise ValueError(f"Το replace_step χρειάζεται έγκυρο index (1..{len(steps)}) και μη κενό text.")
            steps[idx - 1] = txt

        for s in (add_steps or []):
            s_clean = str(s).strip()
            if s_clean and not any(text_key(s_clean) == text_key(ex) for ex in steps):
                steps.append(s_clean)

        for n in (add_notes or []):
            n_clean = str(n).strip()
            if n_clean and not any(text_key(n_clean) == text_key(ex) for ex in notes):
                notes.append(n_clean)

        if len(steps) < 3:
            raise ValueError("Το skill μετά το patch πρέπει να έχει τουλάχιστον 3 βήματα.")

        return self.propose(
            name=name,
            description=new_desc,
            steps=steps,
            scope="project" if current["source"] in ("project", "legacy-project") else "user",
            category=new_cat,
            triggers=new_triggers,
            notes=notes,
            required_tools=new_tools,
            template=new_template,
            direct_learn=True,
        )

    def save_owner_skill(self, name, description, steps, scope="user",
                         category="", triggers=None, notes=None,
                         required_tools=None, template=""):
        """Direct owner creation/update of a recipe from the UI without requiring agent workflow IDs."""
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Όνομα skill: 2–64 λατινικά πεζά, αριθμοί και παύλες (π.χ. clean-csv).")
        description = str(description or "").strip()
        if not description or len(description) > 500 or "\n" in description or "\r" in description:
            raise ValueError("Η περιγραφή χρειάζεται μία γραμμή έως 500 χαρακτήρες.")
        if (not isinstance(steps, list) or not 1 <= len(steps) <= 30
                or not all(isinstance(s, str) and s.strip() and len(s) <= 2000 for s in steps)):
            raise ValueError("Χρειάζονται 1–30 βήματα, έως 2000 χαρακτήρες το καθένα.")
        if scope not in ("user", "project"):
            raise ValueError("Η εμβέλεια είναι user ή project.")
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",") if t.strip()]
        if isinstance(required_tools, str):
            required_tools = [t.strip() for t in required_tools.split(",") if t.strip()]
        if isinstance(notes, str):
            notes = [n.strip() for n in notes.splitlines() if n.strip()]
        body = _format_skill_body(
            name, description, steps, category, triggers, notes, required_tools, template
        )
        if len(body.encode("utf-8")) > MAX_BODY:
            raise ValueError("Η συνταγή ξεπερνά τα 40000 bytes.")
        with LOCK:
            entries, data = self._entries(), self._load()
            current = entries.get(name)
            if current and current["source"] == "builtin":
                raise ValueError("Η έτοιμη συνταγή είναι κλειδωμένη· διάλεξε άλλο όνομα.")
            target = (self.project / ".akatikito" / "skills" if scope == "project" else self.user_root) / (name + ".md")
            checked(target)
            base = digest(target)
            previous = target.read_bytes() if target.is_file() else None
            data["history"].append({"action": "owner_save", "at": time.time(), "name": name, "scope": scope,
                                     "before": previous.decode("utf-8-sig") if previous is not None else None})
            if isinstance(data.get("telemetry"), dict) and name in data["telemetry"]:
                data["telemetry"][name]["needs_healing"] = False
            self._save(data)
            atomic_bytes(target, body.encode("utf-8"), expected=base)
            return {"ok": True, "name": name, "scope": scope}

    def delete_owner_skill(self, name: str) -> dict:
        """Delete a user or project skill from the UI while preserving its full text in revision history."""
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Μη έγκυρο όνομα skill.")
        with LOCK:
            entries, data = self._entries(), self._load()
            current = entries.get(name)
            if current is None:
                raise ValueError(f"Δεν βρέθηκε η συνταγή '{name}'.")
            if current["source"] == "builtin":
                raise ValueError("Οι ενσωματωμένες συνταγές (builtin) είναι κλειδωμένες και δεν διαγράφονται.")
            target = Path(current["path"])
            checked(target)
            previous = target.read_text(encoding="utf-8-sig") if target.is_file() else None
            data["history"].append({
                "action": "owner_delete",
                "at": time.time(),
                "name": name,
                "scope": current["source"],
                "before": previous,
            })
            data["proposals"] = [r for r in data["proposals"] if not (r["name"] == name and r["project"] == str(self.project))]
            self._save(data)
            if target.is_file():
                target.unlink()
            return {"ok": True, "name": name}

    def view(self):
        with LOCK:
            data = self._load()
            return {"catalog": self.catalog(),
                    "proposals": [r for r in data["proposals"] if r["project"] == str(self.project)],
                    "workflows": self.hints(), "revision_count": len(data["history"])}

    def answer(self, proposal_id, approve, expected=None):
        with LOCK:
            data = self._load()
            row = next((r for r in data["proposals"] if r["id"] == proposal_id and r["project"] == str(self.project)), None)
            if row is None:
                raise ValueError("Η πρόταση δεν ανήκει στο τρέχον project ή δεν υπάρχει πια.")
            signature=hashlib.sha256(row["body"].encode("utf-8")).hexdigest()
            if signature != row["signature"] or expected is not None and expected != signature:
                raise ValueError("Η πρόταση άλλαξε μετά την προεπισκόπηση.")
            if approve:
                target = Path(row["target"])
                allowed = [self.user_root, self.project / ".akatikito" / "skills"]
                if self.project != APP_ROOT:
                    allowed.append(self.project / "skills")
                if not NAME.fullmatch(row["name"]) or not any(target == root / (row["name"] + ".md") for root in allowed):
                    raise ValueError("Η διαδρομή της συνταγής δεν επιτρέπεται.")
                checked(target)
                if digest(target) != row["base"]:
                    raise ValueError("Η συνταγή άλλαξε μετά την προεπισκόπηση· χρειάζεται νέα πρόταση.")
                previous = target.read_bytes() if target.is_file() else None
                data["history"].append({"action": "refine" if previous is not None else "create",
                                         "at": time.time(), "proposal": row,
                                         "before": previous.decode("utf-8-sig") if previous is not None else None})
                data["proposals"] = [r for r in data["proposals"] if r["id"] != proposal_id]
                if isinstance(data.get("telemetry"), dict) and row["name"] in data["telemetry"]:
                    data["telemetry"][row["name"]]["needs_healing"] = False
                # Commit history first; if the recipe write fails, restore the pending proposal.
                original_data = self._load()
                self._save(data)
                try:
                    atomic_bytes(target, row["body"].encode("utf-8"), expected=row["base"])
                except BaseException:
                    self._save(original_data)
                    raise
            else:
                data["history"].append({"action": "reject", "at": time.time(), "proposal": row})
                data["proposals"] = [r for r in data["proposals"] if r["id"] != proposal_id]
                self._save(data)
            return {"ok": True}

    def import_markdown(self, raw_markdown: str, scope: str = "user") -> dict:
        from .skills_format import import_skill_markdown
        from .skills_guard import format_block_message, scan_skill_text

        scan = scan_skill_text("imported-skill", str(raw_markdown or ""))
        if scan.verdict == "dangerous":
            raise ValueError(format_block_message(scan))
        res = import_skill_markdown(self, raw_markdown, scope=scope)
        if scan.verdict == "caution" and isinstance(res, dict):
            res["security_warnings"] = [f.description for f in scan.findings[:5]]
        return res
