@echo off
setlocal
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if exist "%SCRIPT_DIR%\.venv-main\Scripts\python.exe" (
    "%SCRIPT_DIR%\.venv-main\Scripts\python.exe" -m butler.text.client %*
) else if exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%\.venv\Scripts\python.exe" -m butler.text.client %*
) else (
    py -3 -m butler.text.client %*
)
