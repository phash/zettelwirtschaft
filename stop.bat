@echo off
title Zettelwirtschaft - Stop
cd /d "%~dp0"

echo.
echo  Zettelwirtschaft wird gestoppt...
echo.

docker compose down

if %ERRORLEVEL% EQU 0 (
    echo.
    echo  Alle Services wurden gestoppt.
) else (
    echo.
    echo  [FEHLER] Beim Stoppen ist ein Fehler aufgetreten.
)
echo.
timeout /t 3 /nobreak >nul
