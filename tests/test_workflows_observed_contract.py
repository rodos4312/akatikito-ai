"""Characterization tests for workflows.observed across all 5 categories (Q4.6)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from akatikito.workflows import observed


def _call(name, **args):
    return SimpleNamespace(kind="tool_call", data={"name": name, "arguments": json.dumps(args)})


def _res(name, result="ok", path=""):
    return SimpleNamespace(kind="tool_result", data={"name": name, "result": result, "path": path})


def test_observed_research_and_paging_dedup(tmp_path):
    # Paging the exact same file 3 times should deduplicate to 1 row -> None
    same_file = [
        _call("read_file", path="a.txt", start=1, end=10), _res("read_file", "chunk1"),
        _call("read_file", path="a.txt", start=11, end=20), _res("read_file", "chunk2"),
        _call("read_file", path="a.txt", start=21, end=30), _res("read_file", "chunk3"),
    ]
    assert observed(same_file, root=tmp_path) is None

    # 3 distinct reads across >=2 sources -> research
    events = [
        _call("web_search", query="python"), _res("web_search", "results"),
        _call("web_fetch", url="https://a.example"), _res("web_fetch", "body a"),
        _call("web_fetch", url="https://b.example"), _res("web_fetch", "body b"),
    ]
    out = observed(events, root=tmp_path)
    assert out is not None
    assert out["category"] == "research"
    assert out["evidence"] == "sources-and-report"
    assert out["source_count"] >= 2


def test_observed_documents_planning_and_analysis():
    doc_events = [
        _call("read_file", path="notes.md"), _res("read_file", "notes"),
        _call("read_file", path="data.csv"), _res("read_file", "csv"),
        _call("create_pdf_document", path="out.pdf"), _res("create_pdf_document", "ΕΓΙΝΕ: out.pdf"),
    ]
    doc = observed(doc_events)
    assert doc is not None and doc["category"] == "documents"

    plan_events = [
        _call("read_file", path="a.py"), _res("read_file", "a"),
        _call("read_file", path="b.py"), _res("read_file", "b"),
        _call("update_todos", items=["step1"]), _res("update_todos", "ΕΓΙΝΕ"),
    ]
    plan = observed(plan_events)
    assert plan is not None and plan["category"] == "planning"

    analysis_events = [
        _call("read_file", path="a.py"), _res("read_file", "a"),
        _call("read_file", path="b.py"), _res("read_file", "b"),
        _call("run_command", command="pytest"), _res("run_command", "returncode=0\nok"),
    ]
    analysis = observed(analysis_events)
    assert analysis is not None and analysis["category"] == "analysis"


def test_observed_organization_readback_and_failures():
    item = {"id": 7, "text": "call mom"}
    org_events = [
        _call("list_reminders"), _res("list_reminders", json.dumps([])),
        _call("add_reminder", text="call mom"), _res("add_reminder", "ΕΓΙΝΕ: " + json.dumps(item)),
        _call("list_reminders"), _res("list_reminders", json.dumps([item])),
    ]
    org = observed(org_events)
    assert org is not None and org["category"] == "organization" and org["evidence"] == "readback"

    # Failed tool invalidates the whole workflow
    bad_events = org_events + [_call("run_command", command="false"), _res("run_command", "returncode=1")]
    assert observed(bad_events) is None

    # MCP server readback verification
    traits = {
        "mcp_read": {"readonly": True, "server": "cal"},
        "mcp_write": {"readonly": False, "server": "cal"},
    }
    mcp_events = [
        _call("mcp_read", id="1"), _res("mcp_read", "old"),
        _call("mcp_write", id="1"), _res("mcp_write", "updated"),
        _call("mcp_read", id="2"), _res("mcp_read", "verified"),
    ]
    mcp_org = observed(mcp_events, traits=traits)
    assert mcp_org is not None and mcp_org["category"] == "organization"
