@echo off
rem Akatikito — zero-Python portable launcher (uses bundled python\ first, then .venv\)
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not "%~1"=="" (
    if exist "python\python.exe" (
        "python\python.exe" app.py %*
        exit /b %ERRORLEVEL%
    )
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" app.py %*
        exit /b %ERRORLEVEL%
    )
    echo Akatikito Python runtime not found. Re-extract the portable ZIP or run Install.cmd.
    exit /b 1
)

if exist "python\pythonw.exe" (
    start "" /B "python\pythonw.exe" app.py
    exit /b 0
)
if exist "python\python.exe" (
    "python\python.exe" app.py
    exit /b %ERRORLEVEL%
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" /B ".venv\Scripts\pythonw.exe" app.py
    exit /b 0
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
    exit /b %ERRORLEVEL%
)

echo Akatikito is not installed yet. Re-extract the portable ZIP or run Install.cmd.
pause
exit /b 1
