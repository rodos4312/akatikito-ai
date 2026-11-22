"""Generate crisp multi-resolution Akatikito .ico and create/remove Windows windowless shortcuts."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_PATH = ROOT / "assets" / "akatikito.ico"


def generate_icon(dest: Path = ICON_PATH) -> Path:
    """Create a clean multi-size (16..256px) modern dark-indigo + cyan app icon."""
    from PIL import Image, ImageDraw

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded dark square background
    draw.rounded_rectangle((12, 12, size - 12, size - 12), radius=54, fill=(15, 20, 36, 255), outline=(59, 130, 246, 255), width=8)
    # Subtle inner highlight ring
    draw.rounded_rectangle((26, 26, size - 26, size - 26), radius=42, outline=(34, 211, 238, 120), width=3)
    # Stylized geometric "A" mark
    cyan = (56, 189, 248, 255)
    blue = (96, 165, 250, 255)
    # Left leg
    draw.polygon([(128, 54), (104, 54), (58, 198), (88, 198)], fill=blue)
    # Right leg
    draw.polygon([(128, 54), (152, 54), (198, 198), (168, 198)], fill=cyan)
    # Crossbar
    draw.rounded_rectangle((84, 142, 172, 164), radius=8, fill=(241, 245, 249, 255))
    # Spark dot at apex
    draw.ellipse((116, 42, 140, 66), fill=(186, 230, 253, 255))

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return dest


def _shortcut_paths() -> list[Path]:
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Akatikito.lnk")
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        desktop = Path(userprofile) / "Desktop"
        if desktop.is_dir():
            paths.append(desktop / "Akatikito.lnk")
    return paths


def create_windows_shortcuts(root: Path = ROOT, icon_path: Path = ICON_PATH, desktop: bool = True) -> list[Path]:
    """Create Start Menu (and optional Desktop) shortcuts pointing directly to pythonw.exe (zero console)."""
    if os.name != "nt":
        return []
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    target_exe = pythonw if pythonw.is_file() else Path(sys.executable)
    if not icon_path.is_file():
        generate_icon(icon_path)

    created: list[Path] = []
    for lnk in _shortcut_paths():
        if not desktop and lnk.parent.name.lower() == "desktop":
            continue
        lnk.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{str(lnk).replace(chr(39), chr(39)*2)}'); "
            f"$s.TargetPath = '{str(target_exe).replace(chr(39), chr(39)*2)}'; "
            "$s.Arguments = 'app.py'; "
            f"$s.WorkingDirectory = '{str(root).replace(chr(39), chr(39)*2)}'; "
            f"$s.IconLocation = '{str(icon_path).replace(chr(39), chr(39)*2)},0'; "
            "$s.Description = 'Akatikito Local AI Assistant'; "
            "$s.Save()"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0 and lnk.is_file():
            created.append(lnk)
    return created


def remove_windows_shortcuts() -> list[Path]:
    removed: list[Path] = []
    for lnk in _shortcut_paths():
        if lnk.is_file():
            lnk.unlink(missing_ok=True)
            removed.append(lnk)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or remove Akatikito windowless Windows shortcuts.")
    parser.add_argument("--remove", action="store_true", help="Remove Start Menu and Desktop shortcuts.")
    parser.add_argument("--no-desktop", action="store_true", help="Create only Start Menu shortcut.")
    parser.add_argument("--icon-only", action="store_true", help="Only generate assets/akatikito.ico.")
    args = parser.parse_args()

    if args.remove:
        removed = remove_windows_shortcuts()
        for p in removed:
            print(f"Removed shortcut: {p}")
        return 0

    icon = generate_icon()
    print(f"Generated icon: {icon}")
    if args.icon_only:
        return 0
    created = create_windows_shortcuts(desktop=not args.no_desktop)
    for p in created:
        print(f"Created windowless shortcut: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
