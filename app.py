"""Akatikito — standalone desktop application (native window, zero console under pythonw.exe).

Launch silently via: .venv\\Scripts\\pythonw.exe app.py  (or Desktop / Start Menu shortcut)
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from akatikito import APP_NAME  # noqa: E402
from akatikito.app_api import AppState, start_server  # noqa: E402


def _show_error_dialog(title: str, message: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, str(message)[:1500], str(title), 0x10)
        except Exception:
            pass


def _prepare_streams() -> bool:
    """Configure UTF-8 stdio when attached to a console; return True if windowless (pythonw)."""
    windowless = sys.stdout is None and sys.stderr is None
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        else:
            reconf = getattr(stream, "reconfigure", None)
            if callable(reconf):
                try:
                    reconf(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass
    return windowless


def _log_startup_error(text: str) -> Path | None:
    try:
        log_path = ROOT / "data" / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8")
        return log_path
    except OSError:
        return None


def main() -> int:
    windowless = _prepare_streams()
    state = None
    server = None
    server_thread = None
    hotkey = None
    tray = None
    try:
        state = AppState()
        url, server = start_server(state)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        if not windowless:
            print(f"{APP_NAME} — local server ready")
        url = server.ui_url

        try:
            import webview  # pywebview
            from akatikito.hotkey import GlobalHotkey, focus_window
            from akatikito.tray import SystemTray

            win = webview.create_window(
                APP_NAME,
                url,
                width=1280,
                height=820,
                min_size=(900, 600),
                background_color="#0f1117",
                text_select=True,
            )

            def _open_doctor():
                focus_window(win, APP_NAME)
                try:
                    if hasattr(win, "evaluate_js"):
                        win.evaluate_js("if(typeof runDoctor==='function'){runDoctor(false);}")
                except Exception:
                    pass

            def _quit_app():
                try:
                    if hasattr(win, "destroy"):
                        win.destroy()
                except Exception:
                    pass

            hk_spec = getattr(state.settings, "global_hotkey", "Alt+Space")
            if hk_spec:
                try:
                    hotkey = GlobalHotkey(hk_spec, lambda: focus_window(win, APP_NAME))
                    hotkey.start()
                except Exception:
                    hotkey = None
            try:
                tray = SystemTray(
                    tooltip=f"{APP_NAME} ({hk_spec or 'Tray'})",
                    on_open=lambda: focus_window(win, APP_NAME),
                    on_doctor=_open_doctor,
                    on_quit=_quit_app,
                )
                tray.start()
            except Exception:
                tray = None
            webview.start()
        except Exception as exc:  # noqa: BLE001 — fallback to system browser if pywebview unavailable
            if not windowless:
                print(f"Native window unavailable ({type(exc).__name__}: {exc}).")
                print(f"Opening in browser: {url}")
            try:
                import webbrowser

                webbrowser.open(url)
                if not windowless and sys.stdin is not None:
                    input("Press Enter to close… ")
                else:
                    # Keep local server alive in browser fallback until user closes
                    import time

                    while True:
                        time.sleep(1.0)
            except (KeyboardInterrupt, EOFError, Exception):  # noqa: BLE001
                pass
    except Exception:
        tb = traceback.format_exc()
        log_path = _log_startup_error(tb)
        if windowless:
            note = f"\n\nLog saved to: {log_path}" if log_path else ""
            _show_error_dialog(f"{APP_NAME} — Startup Error", f"Akatikito could not start.\n\n{tb[-600:]}{note}")
        else:
            print(tb, file=sys.stderr)
        return 1
    finally:
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        if hotkey is not None:
            try:
                hotkey.stop()
            except Exception:
                pass
        if state is not None:
            try:
                state.close()
            except Exception:
                pass
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        if server_thread is not None:
            server_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
