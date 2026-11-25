"""Native Windows Global Hotkey (default: Alt+Space) to summon & focus Akatikito from anywhere.

Zero external dependencies: uses Win32 RegisterHotKey via ctypes on a daemon thread,
and cleanly degrades to a no-op on non-Windows or if another app owns the shortcut.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MOD_MAP = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
}

_VK_MAP = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c for c in range(ord("a"), ord("z") + 1)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """Parse a shortcut string like 'Alt+Space' or 'Ctrl+Shift+A' into (win32_mods, vk)."""
    raw = str(spec or "").strip()
    if not raw or raw.casefold() in ("off", "none", "disabled"):
        return None
    parts = [p.strip().casefold() for p in raw.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for m in parts[:-1]:
        flag = _MOD_MAP.get(m)
        if flag is None:
            return None
        mods |= flag
    key_token = parts[-1]
    if len(key_token) == 1 and "a" <= key_token <= "z":
        vk = ord(key_token.upper())
    else:
        vk = _VK_MAP.get(key_token, 0)
    if not mods or not vk:
        return None
    return mods | MOD_NOREPEAT, vk


def focus_window(win, title: str = "Akatikito") -> None:
    """Restore/raise the native pywebview window and focus the chat prompt box."""
    if win is None:
        return
    try:
        if hasattr(win, "restore"):
            win.restore()
    except Exception:
        pass
    try:
        if hasattr(win, "show"):
            win.show()
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowW.restype = wintypes.HWND
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            hwnd = user32.FindWindowW(None, str(title))
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    try:
        if hasattr(win, "evaluate_js"):
            win.evaluate_js("const p=document.getElementById('prompt');if(p){p.focus();}")
    except Exception:
        pass


class GlobalHotkey:
    """Background listener for a single system-wide Windows hotkey."""

    def __init__(self, spec: str, on_trigger: Callable[[], None], *, hotkey_id: int = 1) -> None:
        self.spec = spec
        self.on_trigger = on_trigger
        self.hotkey_id = int(hotkey_id)
        self._thread: threading.Thread | None = None
        self._tid: int = 0
        self._ready = threading.Event()
        self._registered = False
        self._stop = threading.Event()

    def start(self) -> bool:
        if os.name != "nt":
            return False
        parsed = parse_hotkey(self.spec)
        if parsed is None:
            return False
        mods, vk = parsed

        def _loop() -> None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentThreadId.argtypes = []
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL

            self._tid = int(kernel32.GetCurrentThreadId())
            ok = bool(user32.RegisterHotKey(None, self.hotkey_id, mods, vk))
            self._registered = ok
            self._ready.set()
            if not ok:
                return
            msg = wintypes.MSG()
            try:
                while not self._stop.is_set():
                    res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if res <= 0:
                        break
                    if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                        try:
                            self.on_trigger()
                        except Exception:
                            pass
            finally:
                try:
                    user32.UnregisterHotKey(None, self.hotkey_id)
                except Exception:
                    pass

        self._thread = threading.Thread(target=_loop, daemon=True, name="akatikito-hotkey")
        self._thread.start()
        self._ready.wait(timeout=1.5)
        return self._registered

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt" and self._tid:
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.PostThreadMessageW.restype = wintypes.BOOL
                user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._registered = False
