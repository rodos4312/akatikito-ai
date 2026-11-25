"""Tests for new extensibility & UX features: project tree explorer, UI MCP management, owner skill creation, rich markdown."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito import file_view, memory, settings
from akatikito.app_api import AppState
from akatikito.mcp_client import MCPHub
from akatikito.skills import Skills


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DIR", tmp_path / "memory")
    monkeypatch.setattr(settings, "DEFAULT_PATH", tmp_path / "settings.json")


def test_list_tree_navigates_directories_and_skips_internal_folders(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Title\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / ".agent").mkdir()

    root_tree = file_view.list_tree(tmp_path, ".")
    assert root_tree["path"] == "."
    names = [e["name"] for e in root_tree["entries"]]
    assert names == ["src", "README.md"]
    assert root_tree["entries"][0]["type"] == "dir"
    assert root_tree["entries"][1]["type"] == "file"

    sub_tree = file_view.list_tree(tmp_path, "src")
    assert sub_tree["path"] == "src"
    assert [e["path"] for e in sub_tree["entries"]] == ["src/main.py"]

    with pytest.raises(PermissionError):
        file_view.list_tree(tmp_path, "../outside")


def test_mcp_hub_save_and_remove_server_via_app_state(tmp_path):
    state = AppState()
    state.mcp = MCPHub(tmp_path / "mcp.json")

    saved = state.mcp_save({
        "name": "local-fetch",
        "config": {
            "transport": "stdio",
            "command": "npx.cmd",
            "args": ["-y", "@modelcontextprotocol/server-fetch"],
            "readOnlyTools": ["fetch"],
        },
    })
    assert saved.get("ok") is True
    assert "local-fetch" in saved["mcp"]["configured"]
    assert saved["mcp"]["configs"]["local-fetch"]["command"] == "npx.cmd"

    # Reject invalid server names
    bad = state.mcp_save({"name": "../bad", "config": {"transport": "stdio", "command": "cmd"}})
    assert bad.get("error")

    removed = state.mcp_remove("local-fetch")
    assert removed.get("ok") is True
    assert "local-fetch" not in removed["mcp"]["configured"]


def test_owner_direct_skill_creation_via_app_state(tmp_path):
    state = AppState()
    state.settings.project = str(tmp_path)

    res = state.learning_action({
        "project": str(tmp_path),
        "action": "skill_create",
        "name": "summarize-report",
        "description": "Δομημένη σύνοψη αναφοράς",
        "scope": "project",
        "steps": ["Διάβασε το αρχείο", "Εξήγαγε τα σημεία", "Γράψε τη σύνοψη"],
    })
    assert res.get("ok") is True
    catalog = Skills(tmp_path).catalog()
    assert any(s["name"] == "summarize-report" and s["source"] == "project" for s in catalog)
    content = Skills(tmp_path).read("summarize-report")
    assert "1. Διάβασε το αρχείο" in content


def test_web_search_parses_duckduckgo_and_wikipedia_without_real_network(tmp_path, monkeypatch):
    from akatikito import network_tools as net
    from akatikito.workspace import Workspace

    ddg_html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">'
        'Example <b>Article</b></a>'
        '<a class="result__snippet">Χρήσιμη περίληψη από DuckDuckGo.</a>'
    )
    wiki_json = json.dumps({
        "query": {
            "search": [
                {"title": "Τεχνητή νοημοσύνη", "snippet": "Η <span>τεχνητή νοημοσύνη</span> στην επιστήμη."}
            ]
        }
    })

    def fake_get(url, accept="", api_only=False):
        if "duckduckgo.com" in url:
            return net.Response(url, ddg_html.encode("utf-8"), {"content-type": "text/html"})
        return net.Response(url, wiki_json.encode("utf-8"), {"content-type": "application/json"})

    monkeypatch.setattr(net, "_get", fake_get)
    ws = Workspace(tmp_path)
    out = net.web_search(ws, "Τεχνητή νοημοσύνη", max_results=4)
    assert "https://example.com/article" in out
    assert "Example Article" in out
    assert "Χρήσιμη περίληψη από DuckDuckGo." in out
    assert "https://el.wikipedia.org/wiki/" in out


def test_create_word_document_and_excel_sheet_and_read_back(tmp_path):
    from akatikito.features import create_word_document, create_excel_sheet, read_document
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)
    res_doc = create_word_document(
        ws,
        "report.docx",
        title="Μηνιαία Αναφορά",
        paragraphs=["Πρώτη παράγραφος δοκιμής."],
        table=[["Κατηγορία", "Ποσό"], ["Ενοίκιο", "500€"]],
    )
    assert res_doc.startswith("ΕΓΙΝΕ")
    doc_data = json.loads(read_document(ws, "report.docx"))
    assert "Μηνιαία Αναφορά" in doc_data["text"]
    assert "500€" in doc_data["text"]

    res_xls = create_excel_sheet(
        ws,
        "budget.xlsx",
        sheet_name="Έξοδα",
        headers=["Μήνας", "Σύνολο"],
        rows=[["Νοέμβριος", "1200"]],
    )
    assert res_xls.startswith("ΕΓΙΝΕ")
    xls_data = json.loads(read_document(ws, "budget.xlsx"))
    assert "[Έξοδα]" in xls_data["text"]
    assert "Νοέμβριος | 1200" in xls_data["text"]


def test_agenda_ui_endpoints_crud(tmp_path):
    state = AppState()
    state.settings.project = str(tmp_path)

    added = state.agenda_add({"title": "Ραντεβού με λογιστή", "date": "2026-12-01", "time": "11:30"})
    assert added.get("ok") is True
    task_id = added["task"]["id"]
    assert len(state.agenda_list()["tasks"]) == 1

    toggled = state.agenda_toggle(task_id, True)
    assert toggled.get("ok") is True
    assert toggled["tasks"][0]["done"] is True

    deleted = state.agenda_delete(task_id)
    assert deleted.get("ok") is True
    assert state.agenda_list()["tasks"] == []


def test_smart_skill_similarity_and_auto_refine_instead_of_duplicate(tmp_path):
    s = Skills(tmp_path)
    s.save_owner_skill(
        name="excel-sales-report",
        description="Δημιουργία οικονομικής αναφοράς πωλήσεων σε Excel (.xlsx)",
        steps=[
            "Διάβασε τα στοιχεία πωλήσεων από το αρχείο.",
            "Υπολόγισε τα σύνολα ανά κατηγορία.",
            "Δημιούργησε το φύλλο Excel με create_excel_sheet.",
        ],
        category="office",
        triggers=["excel", "πωλήσεις", "οικονομική αναφορά"],
        notes=["Το όνομα φύλλου δεν πρέπει να ξεπερνά τους 31 χαρακτήρες."],
    )

    # 1. Task auto-matching finds the skill
    matched = s.match_for_task("Φτιάξε μια οικονομική αναφορά πωλήσεων σε Excel")
    assert matched and matched[0]["name"] == "excel-sales-report"
    assert "💡 Σχετικά υπάρχοντα skills" in s.prompt(task="Φτιάξε μια οικονομική αναφορά πωλήσεων σε Excel")

    # 2. Trying to propose a similar skill under a different name without reading it alerts the agent!
    msg = s.propose(
        name="create-excel-sales-report",
        description="Παραγωγή αναφοράς πωλήσεων σε αρχείο Excel",
        steps=[
            "Φόρτωσε τα δεδομένα πωλήσεων.",
            "Οργάνωσε τις στήλες και τα σύνολα.",
            "Κάλεσε create_excel_sheet για εξαγωγή.",
            "Έλεγξε το αρχείο με read_document.",
        ],
        direct_learn=True,
    )
    assert msg.startswith("ΕΝΤΟΠΙΣΤΗΚΕ ΠΑΡΟΜΟΙΟ SKILL")
    assert "excel-sales-report" in msg
    assert not s.view()["proposals"]

    # 3. After reading the similar skill with use_skill (read), proposing a similar skill auto-merges into existing!
    s.read("excel-sales-report")
    merged_msg = s.propose(
        name="create-excel-sales-report",
        description="Παραγωγή αναφοράς πωλήσεων σε αρχείο Excel με επαλήθευση",
        steps=[
            "Φόρτωσε τα δεδομένα πωλήσεων.",
            "Οργάνωσε τις στήλες και τα σύνολα.",
            "Κάλεσε create_excel_sheet για εξαγωγή.",
            "Έλεγξε το αρχείο με read_document.",
        ],
        direct_learn=True,
    )
    assert "συγχωνεύτηκε στο υπάρχον παρόμοιο 'excel-sales-report'" in merged_msg
    proposals = s.view()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["name"] == "excel-sales-report"
    assert proposals[0]["mode"] == "refine"
    assert proposals[0]["merged_from"] == "create-excel-sales-report"


def test_patch_skill_surgical_updates_and_notes(tmp_path):
    s = Skills(tmp_path)
    s.save_owner_skill(
        name="word-contract",
        description="Δημιουργία σύμβασης σε Word",
        steps=[
            "Συγκέντρωσε τα στοιχεία των συμβαλλομένων.",
            "Δομήσε τις παραγράφους όρων.",
            "Κάλεσε create_word_document.",
        ],
        category="office",
    )

    patch_res = s.patch(
        name="word-contract",
        replace_step={"index": 2, "text": "Δομήσε τις παραγράφους όρων και τον πίνακα αμοιβών."},
        add_steps=["Επαλήθευσε το παραγόμενο .docx με read_document."],
        add_notes=["Προσοχή: η κατάληξη πρέπει να είναι πάντα .docx."],
        triggers=["σύμβαση", "word", "docx"],
    )
    assert patch_res.startswith("ΠΡΟΤΑΣΗ SKILL: word-contract")
    prop = s.view()["proposals"][0]
    s.answer(prop["id"], True, expected=prop["signature"])

    updated_text = s.read("word-contract")
    assert "2. Δομήσε τις παραγράφους όρων και τον πίνακα αμοιβών." in updated_text
    assert "4. Επαλήθευσε το παραγόμενο .docx με read_document." in updated_text
    assert "### Σημειώσεις & Παγίδες" in updated_text
    assert "- Προσοχή: η κατάληξη πρέπει να είναι πάντα .docx." in updated_text


def test_slash_learn_command_and_search_past_sessions(tmp_path):
    from akatikito.agent import Akatikito
    from akatikito.config import Config
    from akatikito.model import ScriptedModel, tool_call
    from akatikito.sessions import SessionStore
    from akatikito.features import search_past_sessions
    from akatikito.workspace import Workspace

    # 1. Create a saved past session and search it
    store = SessionStore(tmp_path)
    sid = store.create()
    store.save(
        sid,
        transcript=[
            {"role": "user", "text": "Πώς λύσαμε το bug με την κωδικοποίηση cp1253 στα Windows;"},
            {"role": "assistant", "text": "Προσθέσαμε PYTHONUTF8=1 και sys.stdout.reconfigure(encoding='utf-8')."},
        ],
    )
    ws = Workspace(tmp_path)
    found = json.loads(search_past_sessions(ws, "cp1253"))
    assert found["total"] == 1
    assert "PYTHONUTF8=1" in found["sessions"][0]["matches"][1]["excerpt"]

    # 2. Agent responds to /learn by proposing a rich skill directly without waiting for count >= 2
    model = ScriptedModel([
        {
            "tool_calls": [
                {
                    "name": "propose_skill",
                    "arguments": {
                        "name": "windows-utf8-fix",
                        "description": "Ρύθμιση UTF-8 κωδικοποίησης κονσόλας στα Windows",
                        "category": "debugging",
                        "triggers": ["cp1253", "utf-8", "unicode"],
                        "steps": [
                            "Έλεγξε τα αρχεία εκκίνησης για PYTHONIOENCODING=utf-8.",
                            "Πρόσθεσε sys.stdout.reconfigure(encoding='utf-8') στην εκκίνηση.",
                            "Τρέξε τα smoke tests για επιβεβαίωση ελληνικών χαρακτήρων.",
                        ],
                        "notes": ["Στα Windows η προεπιλογή κονσόλας είναι cp1253 ή cp737."],
                    },
                }
            ]
        },
        {"content": "Δημιούργησα την πρόταση skill /windows-utf8-fix για έγκριση."},
    ])
    agent = Akatikito(str(tmp_path), Config(auto_mode=True, max_steps=4), model)
    reply = agent.chat("/learn Ρύθμιση UTF-8 στα Windows")
    assert "windows-utf8-fix" in reply
    proposals = Skills(tmp_path).view()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["name"] == "windows-utf8-fix"
    assert proposals[0]["category"] == "debugging"


def test_skill_templates_required_tools_and_owner_delete(tmp_path):
    state = AppState()
    state.settings.project = str(tmp_path)

    res = state.learning_action({
        "project": str(tmp_path),
        "action": "skill_create",
        "name": "budget-sheet-gen",
        "description": "Πρότυπο μηνιαίου προϋπολογισμού σε Excel",
        "scope": "project",
        "category": "office",
        "triggers": ["προϋπολογισμός", "budget"],
        "required_tools": ["create_excel_sheet", "read_document"],
        "steps": [
            "Συγκέντρωσε τα έσοδα και έξοδα.",
            "Κάλεσε create_excel_sheet με το πρότυπο στηλών.",
            "Επαλήθευσε με read_document.",
        ],
        "notes": ["Μην ξεπερνάς τους 31 χαρακτήρες στο sheet_name."],
        "template": "headers: ['Κατηγορία', 'Ποσό (€)', 'Ημερομηνία']",
    })
    assert res.get("ok") is True

    s = Skills(tmp_path)
    cat = s.catalog()
    item = next(r for r in cat if r["name"] == "budget-sheet-gen")
    assert item["required_tools"] == ["create_excel_sheet", "read_document"]
    assert "Κατηγορία" in item["template"]

    # Reading with limited available_tools warns about missing tools
    read_out = s.read("budget-sheet-gen", available_tools={"read_file", "list_files"})
    assert "Μη διαθέσιμα στην τρέχουσα λειτουργία: create_excel_sheet, read_document" in read_out
    assert "### Πρότυπο / Snippet" in read_out

    # Delete via AppState preserves history
    del_res = state.learning_action({
        "project": str(tmp_path),
        "action": "skill_delete",
        "name": "budget-sheet-gen",
    })
    assert del_res.get("ok") is True
    assert not any(r["name"] == "budget-sheet-gen" for r in s.catalog())
    history = s._load()["history"]
    assert history[-1]["action"] == "owner_delete"
    assert "budget-sheet-gen" in history[-1]["before"]


def test_direct_slash_skill_execution_and_self_healing_telemetry(tmp_path):
    from akatikito.agent import Akatikito
    from akatikito.config import Config
    from akatikito.model import ScriptedModel, tool_call

    s = Skills(tmp_path)
    s.save_owner_skill(
        name="quick-check",
        description="Έλεγχος αρχείου config.json",
        steps=[
            "Διάβασε το αρχείο config.json.",
            "Έλεγξε αν υπάρχουν τα κλειδιά.",
            "Ανάφερε το αποτέλεσμα.",
        ],
    )

    # 1. User invokes /quick-check directly in chat; skill is preloaded and when read_file fails,
    # self-healing hint is surfaced in the next model step and recorded in telemetry!
    model = ScriptedModel([
        {"tool_calls": [tool_call("read_file", path="missing_config.json")]},
        {"content": "Το αρχείο έλειπε, ολοκλήρωσα τον έλεγχο εναλλακτικά."},
    ])
    agent = Akatikito(str(tmp_path), Config(auto_mode=False, max_steps=4), model)
    agent.set_mode("code")
    agent.chat("/quick-check Έλεγξε τις ρυθμίσεις")

    # Verify the first model call already had the preloaded skill without calling use_skill
    first_user_msgs = [m["content"] for m in model.seen[0] if m.get("role") == "user"]
    assert any("[ΑΜΕΣΗ ΕΚΤΕΛΕΣΗ SKILL /quick-check]" in str(c) for c in first_user_msgs)

    # Verify the second model call received the real-time self-healing hint
    second_msgs = "\n".join(str(m.get("content", "")) for m in model.seen[1])
    assert "ΑΥΤΟ-ΕΠΙΔΙΟΡΘΩΣΗ SKILL" in second_msgs
    assert "quick-check" in second_msgs

    # Verify telemetry recorded the failure and needs_healing flag
    cat_item = next(r for r in Skills(tmp_path).catalog() if r["name"] == "quick-check")
    assert cat_item["uses"] >= 1
    assert cat_item["telemetry"]["failures"] == 1
    assert cat_item["telemetry"]["needs_healing"] is True


def test_python_tool_plugins_and_enable_disable(tmp_path):
    from akatikito import modes
    from akatikito.tool_plugins import ToolPlugins, tool_system_diagnostics, tool_calculate_expression, tool_convert_data_file
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)
    # 1. System diagnostics
    diag = json.loads(tool_system_diagnostics(ws, "overview"))
    assert diag["disk_total_gb"] > 0
    assert diag["cpu_cores"] >= 1

    # 2. Smart calculator with VAT analysis
    calc = json.loads(tool_calculate_expression(ws, "(100 + 50) * 2", vat_rate=24))
    assert calc["result"] == 300.0
    assert calc["vat_analysis"]["if_net_then_gross"] == 372.0

    # 3. Data converter CSV -> Markdown & JSON
    (tmp_path / "items.csv").write_text("Προϊόν,Τιμή\nΛάπτοπ,850\nΟθόνη,220\n", encoding="utf-8")
    md_table = tool_convert_data_file(ws, "items.csv", target_format="markdown")
    assert "| Προϊόν | Τιμή |" in md_table
    assert "| Λάπτοπ | 850 |" in md_table

    # 4. Enable / Disable via ToolPlugins affects modes.allowed_tools
    tp = ToolPlugins()
    assert "calculate_expression" in modes.allowed_tools("ask")
    assert "convert_data_file" not in modes.allowed_tools("ask")  # non-readonly
    assert "convert_data_file" in modes.allowed_tools("code")

    tp.set_enabled("smart-calculator", False)
    assert "calculate_expression" not in modes.allowed_tools("ask")
    tp.set_enabled("smart-calculator", True)
    assert "calculate_expression" in modes.allowed_tools("ask")


def test_gateway_skills_mode_commands_and_media_attachments(tmp_path):
    from akatikito.channel_config import ChannelConfig
    from akatikito.channel_sdk import Inbound
    from akatikito.gateway import Gateway

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()

    Skills(proj).save_owner_skill(
        name="deploy-check",
        description="Έλεγχος πριν το deploy",
        steps=["Τρέξε τα tests.", "Έλεγξε τα configs.", "Επιβεβαίωσε το build."],
        category="devops",
    )

    ChannelConfig(data_dir / "channels.json").save(
        "telegram",
        {"token": "tok", "allowed_users": ["10"], "allowed_chats": [], "project": str(proj), "mode": "ask", "enabled": True},
        {"telegram": {}},
    )
    gw = Gateway(data_dir)

    # 1. /skills command over gateway
    e_skills = Inbound("telegram", "100", "10", "10", "1", "/skills")
    gw.receive("telegram", "100", "2", [e_skills])
    gw.process(gw.store.next())
    out1 = gw.store.outbox()[0]
    assert "/deploy-check" in out1["reply"]
    gw.store.claim_delivery(out1["id"])
    gw.store.delivery(out1["id"], "sent")

    # 2. /mode command over gateway
    e_mode = Inbound("telegram", "100", "10", "10", "2", "/mode assistant")
    gw.receive("telegram", "100", "3", [e_mode])
    gw.process(gw.store.next())
    out2 = gw.store.outbox()[0]
    assert "Βοηθός (assistant)" in out2["reply"]
    assert gw.configs["telegram"]["mode"] == "assistant"


def test_browser_fetch_chromium_dom_and_ssrf_guard(tmp_path, monkeypatch):
    from akatikito import network_tools as net
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)

    # 1. SSRF guard blocks localhost/private URLs before launching Chromium
    with pytest.raises(PermissionError):
        net.browser_fetch(ws, "http://127.0.0.1:8080/admin")
    with pytest.raises(PermissionError):
        net.browser_fetch(ws, "http://localhost/secret")

    # 2. Rendered DOM & Screenshot extraction via headless Chromium
    monkeypatch.setattr(net, "_public_url", lambda url: ("https", "example.com", 443, "/", ["93.184.216.34"]))
    monkeypatch.setattr(net, "find_chromium_binary", lambda: "C:/fake/msedge.exe")

    def fake_run_chromium(binary, url, wait_ms=2500, screenshot_target=None):
        if screenshot_target is not None:
            screenshot_target.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        return "<html><head><title>Dynamic SPA App</title></head><body><div id='app'>Δυναμικό κείμενο από JavaScript!</div></body></html>"

    monkeypatch.setattr(net, "_run_chromium_dom", fake_run_chromium)
    out = net.browser_fetch(ws, "https://example.com/spa", screenshot_path="page.png")
    assert "Chromium Rendered DOM" in out
    assert "Dynamic SPA App" in out
    assert "Δυναμικό κείμενο από JavaScript!" in out
    assert "page.png" in out
    assert (tmp_path / "page.png").is_file()


def test_web_news_rss_and_youtube_transcript(tmp_path, monkeypatch):
    from akatikito import network_tools as net
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Νέα ανακάλυψη στην Τεχνητή Νοημοσύνη</title>
        <link>https://news.example.com/ai-1</link>
        <pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate>
        <source>Καθημερινή</source>
        <description>Σημαντικές εξελίξεις στα τοπικά μοντέλα.</description>
      </item>
    </channel></rss>"""

    yt_html = (
        '<script>var ytInitialPlayerResponse = '
        + json.dumps({
            "videoDetails": {
                "title": "Μάθημα Python για Agents",
                "author": "Akatikito Channel",
                "shortDescription": "Πλήρης οδηγός δημιουργίας AI agents.",
            },
            "captions": {
                "playerCaptionsTracklistRenderer": {
                    "captionTracks": [
                        {"languageCode": "el", "baseUrl": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=el"}
                    ]
                }
            },
        }, ensure_ascii=False)
        + ";</script>"
    )
    yt_captions_xml = """<?xml version="1.0" encoding="utf-8" ?>
    <transcript>
      <text start="5.0" dur="3.2">Καλώς ήρθατε στο μάθημα για αυτόνομους agents.</text>
      <text start="65.0" dur="4.0">Σήμερα θα δούμε πώς δουλεύει ο Chromium.</text>
    </transcript>"""

    def fake_get(url, accept="", api_only=False):
        if "news.google.com" in url:
            return net.Response(url, rss_xml.encode("utf-8"), {"content-type": "application/rss+xml"})
        if "timedtext" in url:
            return net.Response(url, yt_captions_xml.encode("utf-8"), {"content-type": "text/xml"})
        return net.Response(url, yt_html.encode("utf-8"), {"content-type": "text/html"})

    monkeypatch.setattr(net, "_get", fake_get)

    news_out = net.web_news(ws, "Τεχνητή Νοημοσύνη", language="el")
    assert "Νέα ανακάλυψη στην Τεχνητή Νοημοσύνη" in news_out
    assert "Καθημερινή" in news_out
    assert "https://news.example.com/ai-1" in news_out

    yt_out = net.youtube_transcript(ws, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", language="el")
    assert "Μάθημα Python για Agents" in yt_out
    assert "Akatikito Channel" in yt_out
    assert "[00:05] Καλώς ήρθατε στο μάθημα για αυτόνομους agents." in yt_out
    assert "[01:05] Σήμερα θα δούμε πώς δουλεύει ο Chromium." in yt_out


def test_create_pdf_document_and_scheduled_auto_run_agenda(tmp_path, monkeypatch):
    import time
    from pypdf import PdfReader
    from akatikito.agent import Akatikito
    from akatikito.config import Config
    from akatikito.features import create_pdf_document, read_document, _find_unicode_ttf
    from akatikito.model import ScriptedModel
    from akatikito.sessions import SessionStore
    from akatikito.workspace import Workspace
    from akatikito import network_tools as net
    from akatikito import notify

    ws = Workspace(tmp_path)
    # Force pure-Python PDF fallback so we test the embedded-font/visible-text engine deterministically offline
    monkeypatch.setattr(net, "find_chromium_binary", lambda: None)
    pdf_res = create_pdf_document(
        ws,
        "offer.pdf",
        title="Επίσημη Οικονομική Προσφορά",
        paragraphs=["Αναλυτική περιγραφή υπηρεσιών."],
        table=[["Υπηρεσία", "Κόστος"], ["Ανάπτυξη AI Agent", "1.200€"]],
    )
    assert pdf_res.startswith("ΕΓΙΝΕ")
    pdf_path = tmp_path / "offer.pdf"
    assert pdf_path.is_file()

    reader = PdfReader(pdf_path)
    assert len(reader.pages) >= 1
    page = reader.pages[0]
    assert "/Contents" in page and len(page.get_contents().get_data().strip()) > 20
    assert "/Font" in page["/Resources"]
    if _find_unicode_ttf() is not None:
        f_obj = page["/Resources"]["/Font"]["/F1"].get_object()
        desc = f_obj["/DescendantFonts"][0].get_object()["/FontDescriptor"].get_object()
        assert "/FontFile2" in desc

    read_back = json.loads(read_document(ws, "offer.pdf"))
    assert read_back["path"] == "offer.pdf"
    assert "Επίσημη Οικονομική Προσφορά" in read_back["text"]
    assert "Αναλυτική περιγραφή υπηρεσιών." in read_back["text"]
    assert "Ανάπτυξη AI Agent" in read_back["text"]
    assert "1.200€" in read_back["text"]

    # Test scheduled autonomous agenda task (auto_run + repeat), durable queue on busy + immediate drain after send()!
    monkeypatch.setattr(notify, "notify", lambda *a, **kw: None)
    state = AppState()
    state.settings.project = str(tmp_path)
    added = state.agenda_add({
        "title": "Πρωινή σύνοψη ειδήσεων",
        "date": "2020-01-01",
        "time": "08:00",
        "repeat": "daily",
        "auto_run": True,
        "note": "Ψάξε τις σημερινές ειδήσεις με web_news",
    })
    assert added.get("ok") is True
    assert added["task"]["auto_run"] is True
    assert added["task"]["repeat"] == "daily"

    state.agent = type("FakeAgent", (), {"ws": ws})()
    state.busy = True
    state.reminder_tick()
    q_file = tmp_path / ".agent" / "auto_queue.json"
    assert q_file.is_file()
    assert len(state._pending_auto_tasks) == 1

    # Simulate app restart with a fresh AppState + real Akatikito + ScriptedModel
    state2 = AppState()
    state2.settings.project = str(tmp_path)
    state2.settings.checkpoints = False
    model = ScriptedModel([
        {"content": "OK manual"},
        {"content": "OK auto_run"},
    ])
    state2.agent = Akatikito(str(tmp_path), Config(auto_mode=True, checkpoints=False, max_steps=4), model)
    state2.store = SessionStore(tmp_path)
    state2._bind_permissions(state2.agent)
    restored = state2._load_auto_queue()
    assert len(restored) == 1
    assert restored[0]["title"] == "Πρωινή σύνοψη ειδήσεων"

    # Send a manual turn; when it finishes, finally block automatically drains and runs the queued auto_run task!
    res = state2.send("Κάνε χειροκίνητη εργασία πρώτα")
    assert res.get("ok") is True
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        q_cur = state2._load_auto_queue()
        if not state2.busy and len(q_cur) == 0 and len(state2.transcript) >= 4:
            break
        time.sleep(0.02)
    assert not state2.busy
    assert state2._load_auto_queue() == []
    texts = [m.get("text", "") for m in state2.transcript]
    assert "OK manual" in texts
    assert "OK auto_run" in texts
    assert any("ΠΡΟΓΡΑΜΜΑΤΙΣΜΕΝΗ ΑΥΤΟΜΑΤΗ ΕΡΓΑΣΙΑ: Πρωινή σύνοψη ειδήσεων" in t for t in texts)


def test_coding_engine_indentation_recovery_closest_hint_and_inline_diff(tmp_path):
    from akatikito.tools import edit_file, write_file
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)
    code = (
        "class Calculator:\n"
        "    def compute(self, x):\n"
        "        total = x + 10\n"
        "        return total\n"
    )
    (tmp_path / "calc.py").write_text(code, encoding="utf-8")

    # 1. LLM sends search/replace missing the 4-space class indentation -> auto-matches and re-indents!
    res = edit_file(
        ws,
        "calc.py",
        edits=[{
            "search": "def compute(self, x):\n    total = x + 10\n    return total",
            "replace": "def compute(self, x):\n    total = x * 20\n    return total",
        }],
    )
    assert res.startswith("ΕΓΙΝΕ")
    # Carries compact unified diff for inline chat preview
    assert "-        total = x + 10" in getattr(res, "diff", "")
    assert "+        total = x * 20" in getattr(res, "diff", "")
    updated = (tmp_path / "calc.py").read_text(encoding="utf-8")
    assert "    def compute(self, x):\n        total = x * 20\n        return total\n" in updated

    # 2. When search fails due to wrong expression, error includes closest-match line hint!
    err_res = edit_file(
        ws,
        "calc.py",
        edits=[{
            "search": "def compute(self, x):\n    total = x * 99999\n    return total",
            "replace": "def compute(self, x):\n    return 0",
        }],
    )
    assert err_res.startswith("ΣΦΑΛΜΑ")
    assert "Πλησιέστερο σημείο στο αρχείο" in err_res
    assert "total = x * 20" in err_res

    # 3. write_file with overwrite=True replaces existing file with backup and syntax check
    ow_res = write_file(ws, "calc.py", "def simple():\n    return 42\n", overwrite=True)
    assert ow_res.startswith("ΕΓΙΝΕ")
    assert "return 42" in (tmp_path / "calc.py").read_text(encoding="utf-8")


def test_multilanguage_outline_anchor_and_git_status_diff(tmp_path):
    import subprocess
    from akatikito.tools import read_file
    from akatikito.features import git_status_diff
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)
    js_code = (
        "export async function fetchUserData(userId) {\n"
        "  const url = '/api/users/' + userId;\n"
        "  return url;\n"
        "}\n\n"
        "class SessionManager {\n"
        "  constructor() {\n"
        "    this.active = true;\n"
        "  }\n"
        "}\n"
    )
    (tmp_path / "client.js").write_text(js_code, encoding="utf-8")

    # 1. Outline works on JavaScript/TypeScript/HTML files
    outline = read_file(ws, "client.js", outline=True)
    assert "fetchUserData" in outline
    assert "SessionManager" in outline

    # 2. Anchor line extracts complete JS function block
    span_read = read_file(ws, "client.js", anchor_line=2)
    assert "fetchUserData" in span_read
    assert "SessionManager" not in span_read

    # 3. git_status_diff works in read-only mode
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    git_out = git_status_diff(ws)
    assert "[Git Status]" in git_out
    assert "client.js" in git_out


