@echo off
rem start_remote.cmd - runs Jarvis with the Bluetooth phone transport enabled.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 -u "%~dp0main.py" --remote %*
) else (
  python -X utf8 -u "%~dp0main.py" --remote %*
)