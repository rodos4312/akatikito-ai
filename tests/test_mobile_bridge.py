"""Tests for opt-in MobileBridge LAN pairing & authentication."""
from __future__ import annotations


def test_mobile_bridge_pairing_login_and_auth():
    from akatikito.app_api import AppState

    state = AppState()
    assert state.mobile_bridge.active is False

    info = state.mobile_bridge.start(port=0, host="127.0.0.1")
    assert info["active"] is True
    assert len(info["pin"]) == 6
    assert info["token"]
    assert info["pairing_uri"].startswith("akatikito://pair?")

    try:
        # Wrong PIN rejected
        ok_bad, err_bad = state.mobile_bridge._authenticate_login("000000", info["token"])
        assert ok_bad is False
        assert err_bad

        # Wrong token rejected
        ok_bad2, _ = state.mobile_bridge._authenticate_login(info["pin"], "wrong-token")
        assert ok_bad2 is False

        # Valid login returns session_token
        ok_good, sess_tok = state.mobile_bridge._authenticate_login(info["pin"], info["token"])
        assert ok_good is True
        assert len(sess_tok) > 20

        # Session validation works and rejects random tokens
        assert state.mobile_bridge._is_session_valid(sess_tok) is True
        assert state.mobile_bridge._is_session_valid("invalid-session") is False
        assert state.mobile_bridge.status()["paired_devices"] == 1
    finally:
        stopped = state.mobile_bridge_stop()
        assert stopped["active"] is False
        assert stopped["paired_devices"] == 0
