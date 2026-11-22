@echo off
rem Akatikito — κύρια εφαρμογή
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" app.py
pause