def test_checkpoint_scan_consistency_on_concurrent_change(tmp_path, monkeypatch):
    import os
    from akatikito.checkpoints import Checkpoints

    target = tmp_path / "race.txt"
    initial_bytes = b"initial-version\n"  # 16 bytes
    same_len_mut = b"MUTATED-version\n"   # 16 bytes (same size + preserved mtime!)
    target.write_bytes(initial_bytes)
    vanishing = tmp_path / "vanishing.txt"
    vanishing.write_bytes(b"transient-file\n")

    store = Checkpoints(tmp_path)
    orig_git = store._git
    begin_raced = {"done": False}
    delete_raced = {"done": False}

    def raced_git(*args, data=None):
        if args and args[0] == "hash-object" and "--stdin-paths" in args and not begin_raced["done"]:
            begin_raced["done"] = True
            # Mutate ONLY during begin() with identical length AND identical mtime_ns!
            st = target.stat()
            target.write_bytes(same_len_mut)
            os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        return orig_git(*args, data=data)

    monkeypatch.setattr(store, "_git", raced_git)
    key = store.begin()
    assert begin_raced["done"]
    target.write_bytes(b"final-version\n")
    store.finish(key)

    # diff() and undo() verify sha256 against blob; must NOT raise "Αλλοιωμένο checkpoint."
    diffs = store.diff(key)
    assert any(d["path"] == "race.txt" for d in diffs)
    assert store.undo(key) == ["race.txt"]
    assert target.read_bytes() == initial_bytes

    # Also verify graceful fallback when a scanned file disappears right before --stdin-paths
    def vanished_git(*args, data=None):
        if args and args[0] == "hash-object" and "--stdin-paths" in args and not delete_raced["done"]:
            delete_raced["done"] = True
            vanishing.unlink(missing_ok=True)
        return orig_git(*args, data=data)

    monkeypatch.setattr(store, "_git", vanished_git)
    key2 = store.begin()
    assert delete_raced["done"]
    rec2 = store.load(key2)
    assert "vanishing.txt" in rec2["before"]
    assert store._blob(rec2["before"]["vanishing.txt"]) == b"transient-file\n"


