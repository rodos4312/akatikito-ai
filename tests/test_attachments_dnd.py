"""Tests for Drag & Drop attachments, expanded safe text/code extensions, and UI drop overlay."""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from akatikito.attachments import Attachments
from akatikito.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def test_attachments_accepts_modern_code_and_config_extensions(tmp_path):
    ws = Workspace(str(tmp_path))
    store = Attachments(ws)

    for fname, body in (
        ("config.yaml", b"mode: auto\n"),
        ("pubspec.yml", b"name: demo\n"),
        ("pyproject.toml", b"[project]\nname='x'\n"),
        ("schema.sql", b"SELECT 1;\n"),
        ("main.dart", b"void main() {}\n"),
        ("app.tsx", b"export const App = () => null;\n"),
        ("deploy.ps1", b"Write-Output 'ok'\n"),
    ):
        b64 = base64.b64encode(body).decode("ascii")
        meta = store.upload(fname, b64)
        assert meta["name"] == fname
        assert meta["size"] == len(body)
        assert meta["image"] is False
        # Full round-trip through Attachments.content() (including extensions with digits like .ps1)
        blocks, names = store.content([meta])
        assert names == [fname]
        assert len(blocks) == 1 and body.decode("utf-8").strip() in blocks[0]["text"]

    # Still strictly rejects executable / unsupported extensions and empty files
    with pytest.raises(ValueError):
        store.upload("malware.exe", base64.b64encode(b"MZ").decode("ascii"))
    with pytest.raises(ValueError):
        store.upload("empty.yaml", "")


