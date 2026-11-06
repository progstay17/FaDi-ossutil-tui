@echo off
rem Starts the installed OSS Control Center. Run install.cmd first if it is not installed yet.
setlocal
set "LAUNCHER=%LOCALAPPDATA%\FaDi-OSS-TUI\bin\fadi-oss-tui.cmd"
if exist "%LAUNCHER%" (
    call "%LAUNCHER%" %*
    exit /b %errorlevel%
)
echo OSS Control Center is not installed yet. Run install.cmd in this folder first.
exit /b 1