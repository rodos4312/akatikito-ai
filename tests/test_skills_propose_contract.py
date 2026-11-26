"""Characterization tests for Skills.propose (Q4.3 safety net)."""
from __future__ import annotations

import pytest

from akatikito import memory
from akatikito.skills import Skills


def _make_skills(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(memory, "DIR", mem_dir)
    return Skills(project)


VALID_STEPS = [
    "Inspect target files with read_file before changing anything.",
    "Apply minimal verified edits with edit_file on the exact range.",
    "Run automated checks and confirm zero regressions.",
]


def test_propose_input_validation_errors(tmp_path, monkeypatch):
    sk = _make_skills(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Όνομα skill"):
        sk.propose("Bad_Name!", "Valid description", VALID_STEPS, direct_learn=True)
    with pytest.raises(ValueError, match="περιγραφή"):
        sk.propose("valid-skill", "Line1\nLine2", VALID_STEPS, direct_learn=True)
    with pytest.raises(ValueError, match="3–30"):
        sk.propose("valid-skill", "Valid description", ["Only one step here."], direct_learn=True)
    with pytest.raises(ValueError, match="εμβέλεια"):
        sk.propose("valid-skill", "Valid description", VALID_STEPS, scope="global", direct_learn=True)


def test_propose_requires_workflow_unless_direct_learn(tmp_path, monkeypatch):
    sk = _make_skills(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="καταγεγραμμένη επανάληψη"):
        sk.propose("alpha-flow", "Run alpha flow safely", VALID_STEPS)

    msg = sk.propose(
        "alpha-flow",
        "Run alpha flow safely",
        VALID_STEPS,
        scope="project",
        category="testing",
        triggers="alpha, smoke",
        required_tools="read_file, edit_file",
        notes="Watch edge cases\nKeep atomic commits",
        direct_learn=True,
    )
    assert msg.startswith("ΠΡΟΤΑΣΗ SKILL: alpha-flow")
    pending = sk.view()["proposals"]
    assert len(pending) == 1
    row = pending[0]
    assert row["mode"] == "create"
    assert row["scope"] == "project"
    assert row["category"] == "testing"
    assert row["triggers"] == ["alpha", "smoke"]
    assert row["required_tools"] == ["read_file", "edit_file"]


def test_propose_duplicate_pending_and_supersede_history(tmp_path, monkeypatch):
    sk = _make_skills(tmp_path, monkeypatch)
    first = sk.propose("beta-flow", "Beta workflow", VALID_STEPS, direct_learn=True)
    assert "ΠΡΟΤΑΣΗ SKILL: beta-flow" in first

    dup = sk.propose("beta-flow", "Beta workflow", VALID_STEPS, direct_learn=True)
    assert "Υπάρχει ήδη η ίδια πρόταση" in dup

    updated_steps = VALID_STEPS + ["Archive the final summary report cleanly."]
    second = sk.propose("beta-flow", "Beta workflow v2", updated_steps, direct_learn=True)
    assert "ΠΡΟΤΑΣΗ SKILL: beta-flow" in second
    assert len(sk.view()["proposals"]) == 1
    data = sk._load()
    assert any(h.get("action") == "superseded_proposal" for h in data["history"])


def test_propose_exact_body_duplicate_and_similarity_merge(tmp_path, monkeypatch):
    sk = _make_skills(tmp_path, monkeypatch)
    sk.propose("deploy-check", "Verify deployment artifacts safely", VALID_STEPS, direct_learn=True)
    pid = sk.view()["proposals"][0]["id"]
    sk.answer(pid, True)

    # Exact body duplicate under a different name suggests reuse
    reuse = sk.propose("deploy-clone", "Verify deployment artifacts safely", VALID_STEPS, direct_learn=True)
    assert "Χρησιμοποίησε την υπάρχουσα συνταγή: deploy-check" in reuse

    # Similar skill before reading -> warns to read first
    alt_steps = [
        "Inspect target files with read_file before deploying anything.",
        "Apply minimal verified edits with edit_file on deployment config.",
        "Run automated checks and confirm zero deployment regressions.",
    ]
    warn = sk.propose("deploy-check-v2", "Verify deployment artifacts safely", alt_steps, direct_learn=True)
    assert "ΕΝΤΟΠΙΣΤΗΚΕ ΠΑΡΟΜΟΙΟ SKILL" in warn

    # After reading the existing skill, proposing a similar name auto-merges into refine mode
    sk.read("deploy-check")
    merged = sk.propose("deploy-check-v2", "Verify deployment artifacts safely", alt_steps, direct_learn=True)
    assert "συγχωνεύτηκε στο υπάρχον παρόμοιο 'deploy-check'" in merged
    row = sk.view()["proposals"][0]
    assert row["name"] == "deploy-check"
    assert row["mode"] == "refine"
    assert row["merged_from"] == "deploy-check-v2"

    # force_new bypasses similarity detection
    forced = sk.propose(
        "deploy-check-v3",
        "Verify deployment artifacts safely",
        alt_steps,
        direct_learn=True,
        force_new=True,
    )
    assert forced.startswith("ΠΡΟΤΑΣΗ SKILL: deploy-check-v3")


def test_propose_refining_unread_and_identical_existing_and_cap(tmp_path, monkeypatch):
    sk = _make_skills(tmp_path, monkeypatch)
    sk.propose("gamma-flow", "Gamma workflow", VALID_STEPS, direct_learn=True)
    sk.answer(sk.view()["proposals"][0]["id"], True)

    # Refining existing skill without reading it first raises
    with pytest.raises(ValueError, match="use_skill"):
        sk.propose("gamma-flow", "Gamma workflow updated", VALID_STEPS, direct_learn=True)

    # Reading it then proposing identical body returns no-op message
    sk.read("gamma-flow")
    same = sk.propose("gamma-flow", "Gamma workflow", VALID_STEPS, direct_learn=True)
    assert "ήδη ίδια" in same

    # 100 pending proposals cap
    data = sk._load()
    data["proposals"] = [
        {
            "id": str(i), "name": f"p-{i}", "body": "x", "signature": str(i),
            "target": str(tmp_path / f"p-{i}.md"), "project": str(sk.project), "scope": "user",
        }
        for i in range(100)
    ]
    sk._save(data)
    delta_steps = [
        "Collect metrics from the staging database with read_file.",
        "Generate a delta summary report in the target workspace.",
        "Verify the generated report contains zero syntax errors.",
    ]
    with pytest.raises(ValueError, match="100 εκκρεμείς"):
        sk.propose("delta-flow", "Delta workflow", delta_steps, direct_learn=True, force_new=True)
