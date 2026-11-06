@echo off
rem Double-click to install OSS Control Center for your user. Extra options are passed on, e.g.:
rem   install.cmd -InstallOssutil -AddToPath
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%errorlevel%
echo.
pause
exit /b %RC%