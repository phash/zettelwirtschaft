@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft Restore Launcher (Admin)
REM Aufruf: restore-backup.bat <backup-file.zip>
REM ============================================================

REM H2: Pfad + Argumente fuer die PowerShell-Single-Quote-Strings escapen (ein '
REM im Pfad wuerde den String sonst beenden -> Parse-Fehler / Injection). Der
REM Backup-ZIP-Pfad ist voll nutzerkontrolliert und liegt oft unter %USERPROFILE%.
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

if "%~1"=="" (
    echo Usage: restore-backup.bat ^<backup-file.zip^>
    echo.
    echo Beispiel: restore-backup.bat "%%USERPROFILE%%\Documents\Zettelwirtschaft\data\backups\backup_db_20260616_120000.zip"
    pause
    exit /b 1
)

set "DIR=%~dp0"
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DIR%restore-backup.ps1" -BackupZip "%~1"
exit /b %errorlevel%
