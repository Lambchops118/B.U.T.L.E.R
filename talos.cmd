@echo off
REM Start the whole TALOS stack from one place.
REM
REM   talos.cmd            open the launcher GUI
REM   talos.cmd --no-gui   start everything headless in this console
REM
REM Arguments pass through to `python -m talos.launcher`. The launcher pins the
REM LLM (Ollama) to the RTX 5080 and speech-to-text to the RTX 2060, brings up
REM the awareness Postgres container, runs migrations, and supervises the main
REM agent, voice worker, and awareness backend together.

setlocal
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if exist "%SCRIPT_DIR%.venv-main\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv-main\Scripts\python.exe" -m talos.launcher %*
) else if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m talos.launcher %*
) else (
    py -3 -m talos.launcher %*
)
