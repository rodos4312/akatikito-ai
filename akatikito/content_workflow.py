"""Content creation workflow: Research -> Outline -> Draft -> Review -> local MD/DOCX (L6).

Never auto-publishes anywhere; every output is strictly a local draft with explicit sources.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .redact import redact

_SLUG_RE = re.compile(r"[^a-zA-Z0-9\u0370-\u03ff\u1f00-\u1fff-]+")


@dataclass(frozen=True)
class ContentSource:
    title: str
    reference: str


def _safe_slug(topic: str) -> str:
    raw = _SLUG_RE.sub("-", str(topic or "").strip()).strip("-")
    return (raw[:60] or "content-draft").lower()


def review_draft(topic: str, outline: list[str], body: str, sources: list[ContentSource]) -> dict:
    """Run deterministic quality & safety review checks before saving a content draft."""
    issues: list[str] = []
    if not str(topic or "").strip():
        issues.append("Λείπει το θέμα του κειμένου.")
    if not outline:
        issues.append("Λείπει το περίγραμμα (outline).")
    clean_body = str(body or "").strip()
    if len(clean_body) < 40:
        issues.append("Το κείμενο είναι πολύ σύντομο (< 40 χαρακτήρες).")
    if not sources:
        issues.append("Απαιτείται τουλάχιστον μία επαληθευμένη πηγή.")
    redacted_body = redact(clean_body)
    if redacted_body != clean_body:
        issues.append("Εντοπίστηκαν και αφαιρέθηκαν πιθανά μυστικά/κλειδιά από το κείμενο.")
    return {
        "ok": not issues,
        "issues": issues,
        "word_count": len(redacted_body.split()),
        "source_count": len(sources),
        "sanitized_body": redacted_body,
    }


def render_markdown(
    topic: str,
    outline: list[str],
    body: str,
    sources: list[ContentSource],
    *,
    title: str | None = None,
) -> str:
    heading = (title or topic).strip()
    lines = [
        f"# {heading}",
        "",
        f"> Κατάσταση: **Τοπικό πρόχειρο (χωρίς δημοσίευση)** · {time.strftime('%Y-%m-%d')}",
        "",
        "## Περίγραμμα",
    ]
    for item in outline:
        lines.append(f"- {str(item).strip()}")
    lines.extend(["", "## Κείμενο", "", body.strip(), "", "## Πηγές"])
    for idx, src in enumerate(sources, start=1):
        lines.append(f"{idx}. **{src.title.strip()}** — {src.reference.strip()}")
    lines.append("")
    return "\n".join(lines)


def _write_docx(path: Path, heading: str, outline: list[str], body: str, sources: list[ContentSource]) -> None:
    import docx

    doc = docx.Document()
    doc.add_heading(heading, level=1)
    doc.add_paragraph("Κατάσταση: Τοπικό πρόχειρο (χωρίς αυτόματη δημοσίευση)")
    doc.add_heading("Περίγραμμα", level=2)
    for item in outline:
        doc.add_paragraph(str(item).strip(), style="List Bullet")
    doc.add_heading("Κείμενο", level=2)
    for para in body.strip().split("\n\n"):
        if para.strip():
            doc.add_paragraph(para.strip())
    doc.add_heading("Πηγές", level=2)
    for idx, src in enumerate(sources, start=1):
        doc.add_paragraph(f"{idx}. {src.title.strip()} — {src.reference.strip()}")
    tmp = path.with_name("." + path.name + ".tmp")
    doc.save(str(tmp))
    os.replace(tmp, path)


def build_content_draft(
    topic: str,
    *,
    outline: list[str],
    body: str,
    sources: list[ContentSource],
    output_dir: str | Path,
    fmt: str = "md",
    title: str | None = None,
) -> dict:
    """Execute Research -> Outline -> Draft -> Review and save locally as .md or .docx (never publishes)."""
    if fmt not in ("md", "docx"):
        raise ValueError("Η μορφή αποθήκευσης πρέπει να είναι 'md' ή 'docx'.")
    review = review_draft(topic, outline, body, sources)
    if not sources or not str(topic or "").strip() or not outline:
        raise ValueError("Η ροή περιεχομένου απαιτεί θέμα, περίγραμμα και τουλάχιστον μία πηγή.")

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(title or topic)
    target = out_root / f"{slug}.{fmt}"
    safe_body = review["sanitized_body"]

    if fmt == "md":
        md = render_markdown(topic, outline, safe_body, sources, title=title)
        tmp = target.with_name("." + target.name + ".tmp")
        tmp.write_text(md, encoding="utf-8")
        os.replace(tmp, target)
    else:
        _write_docx(target, (title or topic).strip(), outline, safe_body, sources)

    return {
        "ok": True,
        "stages": ["research", "outline", "draft", "review"],
        "path": str(target),
        "format": fmt,
        "published": False,
        "publish_policy": "manual_approval_required",
        "review": {k: v for k, v in review.items() if k != "sanitized_body"},
    }


def request_publish_handoff(draft_path: str | Path, *, user_approved: bool = False) -> dict:
    """Explicit guard: never publishes externally without explicit owner approval."""
    if not user_approved:
        raise PermissionError("Απαγορεύεται η αυτόματη δημοσίευση περιεχομένου χωρίς ρητή έγκριση χρήστη.")
    path = Path(draft_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Δεν βρέθηκε το πρόχειρο: {path}")
    return {"ready_for_manual_publish": True, "path": str(path), "published": False}