def test_drag_and_drop_overlay_and_error_handling_in_node():
    js_code = (ROOT / "akatikito" / "ui" / "attachments.js").read_text(encoding="utf-8")
    harness = (
        "const assert = require('node:assert/strict');\n"
        "const vm = require('node:vm');\n"
        "const listeners = {};\n"
        "const notices = [];\n"
        "const uploads = [];\n"
        "const overlay = { hidden: true };\n"
        "const panel = { children: [], replaceChildren(){ this.children = []; }, appendChild(c){ this.children.push(c); } };\n"
        "const promptEl = { focused: false, focus(){ this.focused = true; } };\n"
        "const document = {\n"
        "  addEventListener(ev, fn){ (listeners[ev] ||= []).push(fn); },\n"
        "  getElementById(id){ return id === 'drop-overlay' ? overlay : id === 'attachment-panel' ? panel : id === 'prompt' ? promptEl : null; },\n"
        "  createElement(tag){ return { tag, textContent: '', title: '', className: '' }; },\n"
        "};\n"
        "class FakeFileReader {\n"
        "  readAsDataURL(f){\n"
        "    if (f._fail) { setImmediate(() => this.onerror(new Error('dir'))); return; }\n"
        "    this.result = 'data:text/plain;base64,' + Buffer.from(f._body || 'hi').toString('base64');\n"
        "    setImmediate(() => this.onload());\n"
        "  }\n"
        "}\n"
        "const ctx = {\n"
        "  document, FileReader: FakeFileReader, connected: false, busy: false,\n"
        "  currentProject: 'P', currentSession: 'S',\n"
        "  showNotice(kind, msg){ notices.push([kind, msg]); },\n"
        "  async api(url, body){ uploads.push({url, body}); return { id: 'abc', name: body.name, image: false, size: 12 }; },\n"
        "};\n"
        "vm.createContext(ctx);\n"
        f"vm.runInContext({js_code!r}, ctx);\n"
        "(async () => {\n"
        "  // 1. Dragenter with Files shows overlay; dragleave hides it\n"
        "  listeners.dragenter[0]({ preventDefault(){}, dataTransfer: { types: ['Files'] } });\n"
        "  assert.equal(overlay.hidden, false);\n"
        "  listeners.dragleave[0]({});\n"
        "  assert.equal(overlay.hidden, true);\n"
        "  // 2. Dropping while disconnected shows clear error instead of silent drop\n"
        "  await ctx.attachFiles([{ name: 'a.py', size: 10, _body: 'x=1' }]);\n"
        "  assert.equal(notices[0][0], 'err');\n"
        "  // 3. Connected: empty 0-byte file rejected cleanly, folder FileReader error handled, valid file uploads & focuses prompt\n"
        "  ctx.connected = true;\n"
        "  await ctx.attachFiles([\n"
        "    { name: 'empty.txt', size: 0 },\n"
        "    { name: 'folder', size: 100, _fail: true },\n"
        "    { name: 'notes.yaml', size: 12, _body: 'ok: true' },\n"
        "  ]);\n"
        "  assert.equal(uploads.length, 1);\n"
        "  assert.equal(uploads[0].body.name, 'notes.yaml');\n"
        "  assert.equal(promptEl.focused, true);\n"
        "  assert.equal(panel.children.length, 1);\n"
        "  // 4. hideDropOverlay hides stuck drop overlay\n"
        "  listeners.dragenter[0]({ preventDefault(){}, dataTransfer: { types: ['Files'] } });\n"
        "  assert.equal(overlay.hidden, false);\n"
        "  assert.equal(ctx.hideDropOverlay(), true);\n"
        "  assert.equal(overlay.hidden, true);\n"
        "  // 5. Ctrl+V image paste uses MIME extension when unnamed, and never hijacks text pastes (Excel/Word) or <input>\n"
        "  let prevented = false;\n"
        "  listeners.paste[0]({\n"
        "    target: { tagName: 'TEXTAREA' },\n"
        "    preventDefault(){ prevented = true; },\n"
        "    clipboardData: {\n"
        "      getData(t){ return t === 'text/plain' ? 'A1\\tB1' : ''; },\n"
        "      items: [{ kind: 'file', type: 'image/png', getAsFile(){ return { name: '', type: 'image/png', size: 10, _body: 'png' }; } }],\n"
        "    },\n"
        "  });\n"
        "  assert.equal(prevented, false);\n"
        "  listeners.paste[0]({\n"
        "    target: { tagName: 'INPUT' },\n"
        "    preventDefault(){ prevented = true; },\n"
        "    clipboardData: {\n"
        "      getData(){ return ''; },\n"
        "      items: [{ kind: 'file', type: 'image/jpeg', getAsFile(){ return { name: '', type: 'image/jpeg', size: 10, _body: 'jpg' }; } }],\n"
        "    },\n"
        "  });\n"
        "  assert.equal(prevented, false);\n"
        "  listeners.paste[0]({\n"
        "    target: { tagName: 'TEXTAREA' },\n"
        "    preventDefault(){ prevented = true; },\n"
        "    clipboardData: {\n"
        "      getData(){ return ''; },\n"
        "      items: [{ kind: 'file', type: 'image/jpeg', getAsFile(){ return { name: '', type: 'image/jpeg', size: 10, _body: 'jpg' }; } }],\n"
        "    },\n"
        "  });\n"
        "  assert.equal(prevented, true);\n"
        "  await new Promise(r => setTimeout(r, 20));\n"
        "  assert.equal(uploads.length, 2);\n"
        "  assert.equal(uploads[1].body.name, 'pasted.jpg');\n"
        "})().catch(e => { console.error(e); process.exit(1); });\n"
    )
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, res.stderr or res.stdout


def test_paste_text_falls_back_to_clipboard_screenshot_for_prompt(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PIL import Image, ImageGrab
    import pyperclip
    from akatikito.app_api import AppState

    state = AppState()
    state.agent = SimpleNamespace(ws=Workspace(str(tmp_path)))
    state.session_id = "s1"

    # 1. Text on clipboard: returns text immediately, does not attach image
    monkeypatch.setattr(pyperclip, "paste", lambda: "hello world")
    res_text = state.paste_text({"allow_image": True, "project": str(tmp_path), "session_id": "s1"})
    assert res_text == {"text": "hello world"}

    # 2. No text + PIL image on clipboard: uploads PNG attachment cleanly
    monkeypatch.setattr(pyperclip, "paste", lambda: "")
    sample_img = Image.new("RGB", (4, 4), color=(10, 120, 220))
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: sample_img)
    res_img = state.paste_text({"allow_image": True, "project": str(tmp_path), "session_id": "s1"})
    assert res_img["text"] == ""
    assert res_img["attachment"]["image"] is True
    assert res_img["attachment"]["name"].endswith(".png")
    assert res_img["attachment"]["size"] > 0

    # 3. Without allow_image (e.g. API key input): never attaches image
    res_input = state.paste_text({"allow_image": False})
    assert res_input == {"text": ""}
