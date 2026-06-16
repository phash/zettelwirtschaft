@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft Restore Launcher (Admin)
REM Aufruf: restore-backup.bat <backup-file.zip>
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Fordere Administrator-Rechte an...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
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
