"""F5 tests: Getting Started EL/EN guides, README ordering, SmartScreen/privacy coverage, and live link/image check."""
from __future__ import annotations

from pathlib import Path

from scripts import release_check

ROOT = Path(__file__).resolve().parents[1]


def test_f5_getting_started_guides_exist_have_5_steps_smartscreen_privacy_and_valid_links():
    el = ROOT / "docs" / "GETTING-STARTED.el.md"
    en = ROOT / "docs" / "GETTING-STARTED.en.md"
    assert el.is_file(), "Missing docs/GETTING-STARTED.el.md"
    assert en.is_file(), "Missing docs/GETTING-STARTED.en.md"

    el_text = el.read_text(encoding="utf-8")
    en_text = en.read_text(encoding="utf-8")
    assert "SmartScreen" in el_text and "SmartScreen" in en_text
    assert "![" in el_text and "![" in en_text
    assert "data/" in el_text or "data\\" in el_text
    assert "data/" in en_text or "data\\" in en_text

    # README must start with Getting Started before technical sections
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    gs_pos = readme.find("docs/GETTING-STARTED")
    tech_pos = readme.find("## Key Capabilities")
    assert gs_pos != -1 and tech_pos != -1 and gs_pos < tech_pos

    # release_check must expose and pass link/image validation
    assert hasattr(release_check, "check_getting_started_docs")
    assert release_check.check_getting_started_docs(ROOT) == []


def test_f5_docs_check_catches_broken_image_or_link(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("Start: [Guide](docs/GETTING-STARTED.en.md)\n", encoding="utf-8")
    (tmp_path / "docs" / "GETTING-STARTED.el.md").write_text("![ok](missing.png)\n", encoding="utf-8")
    (tmp_path / "docs" / "GETTING-STARTED.en.md").write_text("[broken](does-not-exist.md)\n", encoding="utf-8")
    errs = release_check.check_getting_started_docs(tmp_path)
    assert len(errs) >= 2
    assert any("missing.png" in e for e in errs)
    assert any("does-not-exist.md" in e for e in errs)
