@echo off
rem Akatikito quality gate: ruff (undefined names / syntax), pytest, node UI tests.
rem Runs from the pre-commit hook (.githooks/pre-commit) and by hand. Exit 1 = do not commit.
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/4] ruff
"%PY%" -m ruff --version >nul 2>&1
if not errorlevel 1 (
  "%PY%" -m ruff check --select F821,F811,E9,F63,F7 --isolated akatikito scripts tests app.py || goto :fail
) else (
  where ruff >nul 2>&1 || (echo ruff not found: install it with  "%PY%" -m pip install ruff & goto :fail)
  ruff check --select F821,F811,E9,F63,F7 --isolated akatikito scripts tests app.py || goto :fail
)

echo [2/4] UI JavaScript syntax
for %%f in (akatikito\ui\*.js akatikito\onboarding.js) do (
  node --check "%%f" || goto :fail
)

echo [3/4] node UI tests
for %%f in (tests\*.cjs) do (
  node "%%f" >nul || (echo FAILED: %%f & goto :fail)
)

echo [4/4] pytest
"%PY%" -m pytest -q -x -p no:cacheprovider || goto :fail

echo.
echo OK: all checks passed.
exit /b 0

:fail
echo.
echo CHECKS FAILED: fix the errors above before committing.
exit /b 1
