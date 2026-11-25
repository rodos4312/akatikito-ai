"""Tests for native Windows System Tray (akatikito/tray.py)."""
from __future__ import annotations

from akatikito.tray import CMD_DOCTOR, CMD_OPEN, CMD_QUIT, SystemTray, resolve_tray_icon


def test_resolve_tray_icon_finds_bundled_ico():
    ico = resolve_tray_icon()
    assert ico is not None and ico.is_file() and ico.suffix == ".ico"


def test_tray_stop_is_safe_and_idempotent_without_start():
    tray = SystemTray(tooltip="Test Tray")
    tray.stop()
    tray.stop()
    assert len({CMD_OPEN, CMD_DOCTOR, CMD_QUIT}) == 3
