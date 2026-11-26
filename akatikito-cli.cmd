@echo off
setlocal
set "ROOT=%~dp0"
set PYTHONUTF8=1
if exist "%ROOT%python\python.exe" (
  "%ROOT%python\python.exe" -m akatikito.cli %*
) else if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -m akatikito.cli %*
) else (
  python -m akatikito.cli %*
)
exit /b %ERRORLEVEL%