def test_converter_respects_write_guard_backup_and_export_chat(tmp_path, monkeypatch):
    import subprocess
    import threading
    import time
    from types import SimpleNamespace
    from akatikito import network_tools as net
    from akatikito.agent import Akatikito
    from akatikito.config import Config
    from akatikito.features import create_word_document, create_excel_sheet, create_pdf_document
    from akatikito.model import ScriptedModel
    from akatikito.tool_plugins import tool_convert_data_file
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    existing = tmp_path / "out.json"
    existing.write_text('{"old": true}\n', encoding="utf-8")

    # 1. Converter honors write_guard + review_write + backup
    guard_calls = []
    ws.write_guard = lambda target, orig, upd: guard_calls.append((target.name, orig))
    res = tool_convert_data_file(ws, "data.csv", target_format="json", output_path="out.json")
    assert res.startswith("ΕΓΙΝΕ")
    assert guard_calls and guard_calls[0][0] == "out.json"
    assert '"old": true' in guard_calls[0][1]
    assert len(ws.list_backups()) == 1

    # 2. All document writers & screenshot honor write_guard, review_write denial/approval, and backup on overwrite
    monkeypatch.setattr(net, "find_chromium_binary", lambda: None)
    docx_p = tmp_path / "doc.docx"
    xlsx_p = tmp_path / "sheet.xlsx"
    pdf_p = tmp_path / "rep.pdf"
    shot_p = tmp_path / "shot.png"
    docx_p.write_bytes(b"OLD_DOCX")
    xlsx_p.write_bytes(b"OLD_XLSX")
    pdf_p.write_bytes(b"OLD_PDF")
    shot_p.write_bytes(b"OLD_PNG")

    # Deny via review_write -> unchanged
    ws.write_guard = None
    ws.review_write = lambda *_a: False
    assert "OWNER_DENIED" in create_word_document(ws, "doc.docx", title="T")
    assert "OWNER_DENIED" in create_excel_sheet(ws, "sheet.xlsx", rows=[["1"]])
    assert "OWNER_DENIED" in create_pdf_document(ws, "rep.pdf", title="PDF")
    assert docx_p.read_bytes() == b"OLD_DOCX"
    assert xlsx_p.read_bytes() == b"OLD_XLSX"
    assert pdf_p.read_bytes() == b"OLD_PDF"

    # write_guard PermissionError blocks all writers including screenshot before Chromium runs
    def block_guard(*_a):
        raise PermissionError("blocked by guard")
    ws.write_guard = block_guard
    ws.review_write = lambda *_a: True
    for fn, args in [
        (create_word_document, (ws, "doc.docx", "T")),
        (create_excel_sheet, (ws, "sheet.xlsx", [["1"]])),
        (create_pdf_document, (ws, "rep.pdf", "PDF")),
    ]:
        with pytest.raises(PermissionError):
            fn(*args)
    monkeypatch.setattr(net, "_public_url", lambda url: ("https", "example.com", 443, "/", ["93.184.216.34"]))
    with pytest.raises(PermissionError):
        net.browser_fetch(ws, "https://example.com/", screenshot_path="shot.png")

    # Approve writes -> creates backups and updates files atomically
    ws.write_guard = None
    ws.review_write = lambda *_a: True
    backups_before = len(ws.list_backups())
    assert create_word_document(ws, "doc.docx", title="Νέο Word").startswith("ΕΓΙΝΕ")
    assert create_excel_sheet(ws, "sheet.xlsx", rows=[["Α", "Β"]]).startswith("ΕΓΙΝΕ")
    assert create_pdf_document(ws, "rep.pdf", title="Νέο PDF", paragraphs=["Σώμα"]).startswith("ΕΓΙΝΕ")

    monkeypatch.setattr(net, "find_chromium_binary", lambda: "C:/fake/chrome.exe")
    def fake_shot_dom(binary, url, wait_ms=2500, screenshot_target=None):
        if screenshot_target is not None:
            screenshot_target.write_bytes(b"\x89PNG\r\n\x1a\nNEW_PNG")
        return "<html><title>Shot</title><body>OK</body></html>"
    monkeypatch.setattr(net, "_run_chromium_dom", fake_shot_dom)
    # First deny screenshot via review_write -> NetworkError with OWNER_DENIED and original PNG untouched
    ws.review_write = lambda *_a: False
    with pytest.raises(net.NetworkError, match="OWNER_DENIED"):
        net.browser_fetch(ws, "https://example.com/", screenshot_path="shot.png")
    assert shot_p.read_bytes() == b"OLD_PNG"
    # Then allow -> committed + backed up
    ws.review_write = lambda *_a: True
    net.browser_fetch(ws, "https://example.com/", screenshot_path="shot.png")
    assert shot_p.read_bytes().startswith(b"\x89PNG")
    assert len(ws.list_backups()) >= backups_before + 4

    # 3. Verify AppState._bind_permissions review handles large binary PDF/DOCX without hitting 120k text limit
    state = AppState()
    state.settings.project = str(tmp_path)
    state.settings.cautious_edits = True
    agent = Akatikito(str(tmp_path), Config(auto_mode=True, checkpoints=False), ScriptedModel([]))
    state._bind_permissions(agent)
    approved = []
    def approve_once():
        approved.append(agent.ws.review_write(pdf_p, b"\x00" * 200_000, b"\x00" * 250_000))
    t = threading.Thread(target=approve_once, daemon=True)
    t.start()
    deadline = time.monotonic() + 3
    while not state.approvals.pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    pending = state.approvals.pending()
    assert len(pending) == 1
    assert "δυαδικού αρχείου" in pending[0]["details"]["diff"]
    state.approvals.respond(pending[0]["id"], True)
    t.join(timeout=3)
    assert approved == [True]

    # 4. Verify export_chat: headless fallback + webview SAVE dialog + webview cancel
    state.transcript = [
        {"role": "user", "text": "Γεια σου"},
        {"role": "assistant", "text": "Γεια! Πώς μπορώ να βοηθήσω;", "model": "test-model"},
    ]
    exp = state.export_chat()
    assert exp.get("ok") is True
    exported_file = tmp_path / exp["path"]
    assert exported_file.is_file()
    content = exported_file.read_text(encoding="utf-8")
    assert "Γεια σου" in content and "Πώς μπορώ να βοηθήσω;" in content

    picked_target = tmp_path / "picked-export.md"
    fake_win_ok = SimpleNamespace(create_file_dialog=lambda *_a, **_kw: (str(picked_target),))
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[fake_win_ok], SAVE_DIALOG=30, FileDialog=SimpleNamespace(SAVE=30)))
    exp_ok = state.export_chat()
    assert exp_ok.get("ok") is True and picked_target.is_file()

    fake_win_cancel = SimpleNamespace(create_file_dialog=lambda *_a, **_kw: None)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[fake_win_cancel], SAVE_DIALOG=30, FileDialog=SimpleNamespace(SAVE=30)))
    exp_cancel = state.export_chat()
    assert exp_cancel == {"cancelled": True}

    # 5. Verify real UI JS flow (applyMcpPreset -> parseMcpArgs -> saveMcpServer) handles paths with spaces & backslashes
    ui_dir = Path(__file__).resolve().parents[1] / "akatikito" / "ui"
    ui_js = "\n".join((ui_dir / f).read_text(encoding="utf-8") for f in ("chat.js", "files.js", "attachments.js", "learning.js", "settings.js"))
    fn_start = ui_js.index("function onMcpTransport()")
    fn_end = ui_js.index("async function removeMcpServer(", fn_start)
    fn_code = ui_js[fn_start:fn_end]
    node_script = (
        "const assert = require('node:assert/strict');\n"
        "const els = {};\n"
        "for (const id of ['mcp-name','mcp-transport','mcp-command','mcp-args','mcp-url','mcp-readonly','mcp-stdio-fields','mcp-http-fields','mcp-form']) {\n"
        "  els[id] = { value: '', style: { display: '' } };\n"
        "}\n"
        "const document = { getElementById: (id) => els[id] };\n"
        "let savedPayload = null;\n"
        "let currentProject = '';\n"
        "async function api(url, body) { savedPayload = body; return { ok: true }; }\n"
        "function showNotice() {}\n"
        "function toggleMcpForm() {}\n"
        "async function poll() {}\n"
        + fn_code + "\n"
        "(async () => {\n"
        "  for (const proj of ['D:/Owner/my project', 'D:\\\\Owner\\\\my project', 'C:/Work/one  two/repo']) {\n"
        "    currentProject = proj;\n"
        "    applyMcpPreset('filesystem');\n"
        "    await saveMcpServer();\n"
        "    assert.deepEqual(savedPayload.config.args, ['-y', '@modelcontextprotocol/server-filesystem', proj]);\n"
        "  }\n"
        "  assert.deepEqual(parseMcpArgs('-y pkg \"D:\\\\a b\\\\c\"'), ['-y', 'pkg', 'D:\\\\a b\\\\c']);\n"
        "  console.log('OK');\n"
        "})().catch(e => { console.error(e); process.exit(1); });\n"
    )
    node_res = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "OK" in node_res.stdout


