@echo off
setlocal
set "ROOT=%~dp0"
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=check"
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" "%ROOT%scripts\sync_shared_core.py" %*
) else (
  python "%ROOT%scripts\sync_shared_core.py" %*
)
exit /b %ERRORLEVEL%
