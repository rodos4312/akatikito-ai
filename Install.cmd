@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   Akatikito — 1-Click Local Installer (Windows)
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ was not found in PATH.
    echo Download Python from https://www.python.org/downloads/ and check "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating local virtual environment (.venv)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Using existing .venv...
)

echo [2/5] Installing core dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
if exist "requirements.txt" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Core dependency installation failed.
        pause
        exit /b 1
    )
)

echo [3/5] Installing feature & Tree-sitter packages...
if exist "requirements-features.txt" (
    ".venv\Scripts\python.exe" -m pip install -r requirements-features.txt
    if errorlevel 1 (
        echo [WARN] Some optional packages failed; Akatikito will still run with built-in fallbacks.
    )
)

echo [4/5] Checking Node.js for optional Vue/Svelte/Angular parsers...
where npm.cmd >nul 2>&1
if not errorlevel 1 (
    if exist "akatikito\template_parsers\package.json" (
        call npm.cmd install --prefix akatikito\template_parsers --no-audit --no-fund --silent
    )
) else (
    echo [INFO] Node.js not found — skipping optional Vue/Svelte/Angular parsers.
)

echo [5/5] Creating windowless Start Menu & Desktop shortcuts...
".venv\Scripts\python.exe" scripts\create_shortcuts.py

echo.
echo ============================================================
echo   Installation complete!
echo   Launch Akatikito from your Desktop or Start Menu shortcut
echo   (opens cleanly with NO black console window).
echo ============================================================
pause
