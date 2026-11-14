@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local.ps1" -Launch %*
if errorlevel 1 (
    echo.
    echo Installation did not complete. See the error above.
    pause
    popd
    exit /b 1
)
popd
exit /b 0