def test_run_chromium_dom_cli_args_and_errors(tmp_path, monkeypatch):
    import subprocess
    from akatikito import network_tools as net

    recorded = []

    def fake_run(cmd, capture_output=True, timeout=25, creationflags=0):
        recorded.append(cmd)
        for arg in cmd:
            if arg.startswith("--screenshot="):
                Path(arg.split("=", 1)[1]).write_bytes(b"\x89PNG\r\n\x1a\nshot")
        if "--dump-dom" in cmd:
            return subprocess.CompletedProcess(cmd, 0, b"<html><body>Rendered</body></html>", b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(net.subprocess, "run", fake_run)
    shot = tmp_path / "shot.png"
    dom = net._run_chromium_dom("C:/fake/chrome.exe", "https://example.com/", wait_ms=1800, screenshot_target=shot)
    assert "Rendered" in dom
    assert shot.is_file()
    assert len(recorded) == 2
    assert any("--headless" in c and "--dump-dom" in c and "--virtual-time-budget=1800" in c for c in recorded)
    assert any(any(a.startswith("--screenshot=") for a in c) for c in recorded)

    # Non-zero return with empty stdout raises NetworkError
    monkeypatch.setattr(
        net.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"crash"),
    )
    with pytest.raises(net.NetworkError, match="crash"):
        net._run_chromium_dom("C:/fake/chrome.exe", "https://example.com/")

    # Timeout raises NetworkError via browser_fetch
    monkeypatch.setattr(net, "_public_url", lambda url: ("https", "example.com", 443, "/", ["93.184.216.34"]))
    monkeypatch.setattr(net, "find_chromium_binary", lambda: "C:/fake/chrome.exe")

    def raise_timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired("chrome", 25)

    monkeypatch.setattr(net.subprocess, "run", raise_timeout)
    from akatikito.workspace import Workspace
    with pytest.raises(net.NetworkError, match="χρονικό όριο"):
        net.browser_fetch(Workspace(tmp_path), "https://example.com/")


