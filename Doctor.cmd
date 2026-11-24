@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m akatikito.doctor %*
) else (
    python -m akatikito.doctor %*
)
echo.
pause
