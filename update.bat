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
:: B2: Backend ist seit N-001 nur ueber nginx (FRONTEND_PORT) erreichbar,
:: nicht mehr direkt auf 8000. API-Backup-Fehlschlag wird jetzt sichtbar.
curl -sf -X POST http://localhost:%FRONTEND_PORT%/api/system/backup
if %ERRORLEVEL% EQU 0 (
    echo.
    echo         API-Backup erstellt.
) else (
    echo.
    echo  [WARNUNG] API-Backup fehlgeschlagen ^(Backend nicht erreichbar^).
    echo  Lokale Sicherheitskopie wurde bereits erstellt — Update faehrt fort.
    echo  Pruefe nach dem Update: docker compose logs backend
)

:: =============================================
:: Schritt 2b: ChromaDB-Volume sichern (Vector-Index)
:: =============================================
:: ChromaDB-Volume wird beim Major-Bump (0.6 -> 1.x) inkompatibel.
:: Tar-Backup erlaubt Rollback ohne Datenverlust.
docker volume inspect zettelwirtschaft_chromadb-data >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo         Sichere ChromaDB-Volume...
    docker run --rm -v zettelwirtschaft_chromadb-data:/src -v "%CD%\%BACKUP_DIR%":/dst alpine tar czf /dst/chromadb-volume.tar.gz -C /src . >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo         ChromaDB-Volume gesichert: %BACKUP_DIR%\chromadb-volume.tar.gz
    ) else (
        echo  [WARNUNG] ChromaDB-Volume konnte nicht gesichert werden.
        echo  Falls das Update den Vector-Index verliert, neu aufbauen via "Vektor-Index neu aufbauen" in den Einstellungen.
    )
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
:: B2: Health-Check ueber nginx (FRONTEND_PORT), nicht direkt auf 8000.
curl -sf http://localhost:%FRONTEND_PORT%/api/health >nul 2>&1
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
