@echo off
rem Akatikito quality gate.
rem   check.cmd          full: ruff, UI JavaScript syntax, node UI tests, pytest  (pre-push, by hand)
rem   check.cmd --fast   quick: everything except pytest, a few seconds           (pre-commit)
rem Exit 1 = do not commit / push.
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/6] ruff (ruff.toml)
"%PY%" -m ruff --version >nul 2>&1
if not errorlevel 1 (
  "%PY%" -m ruff check --config ruff.toml akatikito scripts tests app.py || goto :fail
) else (
  where ruff >nul 2>&1 || (echo ruff not found: run  "%PY%" -m pip install -r requirements-dev.txt & goto :fail)
  ruff check --config ruff.toml akatikito scripts tests app.py || goto :fail
)

echo [2/6] file size ratchet
"%PY%" scripts\size_check.py || goto :fail

echo [3/6] complexity and long-line ratchet
"%PY%" scripts\health_check.py || goto :fail

echo [4/6] UI JavaScript syntax
for %%f in (akatikito\ui\*.js akatikito\onboarding.js) do (
  node --check "%%f" || goto :fail
)

echo [5/6] node UI tests
for %%f in (tests\*.cjs) do (
  node "%%f" >nul || (echo FAILED: %%f & goto :fail)
)

if /i "%~1"=="--fast" (
  echo [6/6] pytest skipped in --fast mode; it runs before push.
  goto :ok
)

echo [6/6] pytest
"%PY%" -m pytest -q -x -p no:cacheprovider || goto :fail

:ok
echo.
echo OK: all checks passed.
exit /b 0

:fail
echo.
echo CHECKS FAILED: fix the errors above first.
exit /b 1
