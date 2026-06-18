@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft Update-Wizard Launcher (Admin + STA)
REM Aufruf: update-wizard.bat [InstallDir]
REM ============================================================

REM H2: Pfad + Argumente fuer die PowerShell-Single-Quote-Strings escapen. Ein
REM ' im Pfad (z.B. C:\Users\O'Brien\) wuerde den String sonst beenden -> Parse-
REM Fehler / Injection. In Single-Quote-Strings wird ' als '' escaped. Ausserhalb
REM des if-Blocks setzen (Batch expandiert %VAR% im Block schon beim Parsen).
set "SELF=%~f0"
set "SELF=%SELF:'=''%"
set "ARGS=%*"
if defined ARGS set "ARGS=%ARGS:'=''%"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Fordere Administrator-Rechte an...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%SELF%' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%SELF%' -Verb RunAs -ArgumentList '%ARGS%'"
    )
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
