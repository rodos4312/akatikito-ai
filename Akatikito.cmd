@echo off
rem Akatikito — κύρια εφαρμογή (ή `Akatikito.cmd acp ...` για Editor ACP)
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if /I "%~1"=="acp" (
  shift
  ".venv\Scripts\python.exe" -m akatikito.acp_server %1 %2 %3 %4 %5 %6 %7 %8
  exit /b %ERRORLEVEL%
)
".venv\Scripts\python.exe" app.py
pause
