@echo off
rem Akatikito — main launcher (bundled python\ first, then .venv\; supports `Akatikito.cmd acp ...`)
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PYEXE=python\python.exe"
if not exist "%PYEXE%" set "PYEXE=.venv\Scripts\python.exe"

if /I "%~1"=="acp" (
  shift
  "%PYEXE%" -m akatikito.acp_server %1 %2 %3 %4 %5 %6 %7 %8
  exit /b %ERRORLEVEL%
)
"%PYEXE%" app.py %*
pause
