"""Verification tests for Pro GitHub Release pillars: DPAPI vault, diagnostics bundle, eval harness, portable packager."""
from __future__ import annotations

import base64
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_dpapi_secret_vault_roundtrip_random_salt_legacy_and_tamper():
    from akatikito.secret_store import DPAPI_PREFIX, is_dpapi_available, redact_secrets, unprotect_text
    from akatikito.settings import _MASK, _PREFIX, mask_secret, unmask_secret

    secret = "sk-live-super-secret-value-998877"
    enc1 = mask_secret(secret)
    enc2 = mask_secret(secret)

    assert enc1 != secret
    assert unmask_secret(enc1) == secret
    assert unmask_secret(enc2) == secret
    if os.name == "nt" and is_dpapi_available():
        assert enc1.startswith(DPAPI_PREFIX)
        assert enc2.startswith(DPAPI_PREFIX)
        # DPAPI generates random salt per encryption -> distinct ciphertexts
        assert enc1 != enc2
        # Corrupted DPAPI payload fails cleanly to empty string
        assert unprotect_text(DPAPI_PREFIX + "AAAAinvalid!!!") == ""

    # Legacy XOR 'v1:' values still decrypt cleanly for seamless migration
    raw = secret.encode("utf-8")
    mixed = bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw))
    legacy = _PREFIX + base64.urlsafe_b64encode(mixed).decode("ascii")
    assert unmask_secret(legacy) == secret

    # Secret redaction catches explicit & pattern-matched keys
    dirty = f"Error with key {secret} and Bearer abcdefghijklmnop123456 and ghp_1234567890abcdef123456"
    clean = redact_secrets(dirty, extra_secrets=(secret,))
    assert secret not in clean
    assert "abcdefghijklmnop123456" not in clean
    assert "ghp_1234567890abcdef123456" not in clean


def test_diagnostics_health_and_zip_bundle_never_leaks_secrets(tmp_path):
    from akatikito.app_api import AppState
    from akatikito.diagnostics import export_diagnostics_zip, health_report
    from akatikito.settings import mask_secret

    st = AppState()
    st.settings.project = str(tmp_path)
    secret_key = "sk-never-leak-this-key-123456789"
    st.settings.keys["OpenAI"] = mask_secret(secret_key)
    st.error = f"Upstream failed with Bearer {secret_key}"

    rep = health_report(st)
    assert rep["app_version"]
    assert "dpapi_vault" in rep["checks"]
    assert "tree_sitter" in rep["checks"]
    assert secret_key not in json.dumps(rep, ensure_ascii=False)

    res = export_diagnostics_zip(st, allow_dialog=False)
    assert res["ok"] is True
    zpath = Path(res["full_path"])
    assert zpath.is_file()
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
        assert "diagnostics.json" in names and "SUMMARY.md" in names
        for name in names:
            body = zf.read(name).decode("utf-8")
            assert secret_key not in body


def test_offline_eval_harness_passes_all_10_scenarios():
    from akatikito.eval_harness import run_dry_evals

    report = run_dry_evals()
    assert report["total"] == 10
    failures = [r for r in report["results"] if not r["passed"]]
    assert not failures, f"Eval failures: {failures}"
    assert report["passed"] == 10


