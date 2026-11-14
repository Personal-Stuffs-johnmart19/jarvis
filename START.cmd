@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist "%~dp0.venv\Scripts\pythonw.exe" goto setup
if not exist "data\setup-complete.txt" goto setup
findstr /x /c:"unified-v4" "data\setup-complete.txt" >nul
if errorlevel 1 goto setup
:launch
start "" /b "%~dp0.venv\Scripts\pythonw.exe" -X utf8 "%~dp0launch_local.py" %*
if errorlevel 1 goto launch_failed
popd
exit /b 0
:setup
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local.ps1"
if errorlevel 1 goto setup_failed
goto launch
:setup_failed
echo.
echo Could not prepare Jarvis. See the error above. Run INSTALL.cmd to retry.
pause
popd
exit /b 1
:launch_failed
echo.
echo Could not start Jarvis. See data\logs\startup.log or run INSTALL.cmd to repair.
pause
popd
exit /b 1
