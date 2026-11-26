"""W6 real-entry-point wiring test for D5 Plugin SDK via CLI and channel_plugins.Plugins loader."""
from __future__ import annotations

import pytest

from akatikito.channel_plugins import Plugins
from akatikito.cli import run_cli
from akatikito.plugin_sdk import generate_signing_key, verify_plugin_signature
from akatikito.settings import Settings


def test_w6_cli_scaffold_sign_and_plugins_loader_dev_gate(tmp_path):
    spath = tmp_path / "settings.json"
    dev_dir = tmp_path / "dev-plugins"
    dev_dir.mkdir()

    # Closed by default -> CLI `plugin new` refused and Plugins.catalog hides dev plugins
    Settings(advanced_features=[]).save(spath)
    rc_closed, res_closed = run_cli(["--settings", str(spath), "plugin", "new", "my-bridge", "--dir", str(dev_dir)])
    assert rc_closed == 1 and "Advanced" in res_closed["error"]

    # Enable cli + plugin_sdk in Advanced -> CLI scaffolds working plugin
    Settings(advanced_features=["cli", "plugin_sdk"]).save(spath)
    rc_new, res_new = run_cli(["--settings", str(spath), "plugin", "new", "my-bridge", "--dir", str(dev_dir)])
    assert rc_new == 0 and res_new["id"] == "my-bridge"
    pdir = dev_dir / "my-bridge"

    plugins = Plugins(tmp_path / "plugins")

    # With Advanced closed -> dev plugins are NOT listed and load_dev refuses
    closed_cfg = Settings(advanced_features=[])
    cat_closed = plugins.catalog(settings=closed_cfg, dev_dir=dev_dir)
    assert cat_closed["dev"] == []
    with pytest.raises(PermissionError, match="Advanced"):
        plugins.load_dev("my-bridge", {}, dev_dir=dev_dir, settings=closed_cfg)

    # With Advanced plugin_sdk enabled -> unsigned dev plugin appears with unsigned badge and loads
    open_cfg = Settings(advanced_features=["plugin_sdk"])
    cat_open = plugins.catalog(settings=open_cfg, dev_dir=dev_dir)
    assert len(cat_open["dev"]) == 1
    assert cat_open["dev"][0]["id"] == "my-bridge"
    assert cat_open["dev"][0]["unsigned"] is True
    assert cat_open["dev"][0]["badge"] == "μη υπογεγραμμένο"
    adapter = plugins.load_dev("my-bridge", {}, dev_dir=dev_dir, settings=open_cfg)
    assert adapter.connect() == "my-bridge-bot"
    adapter.close()

    # Sign via CLI -> signature verifies; tamper breaks signature
    key = generate_signing_key()
    rc_sign, res_sign = run_cli(["--settings", str(spath), "plugin", "sign", str(pdir), "--key", key])
    assert rc_sign == 0 and res_sign["algorithm"] == "hmac-sha256"
    ok, _ = verify_plugin_signature(pdir, key)
    assert ok is True
    (pdir / "adapter.py").write_text((pdir / "adapter.py").read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    ok_tampered, _ = verify_plugin_signature(pdir, key)
    assert ok_tampered is False
