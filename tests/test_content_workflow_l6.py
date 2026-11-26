"""L6 Content workflow tests: skill catalog, MD/DOCX export with sources, secret redaction, no auto-publish."""
from __future__ import annotations

from pathlib import Path

import pytest

from akatikito.content_workflow import ContentSource, build_content_draft, request_publish_handoff
from akatikito.skills import Skills


def test_content_workflow_skill_in_catalog(tmp_path):
    names = {row["name"] for row in Skills(tmp_path).catalog()}
    assert "content-workflow" in names


def test_content_pipeline_saves_md_and_docx_with_sources_and_blocks_auto_publish(tmp_path):
    sources = [
        ContentSource("Επίσημη τεκμηρίωση", "docs/SETTINGS.md"),
        ContentSource("Οδηγός ασφάλειας", "docs/adr/0001-per-call-sqlite-memory.md"),
    ]
    outline = ["Εισαγωγή", "Πώς λειτουργεί τοπικά", "Συμπέρασμα"]
    body = (
        "Το Akatikito κρατά όλα τα δεδομένα τοπικά στον υπολογιστή του χρήστη.\n\n"
        "Επίσης φροντίζει να κρύβει τυχαία κλειδιά όπως sk-1234567890abcdefghijklmnop πριν αποθηκευτούν."
    )

    md_res = build_content_draft(
        "Τοπικός βοηθός AI",
        outline=outline,
        body=body,
        sources=sources,
        output_dir=tmp_path / "exports",
        fmt="md",
    )
    assert md_res["ok"] is True
    assert md_res["published"] is False
    assert md_res["stages"] == ["research", "outline", "draft", "review"]
    md_text = Path(md_res["path"]).read_text(encoding="utf-8")
    assert "Επίσημη τεκμηρίωση" in md_text
    assert "sk-1234567890abcdefghijklmnop" not in md_text

    # Word (.docx) export also works locally
    docx_res = build_content_draft(
        "Τοπικός βοηθός AI",
        outline=outline,
        body=body,
        sources=sources,
        output_dir=tmp_path / "exports",
        fmt="docx",
    )
    assert docx_res["ok"] is True and docx_res["published"] is False
    assert Path(docx_res["path"]).is_file() and Path(docx_res["path"]).stat().st_size > 0

    # Publishing without explicit approval is strictly refused
    with pytest.raises(PermissionError, match="δημοσίευση"):
        request_publish_handoff(md_res["path"], user_approved=False)

    # Even approved handoff only prepares manual publish (never auto-posts)
    handoff = request_publish_handoff(md_res["path"], user_approved=True)
    assert handoff["ready_for_manual_publish"] is True and handoff["published"] is False
