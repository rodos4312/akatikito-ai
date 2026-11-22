"""Ειδοποιήσεις στα Windows, χωρίς εγκατάσταση πρόσθετων.

- `toast()` → μπαλόνι/ειδοποίηση στην περιοχή ειδοποιήσεων (γλώσσα: PowerShell)
- `popup()` → παράθυρο που θέλει «OK» (γλώσσα: tkinter)
- `beep()`  → σύντομος ήχος

Καμία σύνδεση στο δίκτυο, τίποτα δεν φεύγει από το μηχάνημα.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_APP_ID = "Akatikito σημειωματάριο"

_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Text = $env:AKATIKITO_APP
$icon.Visible = $true
$icon.ShowBalloonTip(20000, $env:AKATIKITO_TITLE, $env:AKATIKITO_MSG, [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Seconds 10
$icon.Visible = $false
$icon.Dispose()
"""


def _powershell() -> str | None:
    found = shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return candidate if os.path.exists(candidate) else None


def beep() -> None:
    """Σύντομος ήχος· δεν σταματά αν δεν υπάρχει ηχείο."""
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:  # noqa: BLE001
        pass


def toast(title: str, message: str) -> bool:
    """Ειδοποίηση Windows. Επιστρέφει True αν ξεκίνησε σωστά."""
    shell = _powershell()
    if shell is None:
        return False
    env = dict(os.environ)
    env["AKATIKITO_TITLE"] = str(title)[:120]
    env["AKATIKITO_MSG"] = str(message)[:900]
    env["AKATIKITO_APP"] = _APP_ID
    try:
        subprocess.Popen(
            [shell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _TOAST_SCRIPT],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def popup(title: str, message: str) -> bool:
    """Παράθυρο μηνύματος που μένει μέχρι να πατηθεί «OK»."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(str(title), str(message))
        root.destroy()
        return True
    except Exception:  # noqa: BLE001
        return False


def notify(title: str, message: str, also_popup: bool = False) -> str:
    """Δείχνει την ειδοποίηση με ό,τι υπάρχει. Επιστρέφει τι δούλεψε."""
    beep()
    if also_popup and popup(title, message):
        return "παράθυρο"
    if toast(title, message):
        return "ειδοποίηση"
    # Τελευταία λύση: κείμενο στην κονσόλα/αρχείο καταγραφής.
    print(f"[{title}] {message}", flush=True)
    return "κονσόλα"


def self_test() -> None:
    """Δοκιμή από τη γραμμή εντολών: python -m akatikito.notify"""
    used = notify("Δοκιμή — Akatikito", "Αν το βλέπεις αυτό, οι ειδοποιήσεις δουλεύουν. 🎉")
    print(f"έγινε με: {used}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    self_test()
