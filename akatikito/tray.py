"""Native Windows System Tray icon for Akatikito (zero external dependencies).

Uses Win32 Shell_NotifyIconW + a lightweight hidden message window on a daemon thread.
Supports:
- Left-click / double-click: restore and focus the Akatikito desktop window.
- Right-click context menu: Άνοιγμα Akatikito | 🩺 Γιατρός | Έξοδος.
Cleanly degrades to a safe no-op outside Windows.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTALIGN = 0x0008
TPM_BOTTOMALIGN = 0x0020

CMD_OPEN = 1001
CMD_DOCTOR = 1002
CMD_QUIT = 1003


def resolve_tray_icon(app_root: str | Path | None = None) -> Path | None:
    root = Path(app_root).resolve() if app_root else Path(__file__).resolve().parents[1]
    ico = root / "assets" / "akatikito.ico"
    return ico if ico.is_file() else None


class SystemTray:
    """Background Windows System Tray icon with context menu."""

    def __init__(
        self,
        *,
        tooltip: str = "Akatikito AI (Alt+Space)",
        icon_path: str | Path | None = None,
        on_open: Callable[[], None] | None = None,
        on_doctor: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        self.tooltip = str(tooltip or "Akatikito AI")[:120]
        self.icon_path = Path(icon_path) if icon_path else resolve_tray_icon()
        self.on_open = on_open or (lambda: None)
        self.on_doctor = on_doctor or self.on_open
        self.on_quit = on_quit or (lambda: None)
        self._thread: threading.Thread | None = None
        self._hwnd: int = 0
        self._ready = threading.Event()
        self._running = False

    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._running

        def _loop() -> None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                ]

            def _show_menu(hwnd: int) -> None:
                menu = user32.CreatePopupMenu()
                if not menu:
                    return
                try:
                    user32.AppendMenuW(menu, MF_STRING, CMD_OPEN, "Άνοιγμα Akatikito")
                    user32.AppendMenuW(menu, MF_STRING, CMD_DOCTOR, "🩺 Γιατρός")
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "Έξοδος")
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    user32.SetForegroundWindow(hwnd)
                    user32.TrackPopupMenu(menu, TPM_RIGHTALIGN | TPM_BOTTOMALIGN, pt.x, pt.y, 0, hwnd, None)
                finally:
                    user32.DestroyMenu(menu)

            def _wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_TRAYICON:
                    if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        try:
                            self.on_open()
                        except Exception:
                            pass
                        return 0
                    if lparam == WM_RBUTTONUP:
                        _show_menu(hwnd)
                        return 0
                elif msg == WM_COMMAND:
                    cmd = wparam & 0xFFFF
                    if cmd == CMD_OPEN:
                        try:
                            self.on_open()
                        except Exception:
                            pass
                    elif cmd == CMD_DOCTOR:
                        try:
                            self.on_doctor()
                        except Exception:
                            pass
                    elif cmd == CMD_QUIT:
                        try:
                            self.on_quit()
                        except Exception:
                            pass
                    return 0
                elif msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            hinst = kernel32.GetModuleHandleW(None)
            cls_name = f"AkatikitoTray_{os.getpid()}_{id(self)}"
            proc = WNDPROC(_wnd_proc)
            wc = WNDCLASSW()
            wc.lpfnWndProc = proc
            wc.hInstance = hinst
            wc.lpszClassName = cls_name
            user32.RegisterClassW(ctypes.byref(wc))

            hwnd = user32.CreateWindowExW(0, cls_name, cls_name, 0, 0, 0, 0, 0, None, None, hinst, None)
            self._hwnd = int(hwnd or 0)
            if not self._hwnd:
                self._ready.set()
                return

            hicon = 0
            if self.icon_path and self.icon_path.is_file():
                hicon = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not hicon:
                hicon = user32.LoadIconW(None, IDI_APPLICATION)

            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = hicon
            nid.szTip = self.tooltip[:127]

            ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
            self._running = ok
            self._ready.set()
            if not ok:
                user32.DestroyWindow(hwnd)
                return

            msg = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                try:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                except Exception:
                    pass
                self._running = False
                self._hwnd = 0

        self._thread = threading.Thread(target=_loop, daemon=True, name="akatikito-tray")
        self._thread.start()
        self._ready.wait(timeout=1.5)
        return self._running

    def stop(self) -> None:
        if os.name == "nt" and self._hwnd:
            try:
                import ctypes

                ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._running = False
