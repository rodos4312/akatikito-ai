@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
if exist "python\python.exe" (
    "python\python.exe" -m akatikito.doctor %*
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m akatikito.doctor %*
) else (
    python -m akatikito.doctor %*
)
echo.
pause
