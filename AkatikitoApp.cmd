@echo off
rem Akatikito — silent desktop launch (no black console window left open)
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\pythonw.exe" (
    start "" /B ".venv\Scripts\pythonw.exe" app.py
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
    exit /b %ERRORLEVEL%
)

echo Akatikito is not installed yet. Run Install.cmd first.
pause
exit /b 1
