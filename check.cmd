@echo off
rem Akatikito quality gate - thin wrapper around scripts\check.py (QUALITY-PLAN-2 Q1).
rem   check.cmd                 full: ruff, size, health, JS, node UI tests, pytest
rem   check.cmd --fast          quick: everything except pytest
rem   check.cmd --staged        pre-commit: staged-aware fast checks
rem   check.cmd --only ruff,js  run specific steps only
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" scripts\check.py %*
exit /b %ERRORLEVEL%