def test_real_chromium_dom_and_pdf_when_available(tmp_path):
    from akatikito import network_tools as net
    from akatikito.features import create_pdf_document, read_document
    from akatikito.workspace import Workspace

    binary = net.find_chromium_binary()
    if not binary:
        pytest.skip("No local Chromium/Chrome/Edge installed")

    html_file = tmp_path / "spa.html"
    html_file.write_text(
        "<!doctype html><html><head><title>Real Test</title></head>"
        "<body><div id='dyn'></div>"
        "<script>document.getElementById('dyn').textContent='REAL_CHROMIUM_JS_OK';</script>"
        "</body></html>",
        encoding="utf-8",
    )
    dom = net._run_chromium_dom(binary, html_file.as_uri(), wait_ms=1500)
    assert "REAL_CHROMIUM_JS_OK" in dom

    ws = Workspace(tmp_path)
    pdf_msg = create_pdf_document(
        ws,
        "chrome_out.pdf",
        title="Αναφορά Chromium PDF",
        paragraphs=["Κείμενο παραγόμενο από πραγματικό Chromium."],
        table=[["Πεδίο", "Τιμή"], ["Επιτυχία", "100%"]],
    )
    assert pdf_msg.startswith("ΕΓΙΝΕ")
    data = json.loads(read_document(ws, "chrome_out.pdf"))
    assert "Αναφορά Chromium PDF" in data["text"]
    assert "Κείμενο παραγόμενο από πραγματικό Chromium." in data["text"]
    assert "100%" in data["text"]


