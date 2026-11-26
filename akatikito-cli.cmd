@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -m akatikito.cli %*
) else (
  python -m akatikito.cli %*
)
exit /b %ERRORLEVEL%
