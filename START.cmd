@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto setup
if not exist "data\setup-complete.txt" goto setup
findstr /x /c:"unified-v4" "data\setup-complete.txt" >nul
if errorlevel 1 goto setup
start "" /b ".venv\Scripts\pythonw.exe" -X utf8 "%~dp0launch_local.py" %*
exit /b 0
:setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local.ps1" -Launch
if errorlevel 1 (
    echo.
    echo Could not prepare Jarvis. See the error above.
    pause
    exit /b 1
)
endlocal
