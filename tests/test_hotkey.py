"""Tests for Global Hotkey (akatikito/hotkey.py) and Settings persistence."""
from __future__ import annotations

from akatikito.hotkey import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, GlobalHotkey, focus_window, parse_hotkey
from akatikito.settings import Settings


def test_parse_hotkey_variants_and_disabled():
    assert parse_hotkey("Alt+Space") == (MOD_ALT | MOD_NOREPEAT, 0x20)
    assert parse_hotkey("Ctrl+Shift+A") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("A"))
    assert parse_hotkey("Ctrl+F12") == (MOD_CONTROL | MOD_NOREPEAT, 0x7B)
    assert parse_hotkey("") is None
    assert parse_hotkey("off") is None
    assert parse_hotkey("Space") is None  # requires at least one modifier
    assert parse_hotkey("BogusMod+A") is None


def test_focus_window_calls_restore_show_and_focus_js():
    events: list[str] = []

    class FakeWin:
        def restore(self):
            events.append("restore")

        def show(self):
            events.append("show")

        def evaluate_js(self, code):
            events.append("js:" + code)

    focus_window(FakeWin(), title="__NonExistentWindowTitle__")
    assert "restore" in events
    assert "show" in events
    assert any("prompt" in e for e in events)


def test_global_hotkey_disabled_start_and_stop_safe():
    hk = GlobalHotkey("off", lambda: None)
    assert hk.start() is False
    hk.stop()  # idempotent


def test_settings_persists_global_hotkey(tmp_path):
    p = tmp_path / "settings.json"
    st = Settings(global_hotkey="Ctrl+Shift+K")
    st.save(p)
    loaded = Settings.load(p)
    assert loaded.global_hotkey == "Ctrl+Shift+K"
