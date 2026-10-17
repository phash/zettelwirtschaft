@echo off
title Zettelwirtschaft - Start
cd /d "%~dp0"

echo.
echo  Zettelwirtschaft wird gestartet...
echo.

:: Pruefen ob Docker laeuft
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [FEHLER] Docker Desktop laeuft nicht.
    echo  Bitte starte Docker Desktop und versuche es erneut.
    echo.
    pause
    exit /b 1
)

:: Compose-Konfiguration vorbereiten (IMMER, auch ohne data\.host-mounts.json:
:: Host-Ordner-Mounts, GPU-Block, HTTPS-Overlay via COMPOSE_FILE in .env).
if not exist generate-mounts.ps1 (
    echo  [FEHLER] generate-mounts.ps1 fehlt im Installationsordner.
    echo  Bitte Zettelwirtschaft neu installieren oder aktualisieren.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File generate-mounts.ps1
if %ERRORLEVEL% EQU 1 (
    echo.
    echo  [FEHLER] Die Docker-Konfiguration ist ungueltig - siehe Meldung oben.
    pause
    exit /b 1
)

:: Services starten
echo  Starte Services...
docker compose up -d
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [FEHLER] Services konnten nicht gestartet werden.
    pause
    exit /b 1
)

echo.
echo  Warte auf Zettelwirtschaft...
:: Health-Check ueber nginx (HTTP auf FRONTEND_PORT bzw. HTTPS), max. 2 Minuten.
powershell -NoProfile -ExecutionPolicy Bypass -File wait-health.ps1 -TimeoutSeconds 120 -OpenBrowser
if %ERRORLEVEL% NEQ 0 (
    echo.
    pause
    exit /b 1
)
echo.
timeout /t 3 /nobreak >nul