def test_portable_release_packager_clean_and_blocks_secrets(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_portable", ROOT / "packaging" / "build_portable.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    build_portable_release = mod.build_portable_release

    # 1. Clean synthetic repo builds valid ZIP + MANIFEST + SHA256SUMS
    repo = tmp_path / "repo"
    (repo / "akatikito").mkdir(parents=True)
    (repo / "akatikito" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    # Private data/ and .venv/ must be silently excluded
    (repo / "data").mkdir()
    (repo / "data" / "settings.json").write_text('{"keys":{"OpenAI":"SECRET"}}', encoding="utf-8")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "dummy.txt").write_text("ignore", encoding="utf-8")

    out = build_portable_release(root=repo, out_dir=tmp_path / "dist", version="2.0.0-test")
    zpath = Path(out["zip_path"])
    assert zpath.is_file()
    assert Path(out["sums_path"]).is_file()
    assert out["sha256"] in Path(out["sums_path"]).read_text(encoding="utf-8")

    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
        assert "Akatikito/app.py" in names
        assert "Akatikito/MANIFEST.json" in names
        assert not any("settings.json" in n or ".venv" in n or "/data/" in n for n in names)

    # 2. Stray settings.json outside data/ causes immediate hard failure
    (repo / "settings.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="sensitive file"):
        build_portable_release(root=repo, out_dir=tmp_path / "dist2", version="2.0.0-test")


def test_i18n_six_languages_parity_api_agent_and_ui_keys(tmp_path):
    import re
    from akatikito.agent import Akatikito
    from akatikito.app_api import AppState
    from akatikito.config import Config
    from akatikito.i18n import SUPPORTED_LANGUAGES, TRANSLATIONS, agent_language_instruction, translations
    from akatikito.settings import Settings

    # 1. All 6 languages have complete key parity
    expected_langs = {"el", "en", "ru", "de", "es", "fr"}
    assert set(SUPPORTED_LANGUAGES.keys()) == expected_langs
    base_keys = set(TRANSLATIONS["el"].keys())
    for lang in expected_langs:
        assert set(TRANSLATIONS[lang].keys()) == base_keys, f"Missing keys in {lang}"
        assert all(v.strip() for v in TRANSLATIONS[lang].values()), f"Empty value in {lang}"

    # 2. Every data-i18n / data-i18n-placeholder attribute in the UI (html + ui/*.js) exists in the catalog
    html = "\n".join(p.read_text(encoding="utf-8") for p in [ROOT / "akatikito" / "ui.html", *sorted((ROOT / "akatikito" / "ui").glob("*.js"))])
    used_keys = set(re.findall(r'data-i18n(?:-placeholder)?="([a-z0-9_]+)"', html))
    assert used_keys and used_keys <= base_keys, f"Unknown i18n keys in ui.html: {used_keys - base_keys}"

    # 3. Settings persistence + AppState preferences & i18n endpoint
    sfile = tmp_path / "settings.json"
    st = Settings(ui_language="ru")
    st.save(sfile)
    loaded = Settings.load(sfile)
    assert loaded.ui_language == "ru"

    app = AppState()
    app.settings.project = str(tmp_path)
    err = app.set_preferences({"ui_language": "xx"})
    assert "error" in err
    app.settings.ui_language = "en"
    bundle = app.i18n_bundle("ru")
    assert bundle["lang"] == "ru"
    assert bundle["strings"]["tab_sess"] == "💬 Чаты"
    assert bundle["strings"]["btn_send"] == "Отправить ➤"

    # 4. Agent system prompt adapts to non-Greek UI language
    assert agent_language_instruction("el") == ""
    assert "Russian" in agent_language_instruction("ru") or "Русский" in agent_language_instruction("ru")
    agent_en = Akatikito(root=tmp_path, config=Config(api_key="test-key", ui_language="en"))
    assert "English" in agent_en.system_prompt()
    agent_ru = Akatikito(root=tmp_path, config=Config(api_key="test-key", ui_language="ru"))
    assert "Русский" in agent_ru.system_prompt()
    assert translations("fr")["btn_send"] == "Envoyer ➤"


def test_windowless_app_icon_and_github_pages_kit(tmp_path, monkeypatch):
    import importlib.util
    import re
    import subprocess

    # 1. Windowless pythonw stream handling in app.py
    spec = importlib.util.spec_from_file_location("akatikito_app_entry", ROOT / "app.py")
    app_mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(app_mod)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert app_mod._prepare_streams() is True
    assert sys.stdout is not None and sys.stderr is not None

    # 2. Multi-resolution .ico generation via create_shortcuts.py
    s_spec = importlib.util.spec_from_file_location("create_shortcuts", ROOT / "scripts" / "create_shortcuts.py")
    sc_mod = importlib.util.module_from_spec(s_spec)
    assert s_spec and s_spec.loader
    s_spec.loader.exec_module(sc_mod)
    ico = sc_mod.generate_icon(tmp_path / "akatikito.ico")
    assert ico.is_file() and ico.stat().st_size > 1000
    assert ico.read_bytes()[:4] == b"\x00\x00\x01\x00"  # valid ICO header

    # 3. GitHub Pages site + README + LICENSE + Install/Uninstall + silent AkatikitoApp.cmd
    for name in ("README.md", "LICENSE", "Install.cmd", "Uninstall.cmd", "AkatikitoApp.cmd", "docs/index.html"):
        assert (ROOT / name).is_file(), f"Missing {name}"
    cmd_txt = (ROOT / "AkatikitoApp.cmd").read_text(encoding="utf-8")
    assert "pythonw.exe" in cmd_txt
    docs_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    docs_js = re.findall(r"<script>(.*?)</script>", docs_html, re.S)[-1]
    assert subprocess.run(["node", "--check"], input=docs_js, text=True, encoding="utf-8", capture_output=True).returncode == 0


