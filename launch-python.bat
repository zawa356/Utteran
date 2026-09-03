@echo off
setlocal
echo This launcher uses PowerShell only for this run with ExecutionPolicy Bypass.
echo It does not change the computer's permanent execution policy.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-python.ps1" %*
set "UTTERAN_EXIT=%ERRORLEVEL%"
if not "%UTTERAN_EXIT%"=="0" pause
exit /b %UTTERAN_EXIT%
