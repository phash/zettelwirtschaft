@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft Update-Wizard Launcher (Admin + STA)
REM Aufruf: update-wizard.bat [InstallDir]
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Fordere Administrator-Rechte an...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "DIR=%~dp0"
set "WIZARD=%DIR%update-wizard.ps1"
if not exist "%WIZARD%" (
    echo FEHLER: %WIZARD% nicht gefunden.
    pause
    exit /b 1
)

chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%WIZARD%" %*
exit /b %errorlevel%
