"""Pure skill text parsing, metadata validation, formatting, and similarity helpers extracted from skills.py."""
from __future__ import annotations

import re
import unicodedata
import uuid

from .profile import text_key

NAME = re.compile(r"[a-z][a-z0-9-]{1,63}")
MAX_BODY = 40000
VALID_CATEGORIES = ("coding", "debugging", "research", "documents", "office", "organization", "devops", "general")

_STOPWORDS = {
    "και", "για", "στο", "στη", "στην", "στον", "των", "την", "τον", "απο", "με", "σε", "να", "το", "τα",
    "ενα", "μια", "που", "οταν", "πριν", "μετα", "αυτο", "αυτη", "ολα", "καθε", "πως", "ωστε", "κατα",
    "the", "and", "for", "with", "from", "into", "this", "that", "when", "then", "how", "use", "using",
    "skill", "skills", "συνταγη", "συνταγες", "βημα", "βηματα", "step", "steps",
}


def _strip_accents(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text or "").casefold())
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")


def _meaningful_tokens(text: str) -> set[str]:
    clean = _strip_accents(text)
    words = re.findall(r"[a-zα-ω0-9]{2,}", clean)
    return {w for w in words if w not in _STOPWORDS and not w.isdigit()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        partial = 0.0
        for wa in a:
            if len(wa) >= 4 and any(len(wb) >= 4 and (wa[:4] == wb[:4]) for wb in b):
                partial += 0.5
        inter = min(partial, float(min(len(a), len(b))))
    union = len(a | b)
    return float(inter) / float(union) if union else 0.0


def _parse_skill_text(text: str) -> dict:
    front = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
    body_part = text.split("---", 2)[2] if text.startswith("---") and text.count("---") >= 2 else text
    fields = dict(re.findall(r"^(name|description|category|triggers|tools):\s*(.+)$", front, re.M))
    raw_triggers = fields.get("triggers", "")
    triggers = [t.strip() for t in raw_triggers.split(",") if t.strip()] if raw_triggers else []
    raw_tools = fields.get("tools", "")
    required_tools = [t.strip() for t in raw_tools.split(",") if t.strip()] if raw_tools else []
    steps = []
    notes = []
    template_lines = []
    section = "steps"
    for line in body_part.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            low = _strip_accents(stripped)
            if any(_strip_accents(k) in low for k in ("σημειώσεις", "παγίδες", "notes", "pitfalls")):
                section = "notes"
                continue
            if any(_strip_accents(k) in low for k in ("πρότυπο", "προτυπο", "snippet", "template")):
                section = "template"
                continue
        if section == "template":
            template_lines.append(line)
            continue
        if not stripped:
            continue
        if section == "notes":
            if stripped.startswith(("- ", "* ")):
                notes.append(stripped[2:].strip())
            else:
                notes.append(stripped)
        else:
            m = re.match(r"^\d+\.\s+(.+)$", stripped)
            if m:
                steps.append(m.group(1).strip())
    template = "\n".join(template_lines).strip()
    if template.startswith("```") and template.endswith("```"):
        inner = template.splitlines()
        if len(inner) >= 2:
            template = "\n".join(inner[1:-1]).strip()
    return {
        "title": fields.get("name", "")[:160],
        "description": fields.get("description", "")[:500],
        "category": fields.get("category", "").strip()[:40],
        "triggers": triggers[:20],
        "required_tools": required_tools[:20],
        "steps": steps,
        "notes": notes,
        "template": template[:10000],
    }


def _check_metadata(category, triggers, notes, required_tools, template):
    if not isinstance(category, str) or len(category) > 40 or any(c in category for c in "\r\n"):
        raise ValueError("Η κατηγορία χρειάζεται μία γραμμή έως 40 χαρακτήρες.")
    for values, label, limit in ((triggers, "triggers", 100), (required_tools, "εργαλεία", 100)):
        if values is not None and (
            not isinstance(values, list)
            or len(values) > 20
            or not all(isinstance(x, str) and 0 < len(x.strip()) <= limit and not any(c in x for c in "\r\n,") for x in values)
        ):
            raise ValueError("Μη έγκυρα " + label + ".")
    if notes is not None and (
        not isinstance(notes, list)
        or len(notes) > 20
        or not all(isinstance(x, str) and len(x) <= 2000 for x in notes)
    ):
        raise ValueError("Χρειάζονται έως 20 σημειώσεις, έως 2000 χαρακτήρες η κάθε μία.")
    if not isinstance(template, str) or len(template) > 10000:
        raise ValueError("Το πρότυπο ξεπερνά το όριο.")


def _format_skill_body(
    name: str,
    description: str,
    steps: list[str],
    category: str = "",
    triggers: list[str] | None = None,
    notes: list[str] | None = None,
    required_tools: list[str] | None = None,
    template: str = "",
) -> str:
    _check_metadata(category, triggers, notes, required_tools, template)
    header = ["---", f"name: {name}", f"description: {description}"]
    if category and str(category).strip():
        header.append(f"category: {str(category).strip()[:40]}")
    clean_triggers = [str(t).strip() for t in (triggers or []) if str(t).strip()]
    if clean_triggers:
        header.append("triggers: " + ", ".join(clean_triggers[:20]))
    clean_tools = [str(t).strip() for t in (required_tools or []) if str(t).strip()]
    if clean_tools:
        header.append("tools: " + ", ".join(clean_tools[:20]))
    header.append("---\n")
    body = "\n".join(header) + "\n"
    body += "\n".join(f"{i}. {s.strip()}" for i, s in enumerate(steps, 1)) + "\n"
    clean_notes = [str(n).strip() for n in (notes or []) if str(n).strip()]
    if clean_notes:
        body += "\n### Σημειώσεις & Παγίδες\n" + "\n".join(f"- {n}" for n in clean_notes[:20]) + "\n"
    clean_template = str(template or "").strip()
    if clean_template:
        body += "\n### Πρότυπο / Snippet\n```\n" + clean_template[:10000] + "\n```\n"
    return body


def _validate_quality(steps: list[str]) -> None:
    keys = [text_key(s) for s in steps]
    if len(set(keys)) < min(3, len(steps)):
        raise ValueError("Τα βήματα του skill δεν επιτρέπεται να επαναλαμβάνουν την ίδια πρόταση.")


def _empty():
    return {"version": 1, "proposals": [], "history": [], "workflows": [], "uses": {}}


def import_skill_markdown(skills, raw_markdown: str, scope: str = "user") -> dict:
    """Import an external SKILL.md (Hermes, Claude Code, Cursor, Agensi.io) safely."""
    from .injection_guard import scan_prompt_injection

    raw = str(raw_markdown or "").strip()
    if not raw or len(raw) > MAX_BODY:
        raise ValueError("Δώσε έγκυρο κείμενο SKILL.md (έως 40.000 χαρακτήρες).")
    hits = scan_prompt_injection(raw)
    if hits:
        raise ValueError(f"Απορρίφθηκε για ασφάλεια — εντοπίστηκαν ύποπτες οδηγίες Prompt Injection ({', '.join(hits)}).")

    parsed = _parse_skill_text(raw)
    name = str(parsed.get("title") or parsed.get("name") or "").strip().strip("\"'").lower()
    if not name:
        m_hdr = re.search(r"^#\s+(.+)$", raw, re.M)
        if m_hdr:
            slug = re.sub(r"[^a-z0-9]+", "-", _strip_accents(m_hdr.group(1))).strip("-")
            name = slug[:60]
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")
    if not name or not name[0].isalpha():
        name = "imported-" + (name or uuid.uuid4().hex[:6])
    name = name[:60]

    desc = str(parsed.get("description") or "").strip().strip("\"'")
    if not desc:
        body_clean = raw.split("---", 2)[2] if raw.startswith("---") and raw.count("---") >= 2 else raw
        for ln in body_clean.splitlines():
            s = ln.strip().lstrip("#-* ").strip()
            if len(s) >= 12:
                desc = s[:180]
                break
    if not desc:
        desc = f"Εισαγόμενη συνταγή ({name})"

    body_clean = raw.split("---", 2)[2].strip() if raw.startswith("---") and raw.count("---") >= 2 else raw
    steps = list(parsed.get("steps") or [])
    if not steps:
        extracted: list[str] = []
        for ln in body_clean.splitlines():
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith("```"):
                continue
            s = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", s).strip()
            if s:
                extracted.append(s)
        steps = extracted
    if not steps:
        steps = [body_clean]
    orig_template = parsed.get("template") or ""
    if not orig_template and len(body_clean) > sum(len(s) for s in steps) + 40:
        orig_template = body_clean[:10000]

    cat = str(parsed.get("category") or "general").strip().lower()
    if cat not in VALID_CATEGORIES:
        cat = "general"

    return skills.save_owner_skill(
        name=name,
        description=desc,
        steps=steps,
        scope=scope if scope in ("user", "project") else "user",
        category=cat,
        triggers=parsed.get("triggers") or [],
        notes=parsed.get("notes") or [],
        required_tools=parsed.get("required_tools") or [],
        template=orig_template,
    )
