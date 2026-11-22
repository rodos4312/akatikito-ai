@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Removing Akatikito Desktop and Start Menu shortcuts...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\create_shortcuts.py --remove
) else (
    python scripts\create_shortcuts.py --remove
)
echo.
echo Shortcuts removed. You can now safely delete this folder if you wish to uninstall completely.
pause
