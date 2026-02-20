@echo off
title Zettelwirtschaft - Installation
echo.
echo  Zettelwirtschaft - Installation wird gestartet...
echo.

:: PowerShell-Skript mit Bypass-Policy starten
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation fehlgeschlagen. Druecke eine Taste zum Beenden.
    pause >nul
)
