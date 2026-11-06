@echo off
rem Double-click to remove OSS Control Center. Your login and queue are kept unless you pass -RemoveData.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
set RC=%errorlevel%
echo.
pause
exit /b %RC%