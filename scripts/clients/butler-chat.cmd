@echo off
setlocal
set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%\..\..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

if exist "%REPO_ROOT%\.venv-runtime\Scripts\python.exe" (
    "%REPO_ROOT%\.venv-runtime\Scripts\python.exe" -m butler.text.client %*
) else if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
    "%REPO_ROOT%\.venv\Scripts\python.exe" -m butler.text.client %*
) else (
    py -3 -m butler.text.client %*
)
