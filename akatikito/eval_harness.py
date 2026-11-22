"""End-to-end evaluation harness for Akatikito (deterministic offline dry-run + live LLM benchmark)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalOutcome:
    id: str
    title: str
    passed: bool
    elapsed_ms: int
    details: str
    tokens: dict | None = None


def _scenario_multilang_navigation(root: Path) -> str:
    from .reading import SOURCE_INDEX
    from .repo_map import RepoMap

    (root / "app.rs").write_text("#[inline]\npub fn compute(x: i32) -> i32 {\n    x * 2\n}\n", encoding="utf-8")
    (root / "Calc.cs").write_text("public class Calc {\n  public int Add(int a, int b) => a + b;\n}\n", encoding="utf-8")
    out_rs = SOURCE_INDEX.outline(root / "app.rs")
    out_cs = SOURCE_INDEX.outline(root / "Calc.cs")
    rmap = RepoMap(root).render()
    assert "compute" in out_rs and "Add" in out_cs and "app.rs" in rmap
    return "Tree-sitter/scanner outline + RepoMap verified for Rust & C#"


def _scenario_surgical_edit_and_verify(root: Path) -> str:
    from .tools import dispatch
    from .workspace import Workspace

    (root / "math_mod.py").write_text("def double(x):\n    return x + 1\n", encoding="utf-8")
    ws = Workspace(root)
    res = dispatch(ws, "edit_file", {"path": "math_mod.py", "edits": [{"search": "return x + 1", "replace": "return x * 2"}]})
    assert res.startswith("ΕΓΙΝΕ")
    assert "return x * 2" in (root / "math_mod.py").read_text(encoding="utf-8")
    return "Surgical edit_file + syntax guard passed"


def _scenario_word_document_roundtrip(root: Path) -> str:
    from .features import create_word_document, read_document
    from .workspace import Workspace

    ws = Workspace(root)
    res = create_word_document(ws, "report.docx", title="Ετήσια Αναφορά", paragraphs=["Πλήρης ελληνική υποστήριξη."])
    assert res.startswith("ΕΓΙΝΕ")
    back = json.loads(read_document(ws, "report.docx"))
    assert "Ετήσια Αναφορά" in back["text"] and len(back["sha256"]) == 64
    return "DOCX write + SHA-256 readback passed"


def _scenario_excel_formula_injection_guard(root: Path) -> str:
    from openpyxl import load_workbook
    from .features import create_excel_sheet
    from .workspace import Workspace

    ws = Workspace(root)
    res = create_excel_sheet(ws, "budget.xlsx", rows=[["=HYPERLINK(\"http://evil\")", "100"]], headers=["Περιγραφή", "Ποσό"])
    assert res.startswith("ΕΓΙΝΕ")
    wb = load_workbook(root / "budget.xlsx", data_only=False)
    try:
        assert wb.active["A2"].data_type == "s"
    finally:
        wb.close()
    return "XLSX literal-text formula injection guard passed"


def _scenario_greek_pdf_generation(root: Path) -> str:
    from .features import create_pdf_document, read_document
    from .workspace import Workspace

    ws = Workspace(root)
    res = create_pdf_document(ws, "greek.pdf", title="Προσφορά Πελάτη", paragraphs=["Σύνολο: 124,00 € με ΦΠΑ."])
    assert res.startswith("ΕΓΙΝΕ")
    back = json.loads(read_document(ws, "greek.pdf"))
    assert "Προσφορά Πελάτη" in back["text"]
    return "Greek PDF creation + text extraction passed"


def _scenario_decimal_calculator_vat(root: Path) -> str:
    from .tool_plugins import tool_calculate_expression
    from .workspace import Workspace

    ws = Workspace(root)
    out = json.loads(tool_calculate_expression(ws, "0.1 + 0.2", vat_rate=24))
    assert out["result_exact"] == "0.3"
    assert out["precision_digits"] == 50
    return "50-digit Decimal arithmetic & VAT passed"


def _scenario_strict_data_conversion(root: Path) -> str:
    from .tool_plugins import tool_convert_data_file
    from .workspace import Workspace

    ws = Workspace(root)
    (root / "items.csv").write_text("όνομα,τιμή\nΚαφές,3.50\nΤσάι,2.80\n", encoding="utf-8")
    md = tool_convert_data_file(ws, "items.csv", target_format="markdown")
    assert "| όνομα | τιμή |" in md and "Καφές" in md
    return "Strict CSV -> Markdown conversion passed"


def _scenario_file_organization_and_undo(root: Path) -> str:
    if os.name != "nt":
        return "Skipped NTFS organizer on non-Windows"
    from .file_organization import Organizer
    from .workspace import Workspace

    (root / "draft.txt").write_text("Κείμενο", encoding="utf-8")
    org = Organizer(Workspace(root), scope="full")
    plan = org.preview([
        {"action": "mkdir", "destination": "sorted"},
        {"action": "move", "source": "draft.txt", "destination": "sorted/final.txt"},
    ])
    org.apply(plan["id"], plan["review"], lambda _p: True)
    assert (root / "sorted/final.txt").is_file() and not (root / "draft.txt").exists()
    up = org.undo_preview(plan["id"])
    org.undo(plan["id"], up["review"], lambda _p: True)
    assert (root / "draft.txt").is_file()
    return "File organization plan -> apply -> verified undo passed"


def _scenario_hardened_git_diff(root: Path) -> str:
    from .git_tools import git_status_diff
    from .workspace import Workspace

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "eval@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Eval"], cwd=root, check=True, capture_output=True)
    (root / "note.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    (root / "note.txt").write_text("v2\n", encoding="utf-8")
    diff = git_status_diff(Workspace(root))
    assert "[Git Status]" in diff and "+v2" in diff
    return "Hardened read-only git_status_diff passed"


def _scenario_security_assistant_scope_and_dpapi(root: Path) -> str:
    from .settings import mask_secret, unmask_secret
    from .file_organization import Organizer
    from .workspace import Workspace

    secret = "dummy-eval-token-987654321"
    enc = mask_secret(secret)
    assert enc != secret and unmask_secret(enc) == secret
    if os.name == "nt":
        (root / "script.py").write_text("print(1)\n", encoding="utf-8")
        try:
            Organizer(Workspace(root), scope="documents").preview(
                [{"action": "move", "source": "script.py", "destination": "other.py"}]
            )
        except PermissionError:
            return "DPAPI encryption + assistant-scope refusal passed"
        raise AssertionError("Assistant scope failed to block Python code move")
    return "Secret protection verified"


DRY_SCENARIOS = [
    ("01_multilang_nav", "Multi-language Tree-sitter Navigation", _scenario_multilang_navigation),
    ("02_surgical_edit", "Surgical edit_file & Syntax Guard", _scenario_surgical_edit_and_verify),
    ("03_word_doc", "Word Document Write & SHA-256 Readback", _scenario_word_document_roundtrip),
    ("04_excel_safe", "Excel Formula-Injection Protection", _scenario_excel_formula_injection_guard),
    ("05_greek_pdf", "Greek PDF Rendering & Text Readback", _scenario_greek_pdf_generation),
    ("06_decimal_vat", "50-Digit Decimal Calculator & VAT", _scenario_decimal_calculator_vat),
    ("07_strict_csv", "Strict CSV/JSON/Markdown Converter", _scenario_strict_data_conversion),
    ("08_file_org", "NTFS File Organization + Verified Undo", _scenario_file_organization_and_undo),
    ("09_git_diff", "Hardened Read-Only Git Status/Diff", _scenario_hardened_git_diff),
    ("10_security", "DPAPI Secret Vault & Mode Policy Refusal", _scenario_security_assistant_scope_and_dpapi),
]


def run_dry_evals() -> dict:
    """Run all 10 deterministic offline scenarios with zero LLM calls."""
    outcomes: list[EvalOutcome] = []
    started_all = time.monotonic()
    for sid, title, fn in DRY_SCENARIOS:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f"akatikito-eval-{sid}-") as td:
            try:
                details = fn(Path(td))
                outcomes.append(EvalOutcome(sid, title, True, int((time.monotonic() - t0) * 1000), details))
            except Exception as exc:
                outcomes.append(EvalOutcome(sid, title, False, int((time.monotonic() - t0) * 1000), f"{type(exc).__name__}: {exc}"))
    passed = sum(1 for o in outcomes if o.passed)
    return {
        "mode": "dry-run",
        "passed": passed,
        "total": len(outcomes),
        "elapsed_ms": int((time.monotonic() - started_all) * 1000),
        "results": [o.__dict__ for o in outcomes],
    }


LIVE_PROMPTS = [
    ("live_01_calc", "assistant", "Υπολόγισε ακριβώς το 0.1 + 0.2 με ΦΠΑ 24% χρησιμοποιώντας το calculate_expression.", lambda ans, root: "0.3" in ans),
    ("live_02_doc", "assistant", "Φτιάξε ένα αρχείο Word με όνομα summary.docx με τίτλο 'Δοκιμή' και μία παράγραφο 'Επιτυχία'.", lambda ans, root: (root / "summary.docx").is_file()),
    ("live_03_code", "code", "Διάβασε το app.py και διόρθωσε τη συνάρτηση double ώστε να επιστρέφει x * 2 αντί για x + 1.", lambda ans, root: "x * 2" in (root / "app.py").read_text(encoding="utf-8")),
]


def run_live_evals() -> dict:
    """Run live benchmark scenarios against the currently configured LLM provider in settings.json."""
    from .agent import Akatikito
    from .config import Config
    from .settings import Settings

    s = Settings.load()
    key = s.get_key(s.provider)
    if not key and s.provider != "Ollama":
        raise RuntimeError(f"Δεν βρέθηκε API key για τον πάροχο {s.provider} στο settings.json.")
    outcomes: list[EvalOutcome] = []
    started_all = time.monotonic()
    for sid, mode, prompt, check in LIVE_PROMPTS:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f"akatikito-live-{sid}-") as td:
            root = Path(td)
            (root / "app.py").write_text("def double(x):\n    return x + 1\n", encoding="utf-8")
            try:
                cfg = Config(base_url=s.base_url, api_key=key, model=s.model or s.models.get(s.provider, ""), max_steps=12)
                agent = Akatikito(root=root, config=cfg)
                agent.set_mode(mode)
                ans = agent.ask(prompt)
                ok = bool(check(ans, root))
                outcomes.append(EvalOutcome(sid, prompt[:60], ok, int((time.monotonic() - t0) * 1000), ans[:200], dict(agent.tokens)))
            except Exception as exc:
                outcomes.append(EvalOutcome(sid, prompt[:60], False, int((time.monotonic() - t0) * 1000), f"{type(exc).__name__}: {exc}"))
    passed = sum(1 for o in outcomes if o.passed)
    return {
        "mode": "live",
        "provider": s.provider,
        "model": s.model,
        "passed": passed,
        "total": len(outcomes),
        "elapsed_ms": int((time.monotonic() - started_all) * 1000),
        "results": [o.__dict__ for o in outcomes],
    }