def test_multilanguage_structure_java_cs_cpp_rust_php_and_repomap(tmp_path):
    from akatikito.repo_map import RepoMap
    from akatikito.tools import read_file
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)

    # 1. Java: class, constructor, generic/throws methods, control-flow exclusion
    (tmp_path / "OrderService.java").write_text(
        "public class OrderService {\n"
        "    public OrderService(Repo repo) {\n"
        "        this.repo = repo;\n"
        "    }\n\n"
        "    public List<Order> findOrders(\n"
        "        String customerId,\n"
        "        int limit\n"
        "    ) throws IOException {\n"
        "        if (customerId == null) {\n"
        "            return List.of();\n"
        "        }\n"
        "        String brace = \"}\";\n"
        "        return repo.query(customerId, limit);\n"
        "    }\n\n"
        "    private static int helper(int a, int b) {\n"
        "        return a + b;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    java_out = read_file(ws, "OrderService.java", outline=True)
    assert "OrderService" in java_out
    assert "OrderService.OrderService" in java_out
    assert "OrderService.findOrders" in java_out
    assert "OrderService.helper" in java_out
    assert "| if:" not in java_out and "OrderService.if" not in java_out
    java_anchor = read_file(ws, "OrderService.java", anchor_line=13)
    assert "findOrders" in java_anchor and "repo.query" in java_anchor and "helper" not in java_anchor

    # 2. C#: Allman braces, namespace, async Task<T?>, expression-bodied method, record
    (tmp_path / "UserService.cs").write_text(
        "namespace MyApp.Services\n"
        "{\n"
        "    public sealed record UserDto(int Id);\n\n"
        "    public class UserService\n"
        "    {\n"
        "        public async Task<User?> GetUserAsync(int id)\n"
        "        {\n"
        "            if (id <= 0) return null;\n"
        "            return await _db.FindAsync(id);\n"
        "        }\n\n"
        "        public string Label(int n) => $\"#{n}\";\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    cs_out = read_file(ws, "UserService.cs", outline=True)
    assert "UserDto" in cs_out
    assert "UserService" in cs_out
    assert "UserService.GetUserAsync" in cs_out
    assert "UserService.Label" in cs_out
    cs_anchor = read_file(ws, "UserService.cs", anchor_line=9)
    assert "GetUserAsync" in cs_anchor and "Label" not in cs_anchor

    # 3. C / C++: standard functions, pointers, 2-line return type, class method, header prototype, digit sep
    (tmp_path / "engine.cpp").write_text(
        "#include <vector>\n"
        "static const char* parse_header(const char *buf, size_t len) {\n"
        "    int big = 1'000'000;\n"
        "    if (len == 0) { return nullptr; }\n"
        "    const char *s = \"}\";\n"
        "    return buf;\n"
        "}\n\n"
        "static std::string\n"
        "format_user(const User& u) const {\n"
        "    return u.name;\n"
        "}\n\n"
        "std::vector<int> Calculator::compute(int x) const {\n"
        "    return {x};\n"
        "}\n\n"
        "int main(int argc, char** argv) {\n"
        "    parse_header(\"a\", 1);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "math_api.h").write_text(
        "#pragma once\n"
        "int add_numbers(int a, int b);\n",
        encoding="utf-8",
    )
    cpp_out = read_file(ws, "engine.cpp", outline=True)
    assert "parse_header" in cpp_out
    assert "format_user" in cpp_out
    assert "Calculator::compute" in cpp_out
    assert "main" in cpp_out
    assert "| if:" not in cpp_out
    cpp_anchor = read_file(ws, "engine.cpp", anchor_line=6)
    assert "parse_header" in cpp_anchor and "return buf;" in cpp_anchor and "format_user" not in cpp_anchor
    h_out = read_file(ws, "math_api.h", outline=True)
    assert "add_numbers" in h_out

    # 4. Rust: pub struct, pub trait, impl methods, pub fn, pub(crate) async fn, lifetimes 'a, char literal '}'
    (tmp_path / "lib.rs").write_text(
        "pub struct Config {\n"
        "    pub name: String,\n"
        "}\n\n"
        "pub trait Runner {\n"
        "    fn run(&self);\n"
        "}\n\n"
        "impl Config {\n"
        "    pub fn new<'a>(name: &'a str) -> Self {\n"
        "        let c = '}';\n"
        "        Self { name: name.to_string() }\n"
        "    }\n\n"
        "    pub(crate) async fn fetch(&self) -> Result<(), String> {\n"
        "        Ok(())\n"
        "    }\n"
        "}\n\n"
        "pub fn main() {\n"
        "    println!(\"ok\");\n"
        "}\n",
        encoding="utf-8",
    )
    rs_out = read_file(ws, "lib.rs", outline=True)
    assert "Config" in rs_out
    assert "Runner" in rs_out
    assert "Runner.run" in rs_out
    assert "Config.new" in rs_out
    assert "Config.fetch" in rs_out
    assert "main" in rs_out
    rs_anchor = read_file(ws, "lib.rs", anchor_line=12)
    assert "pub fn new" in rs_anchor and "Self { name:" in rs_anchor and "pub(crate) async fn fetch" not in rs_anchor

    # 5. PHP: class, public/private static methods, standalone function, # comment with brace
    (tmp_path / "InvoiceController.php").write_text(
        "<?php\n"
        "final class InvoiceController {\n"
        "    # comment with }\n"
        "    public function __construct($db) {\n"
        "        $this->db = $db;\n"
        "    }\n\n"
        "    private static function formatAmount(float $v): string {\n"
        "        return number_format($v, 2);\n"
        "    }\n"
        "}\n\n"
        "function calculate_vat(float $net): float {\n"
        "    return $net * 0.24;\n"
        "}\n",
        encoding="utf-8",
    )
    php_out = read_file(ws, "InvoiceController.php", outline=True)
    assert "InvoiceController" in php_out
    assert "InvoiceController.__construct" in php_out
    assert "InvoiceController.formatAmount" in php_out
    assert "calculate_vat" in php_out
    php_anchor = read_file(ws, "InvoiceController.php", anchor_line=9)
    assert "formatAmount" in php_anchor and "calculate_vat" not in php_anchor

    # 6. TypeScript/JS class methods + Go receiver method
    (tmp_path / "service.ts").write_text(
        "export class ApiClient {\n"
        "    async fetchUser(id: string): Promise<string> {\n"
        "        const s = \"}\";\n"
        "        if (id) { return id; }\n"
        "        return s;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "server.go").write_text(
        "package main\n"
        "type Server struct { port int }\n"
        "func (s *Server) Handle(path string) {\n"
        "    println(path)\n"
        "}\n",
        encoding="utf-8",
    )
    ts_out = read_file(ws, "service.ts", outline=True)
    assert "ApiClient" in ts_out and "ApiClient.fetchUser" in ts_out
    assert "ApiClient.if" not in ts_out
    ts_anchor = read_file(ws, "service.ts", anchor_line=5)
    assert "fetchUser" in ts_anchor and "return s;" in ts_anchor

    go_out = read_file(ws, "server.go", outline=True)
    assert "Server" in go_out and "Server.Handle" in go_out

    # 7. RepoMap renders definitions across ALL languages
    rmap = RepoMap(tmp_path).render(budget=6000, time_limit=10)
    for expected in (
        "OrderService.java",
        "OrderService.findOrders",
        "UserService.cs",
        "UserService.GetUserAsync",
        "engine.cpp",
        "parse_header",
        "math_api.h",
        "add_numbers",
        "lib.rs",
        "Config.fetch",
        "InvoiceController.php",
        "InvoiceController.formatAmount",
        "calculate_vat",
        "service.ts",
        "ApiClient.fetchUser",
        "server.go",
        "Server.Handle",
    ):
        assert expected in rmap, f"Missing {expected!r} in RepoMap:\n{rmap}"


def test_audit_fixes_security_math_youtube_skills_agenda_ui_and_annotations(tmp_path, monkeypatch):
    import subprocess
    from akatikito import network_tools as net
    from akatikito import notify
    from akatikito.agent import Akatikito
    from akatikito.config import Config
    from akatikito.execution import mutation_paths
    from akatikito.model import ScriptedModel, tool_call
    from akatikito.tool_plugins import _safe_eval_math
    from akatikito.tools import read_file
    from akatikito.workspace import Workspace

    ws = Workspace(tmp_path)

    # 1. Assistant mode blocks convert_data_file to code/dotfiles & blocks dotfiles in create_pdf_document
    (tmp_path / "in.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    ag = Akatikito(str(tmp_path), Config(auto_mode=False, checkpoints=False), ScriptedModel([]))
    ag.set_mode("assistant")
    r_bad_conv, _ = ag._execute_call(
        {"name": "convert_data_file", "arguments": {"source_path": "in.csv", "target_format": "json", "output_path": ".hidden/pwn.sh"}},
        ag.allowed_tools(),
    )
    assert r_bad_conv.startswith("ΑΠΟΡΡΙΨΗ") and not (tmp_path / ".hidden" / "pwn.sh").exists()
    r_bad_pdf, _ = ag._execute_call(
        {"name": "create_pdf_document", "arguments": {"path": ".hidden/sec.pdf", "title": "X"}},
        ag.allowed_tools(),
    )
    assert r_bad_pdf.startswith("ΑΠΟΡΡΙΨΗ") and not (tmp_path / ".hidden" / "sec.pdf").exists()

    # PDF creation in assistant mode marks task complete & mutation_paths tracks convert_data_file output_path
    monkeypatch.setattr(net, "find_chromium_binary", lambda: None)
    model_pdf = ScriptedModel([
        {"tool_calls": [tool_call("create_pdf_document", path="summary.pdf", title="Τίτλος")]},
        {"content": "Έτοιμο το PDF."},
    ])
    ag_pdf = Akatikito(str(tmp_path), Config(auto_mode=False, checkpoints=False), model_pdf)
    ag_pdf.set_mode("assistant")
    ag_pdf.chat("Φτιάξε PDF")
    assert ag_pdf.task.status == "complete"
    assert mutation_paths("convert_data_file", {"output_path": "out.md"}, "ΕΓΙΝΕ: ok") == ("out.md",)
    assert mutation_paths("convert_data_file", {"output_path": ""}, "table") == ()

    # 2. Math evaluator fixes: sum() works with varargs & list, pow() enforces exponent limit, keywords rejected
    assert _safe_eval_math("sum(1, 2, 3)") == 6.0
    assert _safe_eval_math("sum([10, 20, 30])") == 60.0
    with pytest.raises(ValueError, match="εκθέτης"):
        _safe_eval_math("pow(2, 5000)")
    with pytest.raises(ValueError):
        _safe_eval_math("round(1.234, ndigits=2)")

    # 3. YouTube transcript raw_decode survives '};' inside JSON strings
    yt_tricky = (
        "<script>var ytInitialPlayerResponse = "
        + json.dumps({
            "videoDetails": {"title": "Tricky }; Title", "author": "Tester", "shortDescription": "Desc }; end"},
            "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": []}},
        })
        + ";</script>"
    )
    monkeypatch.setattr(net, "_get", lambda url, **kw: net.Response(url, yt_tricky.encode("utf-8"), {"content-type": "text/html"}))
    yt_res = net.youtube_transcript(ws, "dQw4w9WgXcQ")
    assert "Tricky }; Title" in yt_res and "Tester" in yt_res

    # 4. browser_fetch does NOT falsely report saved screenshot when existing file wasn't updated
    (tmp_path / "existing.png").write_bytes(b"\x89PNG\r\n\x1a\nOLD")
    monkeypatch.setattr(net, "_public_url", lambda url: ("https", "example.com", 443, "/", ["93.184.216.34"]))
    monkeypatch.setattr(net, "find_chromium_binary", lambda: "C:/fake/chrome.exe")
    monkeypatch.setattr(net, "_run_chromium_dom", lambda b, u, wait_ms=2500, screenshot_target=None: "<html><body>OK</body></html>")
    bf_out = net.browser_fetch(ws, "https://example.com/", screenshot_path="existing.png")
    assert "Στιγμιότυπο σελίδας αποθηκεύτηκε" not in bf_out

    # 5. Skills: rejecting proposal preserves needs_healing; approving clears it; direct /skill warns missing tools
    sk = Skills(tmp_path)
    sk.save_owner_skill(
        name="excel-heal-test",
        description="Δοκιμή self-healing",
        steps=["Βήμα ένα", "Βήμα δύο", "Βήμα τρία"],
        required_tools=["create_excel_sheet"],
    )
    sk.record_telemetry("excel-heal-test", had_error=True, recovered=False, last_error="boom")
    assert next(r for r in sk.catalog() if r["name"] == "excel-heal-test")["telemetry"]["needs_healing"] is True
    sk.patch("excel-heal-test", add_notes=["Παγίδα"])
    prop = sk.view()["proposals"][0]
    sk.answer(prop["id"], False, expected=prop["signature"])
    assert next(r for r in sk.catalog() if r["name"] == "excel-heal-test")["telemetry"]["needs_healing"] is True
    sk.patch("excel-heal-test", add_notes=["Παγίδα 2"])
    prop2 = sk.view()["proposals"][0]
    sk.answer(prop2["id"], True, expected=prop2["signature"])
    assert next(r for r in sk.catalog() if r["name"] == "excel-heal-test")["telemetry"]["needs_healing"] is False

    m_ask = ScriptedModel([{"content": "ΟΚ"}])
    ag_ask = Akatikito(str(tmp_path), Config(auto_mode=False, checkpoints=False), m_ask)
    ag_ask.set_mode("ask")
    ag_ask.chat("/excel-heal-test τρέξε")
    first_prompt = "\n".join(str(m.get("content", "")) for m in m_ask.seen[0] if m.get("role") == "user")
    assert "Μη διαθέσιμα στην τρέχουσα λειτουργία: create_excel_sheet" in first_prompt

    # 6. Deleting / completing a queued auto_run agenda task removes it from .agent/auto_queue.json
    monkeypatch.setattr(notify, "notify", lambda *a, **kw: None)
    st = AppState()
    st.settings.project = str(tmp_path)
    st.agent = type("FakeAgent", (), {"ws": ws})()
    t_del = st.agenda_add({"title": "Προς διαγραφή", "date": "2020-01-01", "time": "08:00", "auto_run": True})["task"]["id"]
    t_done = st.agenda_add({"title": "Προς ολοκλήρωση", "date": "2020-01-01", "time": "08:05", "auto_run": True})["task"]["id"]
    st.busy = True
    st.reminder_tick()
    assert len(st._load_auto_queue()) == 2
    st.busy = False
    st.agenda_delete(t_del)
    assert [x["id"] for x in st._load_auto_queue()] == [t_done]
    st.agenda_toggle(t_done, True)
    assert st._load_auto_queue() == []

    # 7. Inline Java/C# annotations on same line as method
    (tmp_path / "Annotated.java").write_text(
        "public class Annotated {\n"
        "    @Override public void run() {\n"
        "        System.out.println(\"hi\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Ctrl.cs").write_text(
        "public class Ctrl {\n"
        "    [HttpGet(\"/api\")] public string Index() {\n"
        "        return \"ok\";\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    assert "Annotated.run" in read_file(ws, "Annotated.java", outline=True)
    assert "Ctrl.Index" in read_file(ws, "Ctrl.cs", outline=True)

    # 8. UI JS checks in Node: trailing-EOF markdown table, safe DOM button (no inline onclick), agenda signature dedup
    ui_dir = Path(__file__).resolve().parents[1] / "akatikito" / "ui"
    js_body = "\n".join((ui_dir / f).read_text(encoding="utf-8") for f in ("chat.js", "files.js", "attachments.js", "learning.js", "settings.js"))
    js_body = js_body[: js_body.rindex("init().catch")]
    node_check = (
        "const assert = require('node:assert/strict');\n"
        "const vm = require('node:vm');\n"
        "class El {\n"
        "  constructor(t='DIV'){ this.tagName=t.toUpperCase(); this.children=[]; this.style={}; this.dataset={}; this.className=''; this.textContent=''; this.classList={add(){},toggle(){}}; this.replaceCount=0; }\n"
        "  appendChild(c){ this.children.push(c); return c; }\n"
        "  append(...cs){ cs.forEach(c=>this.appendChild(c)); }\n"
        "  replaceChildren(...cs){ this.replaceCount++; this.children=[]; this.append(...cs); }\n"
        "  querySelectorAll(){ return []; }\n"
        "}\n"
        "const byId = new Map();\n"
        "const document = { getElementById(id){ if(!byId.has(id)) byId.set(id, new El()); return byId.get(id); }, createElement(t){ return new El(t); }, addEventListener(){}, querySelectorAll(){ return []; } };\n"
        "let openedPath = null;\n"
        "const ctx = { document, window: {}, setTimeout(){}, clearTimeout(){}, setInterval(){}, confirm: ()=>true, console };\n"
        "vm.createContext(ctx);\n"
        "vm.runInContext(" + json.dumps(js_body) + ", ctx);\n"
        "ctx.openProjectFile = (p) => { openedPath = p; };\n"
        "const htmlTbl = ctx.fmt('| A | B |\\n| --- | --- |\\n| 1 | 2 |');\n"
        "assert(htmlTbl.includes('<table') && htmlTbl.includes('<td>2</td>'), 'EOF table failed: ' + htmlTbl);\n"
        "ctx.fmt('```js\\nconst s = \"hi\";\\n```');\n"
        "const snips = vm.runInContext('codeSnippets', ctx);\n"
        "assert.equal(snips[snips.length - 1], 'const s = \"hi\";');\n"
        "const tricky = 'dir\\\\a\"b\\'c.py';\n"
        "ctx.addStep('tool_result', { name: 'edit_file', path: tricky, result: 'ΕΓΙΝΕ' });\n"
        "const chatEl = byId.get('chat');\n"
        "const btn = chatEl.children[0].children[0].children[0].children[0];\n"
        "assert.equal(btn.tagName, 'BUTTON');\n"
        "btn.onclick();\n"
        "assert.equal(openedPath, tricky);\n"
        "const agEl = document.getElementById('agenda-list');\n"
        "const tasks = [{ id: '1', date: '2026-12-01', time: '09:00', title: 'Test', done: false }];\n"
        "ctx.renderAgendaList(tasks);\n"
        "const firstCount = agEl.replaceCount;\n"
        "ctx.renderAgendaList(tasks);\n"
        "assert.equal(agEl.replaceCount, firstCount);\n"
        "console.log('AUDIT_UI_OK');\n"
    )
    res_node = subprocess.run(["node", "-"], input=node_check, capture_output=True, text=True, encoding="utf-8")
    assert res_node.returncode == 0, res_node.stderr
    assert "AUDIT_UI_OK" in res_node.stdout


def test_mcp_upgrades_secrets_json_import_stderr_autoconnect_and_ui(tmp_path):
    import os
    import subprocess
    from akatikito.mcp_client import MCPError, MCPHub, StdioClient

    mcp_path = tmp_path / "mcp.json"
    state = AppState()
    state.mcp = MCPHub(mcp_path)

    # 1. Encrypted env & HTTP headers: never stored in plaintext, never leaked via mcp_status(), preserved on blank edit
    saved = state.mcp_save({
        "name": "notion",
        "config": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", "pass"],
            "env": {"NOTION_TOKEN": "ntn_super_secret_12345"},
            "readOnlyTools": ["search"],
            "autoConnect": True,
        },
    })
    assert saved.get("ok") is True
    raw_file = mcp_path.read_text(encoding="utf-8")
    assert "ntn_super_secret_12345" not in raw_file
    assert "v1:" in raw_file
    pub_notion = saved["mcp"]["configs"]["notion"]
    assert pub_notion["envKeys"] == ["NOTION_TOKEN"]
    assert "ntn_super_secret_12345" not in json.dumps(saved["mcp"])
    assert pub_notion["autoConnect"] is True

    # Blank value preserves existing masked token
    saved2 = state.mcp_save({
        "name": "notion",
        "config": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", "pass"],
            "env": {"NOTION_TOKEN": ""},
            "readOnlyTools": ["search"],
            "autoConnect": True,
        },
    })
    assert saved2.get("ok") is True
    assert state.mcp.configured()["notion"]["env"]["NOTION_TOKEN"].startswith(("dpapi:v1:", "v1:"))

    # HTTP headers masking
    http_saved = state.mcp_save({
        "name": "cloud-mcp",
        "config": {
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer secret_hdr_token_999"},
        },
    })
    assert http_saved.get("ok") is True
    assert "secret_hdr_token_999" not in mcp_path.read_text(encoding="utf-8")
    assert http_saved["mcp"]["configs"]["cloud-mcp"]["headerKeys"] == ["Authorization"]

    # 2. 1-click Claude/Cursor JSON import (with Windows npx -> npx.cmd normalization)
    imp = state.mcp_import({
        "json": json.dumps({
            "mcpServers": {
                "my github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret_abc123"},
                }
            }
        })
    })
    assert imp.get("ok") is True
    assert imp["imported"] == ["my-github"]
    gh_cfg = imp["mcp"]["configs"]["my-github"]
    assert gh_cfg["command"] == ("npx.cmd" if os.name == "nt" else "npx")
    assert gh_cfg["envKeys"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert "ghp_secret_abc123" not in mcp_path.read_text(encoding="utf-8")

    # 3. Missing command hint + stderr diagnostic capture with secret redaction
    with pytest.raises(MCPError, match="Δεν βρέθηκε"):
        StdioClient({"command": "definitely_missing_mcp_binary_xyz_999", "args": []})

    crash_script = (
        "import sys; "
        "sys.stderr.write('CRASH_DETAIL token=SUPER_SECRET_XYZ99\\n'); "
        "sys.stderr.flush(); "
        "sys.exit(1)"
    )
    with pytest.raises(MCPError) as exc_info:
        StdioClient({"command": sys.executable, "args": ["-c", crash_script], "env": {"API_KEY": "SUPER_SECRET_XYZ99"}})
    err_msg = str(exc_info.value)
    assert "CRASH_DETAIL" in err_msg
    assert "SUPER_SECRET_XYZ99" not in err_msg
    assert "***" in err_msg

    # 4. Real local stdio MCP server: connect records approvedHash + server_tools + hash-bound auto_connect()
    fake_mcp = tmp_path / "mini_mcp.py"
    fake_mcp.write_text(
        "import json, os, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    mid = req.get('id')\n"
        "    m = req.get('method')\n"
        "    if m == 'server/discover':\n"
        "        res = {'supportedVersions': ['2026-07-28']}\n"
        "    elif m == 'tools/list':\n"
        "        res = {'tools': [{'name': 'ping_tool', 'description': 'Ping check', 'inputSchema': {'type': 'object', 'properties': {}}, 'annotations': {'readOnlyHint': True}}]}\n"
        "    elif m == 'tools/call':\n"
        "        res = {'content': [{'type': 'text', 'text': 'env=' + os.environ.get('DEMO_SECRET', '')}]}\n"
        "    else:\n"
        "        continue\n"
        "    if mid is not None:\n"
        "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': mid, 'result': res}) + '\\n')\n"
        "        sys.stdout.flush()\n",
        encoding="utf-8",
    )
    hub1 = MCPHub(tmp_path / "auto_mcp.json")
    hub1.save_server("demo", {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(fake_mcp)],
        "env": {"DEMO_SECRET": "unmasked_value_ok"},
        "readOnlyTools": ["ping_tool"],
        "autoConnect": True,
    })
    # Before first approval, auto_connect MUST NOT connect!
    assert hub1.auto_connect() == []
    assert "demo" in hub1.auto_warnings

    # Approve and connect once
    hub1.connect("demo", lambda _d: True)
    try:
        st_tools = hub1.server_tools()
        assert "demo" in st_tools and st_tools["demo"][0]["name"] == "ping_tool"
        assert st_tools["demo"][0]["readonly"] is True
        # Verify unmasked env reached the child process
        call_res = hub1.servers["demo"].request("tools/call", {"name": "ping_tool", "arguments": {}})
        assert "env=unmasked_value_ok" in json.dumps(call_res)
    finally:
        hub1.close()

    # Restart with fresh MCPHub -> exact approvedHash allows auto_connect()!
    hub2 = MCPHub(tmp_path / "auto_mcp.json")
    try:
        assert hub2.auto_connect() == ["demo"]
        assert "demo" in hub2.servers
    finally:
        hub2.close()

    # External tampering of mcp.json invalidates approvedHash -> auto_connect() MUST refuse!
    tampered = json.loads((tmp_path / "auto_mcp.json").read_text(encoding="utf-8"))
    tampered["servers"]["demo"]["args"].append("--tampered")
    (tmp_path / "auto_mcp.json").write_text(json.dumps(tampered), encoding="utf-8")
    hub3 = MCPHub(tmp_path / "auto_mcp.json")
    assert hub3.auto_connect() == []
    assert "demo" in hub3.auto_warnings

    # 5. UI JS check: applyMcpPreset('notion'), editMcpServer, parseKeyValLines, saveMcpServer payload
    ui_dir = Path(__file__).resolve().parents[1] / "akatikito" / "ui"
    js_body = "\n".join((ui_dir / f).read_text(encoding="utf-8") for f in ("chat.js", "files.js", "attachments.js", "learning.js", "settings.js"))
    js_body = js_body[: js_body.rindex("init().catch")]
    node_mcp = (
        "const assert = require('node:assert/strict');\n"
        "const vm = require('node:vm');\n"
        "class El {\n"
        "  constructor(t='DIV'){ this.tagName=t.toUpperCase(); this.children=[]; this.style={display:'none'}; this.value=''; this.checked=false; this.dataset={}; this.className=''; this.textContent=''; this.classList={add(){},toggle(){}}; }\n"
        "  appendChild(c){ this.children.push(c); return c; }\n"
        "  append(...cs){ cs.forEach(c=>this.appendChild(c)); }\n"
        "  replaceChildren(...cs){ this.children=[]; this.append(...cs); }\n"
        "  querySelectorAll(){ return []; }\n"
        "}\n"
        "const byId = new Map();\n"
        "const document = { getElementById(id){ if(!byId.has(id)) byId.set(id, new El()); return byId.get(id); }, createElement(t){ return new El(t); }, addEventListener(){}, querySelectorAll(){ return []; } };\n"
        "let lastSaved = null;\n"
        "const ctx = { document, window: {}, setTimeout(){}, clearTimeout(){}, setInterval(){}, confirm: ()=>true, console };\n"
        "vm.createContext(ctx);\n"
        "vm.runInContext(" + json.dumps(js_body) + ", ctx);\n"
        "ctx.api = async (url, body) => { lastSaved = {url, body}; return { ok: true }; };\n"
        "ctx.poll = async () => {};\n"
        "ctx.applyMcpPreset('notion');\n"
        "assert.equal(document.getElementById('mcp-name').value, 'notion');\n"
        "assert(document.getElementById('mcp-env').value.includes('NOTION_TOKEN='));\n"
        "ctx.editMcpServer('my-srv', { transport: 'stdio', command: 'npx.cmd', args: ['-y', 'pkg', 'D:/my dir'], readOnlyTools: ['r1'], envKeys: ['API_KEY'], autoConnect: true });\n"
        "assert.equal(document.getElementById('mcp-name').value, 'my-srv');\n"
        "assert.equal(document.getElementById('mcp-args').value, '-y pkg \"D:/my dir\"');\n"
        "assert.equal(document.getElementById('mcp-env').value, 'API_KEY=');\n"
        "assert.equal(document.getElementById('mcp-autoconnect').checked, true);\n"
        "(async () => {\n"
        "  await ctx.saveMcpServer();\n"
        "  assert.equal(lastSaved.url, '/api/mcp/save');\n"
        "  assert.equal(JSON.stringify(lastSaved.body.config.args), JSON.stringify(['-y', 'pkg', 'D:/my dir']));\n"
        "  assert.equal(JSON.stringify(lastSaved.body.config.env), JSON.stringify({ API_KEY: '' }));\n"
        "  assert.equal(lastSaved.body.config.autoConnect, true);\n"
        "  console.log('MCP_UI_OK');\n"
        "})().catch(e => { console.error(e); process.exit(1); });\n"
    )
    r_mcp = subprocess.run(["node", "-"], input=node_mcp, capture_output=True, text=True, encoding="utf-8")
    assert r_mcp.returncode == 0, r_mcp.stderr
    assert "MCP_UI_OK" in r_mcp.stdout








