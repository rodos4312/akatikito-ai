"""D5 Plugin SDK tests: scaffold, dev folder gate + unsigned badge, HMAC signing & tamper detection."""
from __future__ import annotations

import pytest

from akatikito.channel_plugins import manifest
from akatikito.plugin_sdk import (
    discover_dev_plugins,
    generate_signing_key,
    scaffold_plugin,
    sign_plugin,
    verify_plugin_signature,
)
from akatikito.settings import Settings


def test_plugin_sdk_scaffold_dev_gate_signing_and_tamper_detection(tmp_path):
    dev_dir = tmp_path / "dev-plugins"
    dev_dir.mkdir()
    created = scaffold_plugin(dev_dir, "demo-hook", name="Demo Hook")
    pdir = dev_dir / "demo-hook"
    assert created["id"] == "demo-hook"
    assert (pdir / "adapter.py").is_file() and (pdir / "test_plugin.py").is_file()
    info = manifest((pdir / "akatikito-plugin.json").read_text(encoding="utf-8"))
    assert info["kind"] == "channel" and info["api_version"] == 1

    # Closed by default -> dev folder discovery refused
    with pytest.raises(PermissionError, match="Advanced"):
        discover_dev_plugins(dev_dir, Settings(advanced_features=[]))

    # Open SDK -> unsigned plugin loads with visible "μη υπογεγραμμένο" badge
    cfg = Settings(advanced_features=["plugin_sdk"])
    unsigned = discover_dev_plugins(dev_dir, cfg)
    assert len(unsigned) == 1
    assert unsigned[0]["unsigned"] is True
    assert unsigned[0]["badge"] == "μη υπογεγραμμένο"

    # Sign plugin -> verifies cleanly and clears unsigned badge
    key = generate_signing_key()
    sig = sign_plugin(pdir, key)
    assert sig["algorithm"] == "hmac-sha256"
    ok, _ = verify_plugin_signature(pdir, key)
    assert ok is True
    signed = discover_dev_plugins(dev_dir, cfg, trusted_key=key)
    assert signed[0]["unsigned"] is False and signed[0]["badge"] == "υπογεγραμμένο"

    # Single-byte tamper invalidates signature immediately
    adapter_file = pdir / "adapter.py"
    adapter_file.write_text(adapter_file.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    ok_after, reason = verify_plugin_signature(pdir, key)
    assert ok_after is False and "άλλαξαν" in reason
