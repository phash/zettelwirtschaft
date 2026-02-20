@echo off
title Zettelwirtschaft - Installation
echo.
echo  Zettelwirtschaft - Installation wird gestartet...
echo.

:: GUI-Installer starten (Fallback: CLI-Installer)
if exist "%~dp0install-gui.ps1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-gui.ps1"
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation fehlgeschlagen. Druecke eine Taste zum Beenden.
    pause >nul
)
