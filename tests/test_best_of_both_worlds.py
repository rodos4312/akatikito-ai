"""Comprehensive verification tests for all 5 integrated Best-of-Both-Worlds packages (A-E)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- Package A: File Organization & Paginated Tree


def test_package_a_paginated_tree_and_version_check(tmp_path):
    from akatikito.file_view import list_tree

    for i in range(5):
        (tmp_path / f"doc_{i:02d}.txt").write_text(f"hello {i}", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    page1 = list_tree(tmp_path, ".", offset=0, limit=3)
    assert page1["total"] == 6
    assert len(page1["entries"]) == 3
    assert page1["entries"][0]["type"] == "dir"
    assert page1["entries"][0]["name"] == "subdir"
    assert page1["next"] == 3

    page2 = list_tree(tmp_path, ".", offset=3, limit=3, version=page1["version"])
    assert len(page2["entries"]) == 3
    assert page2["next"] is None

    # Mutate directory -> stale version must be rejected
    (tmp_path / "doc_99.txt").write_text("new", encoding="utf-8")
    with pytest.raises(ValueError, match="άλλαξε"):
        list_tree(tmp_path, ".", offset=3, limit=3, version=page1["version"])


@pytest.mark.skipif(os.name != "nt", reason="NTFS file organizer targets Windows")
def test_package_a_organizer_preview_apply_undo_scope_and_api(tmp_path):
    from akatikito.app_api import AppState
    from akatikito.file_organization import Organizer
    from akatikito.organization_tools import apply_file_operations, file_operation_history, plan_file_operations
    from akatikito.workspace import Workspace

    (tmp_path / "notes.md").write_bytes("Σημειώσεις\r\n".encode("utf-8-sig"))
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "code.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    ws = Workspace(tmp_path)

    # 1. Assistant scope ('documents') blocks organizing Python code, allows documents
    doc_org = Organizer(ws, scope="documents")
    with pytest.raises(PermissionError, match="Κώδικας"):
        doc_org.preview([{"action": "move", "source": "code.py", "destination": "renamed.py"}])

    # 2. Full workflow: mkdir + move + copy + trash + undo
    org = Organizer(ws, scope="full")
    plan = org.preview([
        {"action": "mkdir", "destination": "archive/2026"},
        {"action": "move", "source": "notes.md", "destination": "archive/2026/notes_final.md"},
        {"action": "copy", "source": "data.csv", "destination": "archive/2026/data_copy.csv"},
        {"action": "trash", "source": "code.py"},
    ])
    assert plan["id"] and plan["review"]
    # Preview must NOT modify files
    assert (tmp_path / "notes.md").is_file()
    assert not (tmp_path / "archive").exists()

    # Owner denial refuses apply
    with pytest.raises(PermissionError, match="OWNER_DENIED"):
        org.apply(plan["id"], plan["review"], lambda _p: False)
    assert (tmp_path / "notes.md").is_file()

    # Approved apply executes cleanly
    res = org.apply(plan["id"], plan["review"], lambda _p: True)
    assert res.startswith("ΕΓΙΝΕ")
    assert not (tmp_path / "notes.md").exists()
    assert (tmp_path / "archive/2026/notes_final.md").read_text(encoding="utf-8-sig") == "Σημειώσεις\n"
    assert (tmp_path / "archive/2026/data_copy.csv").is_file()
    assert (tmp_path / "data.csv").is_file()
    assert not (tmp_path / "code.py").exists()

    # History & Undo restore exact original state
    hist = org.history()
    assert len(hist) == 1 and hist[0]["state"] == "complete"
    u_prev = org.undo_preview(plan["id"])
    u_res = org.undo(plan["id"], u_prev["review"], lambda _p: True)
    assert u_res.startswith("ΕΓΙΝΕ")
    assert (tmp_path / "notes.md").is_file()
    assert (tmp_path / "code.py").is_file()
    assert not (tmp_path / "archive/2026/notes_final.md").exists()

    # 3. Case-only rename works on Windows NTFS
    case_plan = org.preview([{"action": "move", "source": "notes.md", "destination": "Notes.md"}])
    org.apply(case_plan["id"], case_plan["review"], lambda _p: True)
    assert "Notes.md" in os.listdir(tmp_path)

    # 4. Shared agent tools & AppState.files_organize API
    ws.organization_scope = "documents"
    ws.external_guard = lambda _kind, _details: True
    tool_plan = json.loads(plan_file_operations(ws, [{"action": "copy", "source": "Notes.md", "destination": "Notes_v2.md"}]))
    tool_res = apply_file_operations(ws, tool_plan["id"], tool_plan["review"])
    assert tool_res.startswith("ΕΓΙΝΕ")
    assert (tmp_path / "Notes_v2.md").is_file()
    assert tool_plan["id"] in file_operation_history(ws, id=tool_plan["id"])

    st = AppState()
    st.settings.project = str(tmp_path)
    api_plan = st.files_organize("plan", {"project": str(tmp_path), "operations": [{"action": "mkdir", "destination": "reports"}]})
    assert "id" in api_plan
    api_apply = st.files_organize("apply", {"project": str(tmp_path), "id": api_plan["id"], "review": api_plan["review"]})
    assert api_apply["ok"] is True
    assert (tmp_path / "reports").is_dir()


# --------------------------------------------------------------------------- Package B: Isolated PublicProxy & PDF Fallback


def test_package_b_pdf_cdp_primary_and_ttf_fallback(tmp_path, monkeypatch):
    from akatikito import network_tools as net
    from akatikito.features import create_pdf_document, read_document
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)
    # Force Chromium absent -> must fall back cleanly to our pure-Python TTF vector PDF engine!
    monkeypatch.setattr(net, "find_chromium_binary", lambda: None)
    out = create_pdf_document(ws, "fallback.pdf", title="Ελληνικός Τίτλος", paragraphs=["Παράγραφος δοκιμής."])
    assert out.startswith("ΕΓΙΝΕ")
    info = json.loads(read_document(ws, "fallback.pdf"))
    assert info["sha256"] and len(info["sha256"]) == 64
    assert "Ελληνικός Τίτλος" in info["text"]

    # Overwrite with wrong expected_sha256 must fail; with exact sha256 must succeed
    with pytest.raises(ValueError, match="SHA256"):
        create_pdf_document(ws, "fallback.pdf", title="Νέος", expected_sha256="0" * 64)
    out2 = create_pdf_document(ws, "fallback.pdf", title="Νέος Τίτλος", expected_sha256=info["sha256"])
    assert out2.startswith("ΕΓΙΝΕ")


def test_package_b_public_proxy_validator_blocks_private():
    from akatikito.browser_web import PublicProxy
    from akatikito.network_tools import _public_url

    # Validate that _public_url rejects loopback/private targets before proxy connects
    for bad in ("http://127.0.0.1/secret", "http://localhost:8080/", "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(PermissionError):
            _public_url(bad)
    assert callable(PublicProxy)


# --------------------------------------------------------------------------- Package C: Advanced Scheduler


def test_package_c_scheduler_hash_auth_and_uncertain_crash_recovery(tmp_path):
    from datetime import datetime, timedelta
    from akatikito.agenda import Agenda
    from akatikito.scheduler import ScheduledQueue

    proj = tmp_path / "proj"
    proj.mkdir()
    agenda_path = proj / ".agent" / "agenda.json"
    queue_path = tmp_path / "scheduled.json"
    queue = ScheduledQueue(queue_path)

    past = datetime.now() - timedelta(minutes=5)
    agenda = Agenda(agenda_path)
    task = agenda.add(
        "Ημερήσιος έλεγχος",
        past.strftime("%Y-%m-%d"),
        past.strftime("%H:%M"),
        note="Δώσε σύντομη αναφορά.",
        repeat="daily",
        auto_run=True,
        auto_mode="assistant",
    )

    # 1. Without owner authorization, collect() refuses to enqueue!
    with pytest.raises(ValueError, match="έγκριση ιδιοκτήτη"):
        queue.collect(proj)

    # 2. Authorize task -> collect() enqueues cleanly and advances repeat
    queue.authorize_task(proj, task)
    queue.collect(proj)
    jobs = queue.view()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["mode"] == "assistant"

    # 3. Claim starts job; simulate dead PID -> next claim marks it 'uncertain' (never auto-replayed!)
    claimed = queue.claim()
    assert claimed is not None and claimed["status"] == "running"
    data = queue.load()
    data["jobs"][0]["pid"] = 99999999  # non-existent PID
    from akatikito.local_store import save_json
    save_json(queue_path, data)

    second = queue.claim()
    assert second is None  # did NOT auto-replay!
    assert queue.view()[0]["status"] == "uncertain"

    # 4. Owner explicit retry creates a new queued job
    queue.action(claimed["id"], "retry")
    all_jobs = queue.view()
    assert len(all_jobs) == 2
    assert all_jobs[1]["status"] == "queued"

    # 5. Owner cancel transitions queued job to cancelled
    queue.action(all_jobs[1]["id"], "cancel")
    assert queue.view()[1]["status"] == "cancelled"


# --------------------------------------------------------------------------- Package D: Hardening & Smart Tools


def test_package_d_decimal_calculator_strict_converter_excel_formula_and_git(tmp_path):
    from openpyxl import load_workbook
    from akatikito.features import create_excel_sheet, git_status_diff, read_document
    from akatikito.tool_plugins import tool_calculate_expression, tool_convert_data_file
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)

    # 1. Exact 50-digit Decimal arithmetic (no binary float 0.30000000000000004!)
    calc = json.loads(tool_calculate_expression(ws, "0.1 + 0.2", vat_rate=24))
    assert calc["result_exact"] == "0.3"
    assert calc["precision_digits"] == 50
    assert calc["vat_analysis"]["exact_net_to_gross"].startswith("0.372")

    # 2. Excel formula injection protection (literal string cell, never formula!)
    msg = create_excel_sheet(ws, "safe.xlsx", rows=[["=SUM(A1:A5)", "+1+1", "Κανονικό"]], headers=["Τύπος", "Άλλο", "Κείμενο"])
    assert msg.startswith("ΕΓΙΝΕ")
    wb = load_workbook(tmp_path / "safe.xlsx", data_only=False)
    try:
        sh = wb.active
        assert sh["A2"].data_type == "s"
        assert sh["A2"].value == "=SUM(A1:A5)"
    finally:
        wb.close()
    doc_info = json.loads(read_document(ws, "safe.xlsx"))
    assert doc_info["sha256"]

    # 3. Strict data converter: rejects duplicate headers, inconsistent columns, nested JSON -> CSV, invalid encoding
    (tmp_path / "dup.csv").write_text("a,a\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="μοναδικές επικεφαλίδες"):
        tool_convert_data_file(ws, "dup.csv", target_format="json")

    (tmp_path / "ragged.csv").write_text("a,b\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Ασυνεπές πλήθος στηλών"):
        tool_convert_data_file(ws, "ragged.csv", target_format="json")

    (tmp_path / "nested.json").write_text(json.dumps([{"a": [1, 2]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="σύνθετα JSON"):
        tool_convert_data_file(ws, "nested.json", target_format="csv")

    (tmp_path / "bad_utf8.csv").write_bytes(b"a,b\n\xff\xfe,1\n")
    with pytest.raises(ValueError, match="κωδικοποίηση"):
        tool_convert_data_file(ws, "bad_utf8.csv", target_format="json", encoding="utf-8")

    (tmp_path / "utf16.csv").write_bytes("όνομα,τιμή\nΆλφα,10\n".encode("utf-16"))
    md = tool_convert_data_file(ws, "utf16.csv", target_format="markdown")
    assert "Άλφα" in md and "10" in md

    # 4. Hardened git_status_diff on a real git repo
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    g_out = git_status_diff(ws)
    assert "[Git Status]" in g_out and "a.txt" in g_out and "+hello world" in g_out


# --------------------------------------------------------------------------- Package E: Tree-sitter + Markup + Graceful Fallback


def test_package_e_treesitter_multilang_and_graceful_fallback(tmp_path):
    from akatikito.reading import SOURCE_INDEX
    from akatikito.repo_map import RepoMap
    from akatikito.syntax_index import symbols as ts_symbols

    # 1. C++ template + Rust attributes + Java record/method + C# expression-bodied + PHP + HTML embedded JS + XML XXE blocked
    (tmp_path / "math.cpp").write_text(
        "template <typename T>\nclass Box {\npublic:\n  T get() const { return val; }\n};\n",
        encoding="utf-8",
    )
    (tmp_path / "lib.rs").write_text(
        "#[inline]\npub fn compute(x: i32) -> i32 {\n    x + 1\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "App.java").write_text(
        "public class App {\n  @Override\n  public int run(int x) {\n    return x * 2;\n  }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "Svc.cs").write_text(
        "public class Svc {\n  public int Double(int x) => x * 2;\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "page.html").write_text(
        "<html><body><button onclick=\"greet('hi')\">Go</button><script>function greet(m){ return m; }</script></body></html>\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.xml").write_text(
        "<!DOCTYPE x [<!ENTITY lol 'bomb'>]><root>&lol;</root>\n",
        encoding="utf-8",
    )
    (tmp_path / "same_line.js").write_text("function a(){return 1;} function b(){return 2;}\n", encoding="utf-8")

    # Verify Tree-sitter real parsing when installed
    cpp_res = ts_symbols((tmp_path / "math.cpp").read_text(encoding="utf-8"), ".cpp")
    if not cpp_res.get("missing_parser"):
        assert any("Box" in s["name"] for s in cpp_res["symbols"])
        assert any("get" in s["name"] for s in cpp_res["symbols"])

        rs_out = SOURCE_INDEX.outline(tmp_path / "lib.rs")
        assert "compute" in rs_out

        html_out = SOURCE_INDEX.outline(tmp_path / "page.html")
        assert "greet" in html_out

        xml_res = ts_symbols((tmp_path / "bad.xml").read_text(encoding="utf-8"), ".xml")
        assert "entities" in xml_res["notice"].lower() or "entity" in xml_res["notice"].lower()

        # Ambiguous single-line multi-function detection
        first, last, _, label = SOURCE_INDEX.span(tmp_path / "same_line.js", {"anchor_line": 1})
        assert first == 1 and last == 1
        assert "Αμφίσημη" in label

    # RepoMap renders all languages cleanly
    rmap = RepoMap(tmp_path).render()
    assert "math.cpp" in rmap and "lib.rs" in rmap and "App.java" in rmap and "Svc.cs" in rmap

    # 1b. Official offline framework template parsers (Vue, Svelte, Angular)
    vue_res = ts_symbols(
        '<template><div v-if="ok">{{ msg }}</div></template>\n'
        '<script setup lang="ts">function greet(x: string){ return x; }</script>\n'
        '<style>.card { color: red; }</style>\n',
        ".vue",
    )
    assert not vue_res.get("missing_parser")
    assert any("greet" in s["name"] for s in vue_res["symbols"])
    assert any(".card" in s["name"] for s in vue_res["symbols"])

    sv_res = ts_symbols(
        "<script>function add(a,b){return a+b;}</script><button>{name}</button><style>button{color:blue}</style>\n",
        ".svelte",
    )
    assert not sv_res.get("missing_parser")
    assert any("add" in s["name"] for s in sv_res["symbols"])

    ng_res = ts_symbols('<section (click)="save()">{{ title }}</section>\n', ".angular")
    assert not ng_res.get("missing_parser")
    assert any("section" in s["name"] for s in ng_res["symbols"])

    # 2. Graceful fallback when tree-sitter grammar is missing!
    SOURCE_INDEX.cache.clear()
    with mock.patch("akatikito.syntax_index.symbols", return_value={"symbols": [], "notice": "missing", "cacheable": False, "refs": [], "missing_parser": True}):
        out_rs = SOURCE_INDEX.outline(tmp_path / "lib.rs")
        out_java = SOURCE_INDEX.outline(tmp_path / "App.java")
        out_cs = SOURCE_INDEX.outline(tmp_path / "Svc.cs")
        assert "compute" in out_rs
        assert "App.run" in out_java or "run" in out_java
        assert "Double" in out_cs
