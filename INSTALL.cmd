@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local.ps1" -Launch
if errorlevel 1 (
    echo.
    echo Installation did not complete. See the error above.
    pause
    exit /b 1
)
endlocal
