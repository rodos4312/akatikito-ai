@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

echo ============================================================
echo   Akatikito — Απεγκατάσταση / Uninstall
echo ============================================================
echo Τα προσωπικά σου δεδομένα (φάκελος data\: ρυθμίσεις, μνήμη, κλειδιά)
echo ΔΙΑΤΗΡΟΥΝΤΑΙ από προεπιλογή.
echo.
set "PURGE_FLAG="
if /I "%~1"=="--purge-data" (
    set "PURGE_FLAG=--purge-data"
) else (
    set /p "DELDATA=Θέλεις να διαγραφούν ΚΑΙ τα δεδομένα χρήστη (data\); [ο/ΝΑΙ]: "
    if /I "%DELDATA%"=="YES" set "PURGE_FLAG=--purge-data"
    if /I "%DELDATA%"=="ΝΑΙ" set "PURGE_FLAG=--purge-data"
)

if exist "python\python.exe" (
    "python\python.exe" scripts\uninstall_app.py %PURGE_FLAG%
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\uninstall_app.py %PURGE_FLAG%
) else (
    python scripts\uninstall_app.py %PURGE_FLAG%
)
echo.
pause
