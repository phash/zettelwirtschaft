@echo off
setlocal enabledelayedexpansion
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

:: =============================================
:: Schritt 1: Lokales Datei-Backup
:: =============================================
echo  [1/5] Erstelle Sicherheitskopie der Daten...

if not exist data (
    echo         Kein Datenverzeichnis vorhanden, ueberspringe Backup.
    goto :api_backup
)

:: Backup-Verzeichnis mit Zeitstempel
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /format:list') do set datetime=%%I
set TIMESTAMP=%datetime:~0,8%_%datetime:~8,6%
set BACKUP_DIR=data\backups\pre-update_%TIMESTAMP%

mkdir "%BACKUP_DIR%" 2>nul

:: Datenbank sichern (kritischste Datei)
if exist data\zettelwirtschaft.db (
    copy /y data\zettelwirtschaft.db "%BACKUP_DIR%\zettelwirtschaft.db" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo         Datenbank gesichert.
    ) else (
        echo  [FEHLER] Datenbank konnte nicht gesichert werden!
        echo  Das Update wird aus Sicherheitsgruenden abgebrochen.
        pause
        exit /b 1
    )
)

:: .env sichern
if exist .env (
    copy /y .env "%BACKUP_DIR%\.env" >nul 2>&1
    echo         Konfiguration gesichert.
)

echo         Backup-Verzeichnis: %BACKUP_DIR%

:: =============================================
:: Schritt 2: API-Backup (falls Backend laeuft)
:: =============================================
:api_backup
echo  [2/5] Erstelle vollstaendiges API-Backup...
curl -sf -X POST http://localhost:8000/api/system/backup >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo         API-Backup erstellt.
) else (
    echo         API-Backup uebersprungen (Backend nicht erreichbar).
    echo         Lokale Sicherheitskopie wurde bereits erstellt.
)

:: =============================================
:: Schritt 3: Neueste Images laden
:: =============================================
echo  [3/5] Lade Updates...
docker compose pull 2>nul

:: =============================================
:: Schritt 4: Neu bauen und starten
:: =============================================
echo  [4/5] Starte Services neu...
:: Erkennen ob Quellcode vorhanden (Dev) oder nur Images (Release)
if exist backend (
    docker compose up --build -d
) else (
    docker compose up -d
)
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [FEHLER] Update fehlgeschlagen.
    echo.
    echo  Die Sicherheitskopie liegt in: %BACKUP_DIR%
    echo  Um die Datenbank wiederherzustellen:
    echo    copy "%BACKUP_DIR%\zettelwirtschaft.db" data\zettelwirtschaft.db
    echo.
    pause
    exit /b 1
)

:: =============================================
:: Schritt 5: Auf Health warten
:: =============================================
echo  [5/5] Warte auf Backend...
set WAIT_COUNT=0
:wait_loop
timeout /t 2 /nobreak >nul
curl -sf http://localhost:8000/api/health >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :healthy
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 30 (
    echo.
    echo  [WARNUNG] Backend antwortet nach 60 Sekunden nicht.
    echo  Pruefe die Logs mit: docker compose logs backend
    echo.
    echo  Die Sicherheitskopie liegt in: %BACKUP_DIR%
    pause
    exit /b 1
)
goto wait_loop

:healthy
echo.
echo  Update erfolgreich abgeschlossen!
echo  Zettelwirtschaft laeuft auf http://localhost:%FRONTEND_PORT%
echo.
echo  Sicherheitskopie: %BACKUP_DIR%
echo  (Kann nach erfolgreichem Test geloescht werden)
echo.
timeout /t 5 /nobreak >nul
