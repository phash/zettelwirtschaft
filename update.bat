@echo off
title Zettelwirtschaft - Update
cd /d "%~dp0"

echo.
echo  ===================================
echo   Zettelwirtschaft - Update
echo  ===================================
echo.

:: Pruefen ob Docker laeuft
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [FEHLER] Docker Desktop laeuft nicht.
    echo  Bitte starte Docker Desktop und versuche es erneut.
    pause
    exit /b 1
)

:: .env lesen fuer Port
set FRONTEND_PORT=8080
if exist .env (
    for /f "tokens=1,2 delims==" %%a in ('findstr /r "^FRONTEND_PORT=" .env') do set FRONTEND_PORT=%%b
)

:: Backup erstellen
echo  [1/4] Erstelle Backup...
curl -sf -X POST http://localhost:8000/api/system/backup >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo         Backup erstellt.
) else (
    echo         Backup uebersprungen (Backend nicht erreichbar).
)

:: Neueste Images laden
echo  [2/4] Lade Updates...
docker compose pull

:: Neu bauen und starten
echo  [3/4] Baue und starte Services neu...
docker compose up --build -d
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [FEHLER] Update fehlgeschlagen.
    pause
    exit /b 1
)

:: Auf Health warten
echo  [4/4] Warte auf Backend...
:wait_loop
timeout /t 2 /nobreak >nul
curl -sf http://localhost:8000/api/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto wait_loop

echo.
echo  Update erfolgreich abgeschlossen!
echo  Zettelwirtschaft laeuft auf http://localhost:%FRONTEND_PORT%
echo.
timeout /t 5 /nobreak >nul
