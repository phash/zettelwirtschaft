@echo off
setlocal enabledelayedexpansion
title Zettelwirtschaft - Update
cd /d "%~dp0"

:: Hinweis: enabledelayedexpansion entfernt "!" aus echo-Texten - keine
:: Ausrufezeichen in Meldungen verwenden. Klammern in echo-Texten innerhalb
:: von ( ... )-Bloecken als ^( ^) escapen.

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

:: =============================================
:: Schritt 1: Lokales Datei-Backup
:: =============================================
echo  [1/5] Erstelle Sicherheitskopie der Daten...

set "BACKUP_DIR="
if not exist data (
    echo         Kein Datenverzeichnis vorhanden, ueberspringe Backup.
    goto :api_backup
)

:: Zeitstempel ueber PowerShell (WMIC fehlt auf aktuellem Windows 11)
set "TIMESTAMP="
for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TIMESTAMP=%%I"
if not defined TIMESTAMP (
    echo  [FEHLER] Zeitstempel fuer das Backup konnte nicht ermittelt werden.
    echo  Das Update wird aus Sicherheitsgruenden abgebrochen.
    pause
    exit /b 1
)
set "BACKUP_DIR=data\backups\pre-update_%TIMESTAMP%"

mkdir "%BACKUP_DIR%"
if not exist "%BACKUP_DIR%\" (
    echo  [FEHLER] Backup-Verzeichnis konnte nicht angelegt werden: %BACKUP_DIR%
    echo  Das Update wird aus Sicherheitsgruenden abgebrochen.
    pause
    exit /b 1
)

:: Datenbank sichern (kritischste Datei)
if exist data\zettelwirtschaft.db (
    copy /y data\zettelwirtschaft.db "%BACKUP_DIR%\zettelwirtschaft.db" >nul
    if !ERRORLEVEL! EQU 0 (
        echo         Datenbank gesichert.
    ) else (
        echo  [FEHLER] Datenbank konnte nicht gesichert werden.
        echo  Das Update wird aus Sicherheitsgruenden abgebrochen.
        pause
        exit /b 1
    )
)

:: .env sichern (enthaelt PIN und E-Mail-Schluessel)
if exist .env (
    copy /y .env "%BACKUP_DIR%\.env" >nul
    if !ERRORLEVEL! EQU 0 (
        echo         Konfiguration gesichert.
    ) else (
        echo  [FEHLER] Konfiguration ^(.env^) konnte nicht gesichert werden.
        echo  Das Update wird aus Sicherheitsgruenden abgebrochen.
        pause
        exit /b 1
    )
)

echo         Backup-Verzeichnis: %BACKUP_DIR%

:: =============================================
:: Schritt 2: Vollstaendiges Backup im Container (DB + Dokumente)
:: =============================================
:api_backup
echo  [2/5] Erstelle vollstaendiges Backup ^(Datenbank + Dokumente^)...
:: Direkt im Backend-Container statt per HTTP: die API verlangt bei aktivem
:: PIN-Schutz (Installer-Default) eine Anmeldung und antwortete sonst mit 401.
docker compose exec -T backend python -m app.entrypoint --backup --full
set "FULL_RC=%ERRORLEVEL%"
if "%FULL_RC%"=="0" (
    echo         Backup erstellt ^(liegt in data\backups^).
) else (
    echo  [WARNUNG] Vollstaendiges Backup fehlgeschlagen ^(Exit %FULL_RC%^).
    echo  Laeuft das Backend nicht, ist das bei einem ersten Start normal.
    echo  Die lokale Sicherheitskopie aus Schritt 1 bleibt erhalten - Update faehrt fort.
)

:: =============================================
:: Schritt 2b: ChromaDB-Volume sichern (Vector-Index)
:: =============================================
:: ChromaDB-Volume wird beim Major-Bump (0.6 -> 1.x) inkompatibel.
:: Tar-Backup erlaubt Rollback ohne Datenverlust.
set "PROJECT="
for /f "tokens=2" %%n in ('docker compose config 2^>nul ^| findstr /b /c:"name:"') do if not defined PROJECT set "PROJECT=%%n"
if not defined PROJECT set "PROJECT=zettelwirtschaft"
if defined BACKUP_DIR (
    docker volume inspect %PROJECT%_chromadb-data >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo         Sichere ChromaDB-Volume...
        docker run --rm -v %PROJECT%_chromadb-data:/src -v "%CD%\%BACKUP_DIR%":/dst alpine tar czf /dst/chromadb-volume.tar.gz -C /src . >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            echo         ChromaDB-Volume gesichert: %BACKUP_DIR%\chromadb-volume.tar.gz
        ) else (
            echo  [WARNUNG] ChromaDB-Volume konnte nicht gesichert werden.
            echo  Falls das Update den Vector-Index verliert, neu aufbauen via "Vektor-Index neu aufbauen" in den Einstellungen.
        )
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
:: Host-Ordner/GPU/HTTPS-Konfiguration wie in start.bat vorbereiten
if exist generate-mounts.ps1 (
    powershell -NoProfile -ExecutionPolicy Bypass -File generate-mounts.ps1
    if !ERRORLEVEL! EQU 1 (
        echo  [FEHLER] Die Docker-Konfiguration ist ungueltig - siehe Meldung oben.
        pause
        exit /b 1
    )
)
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
    if defined BACKUP_DIR (
        echo  Die Sicherheitskopie liegt in: %BACKUP_DIR%
        echo  Um die Datenbank wiederherzustellen:
        echo    copy "%BACKUP_DIR%\zettelwirtschaft.db" data\zettelwirtschaft.db
    )
    echo.
    pause
    exit /b 1
)

:: =============================================
:: Schritt 5: Auf Health warten
:: =============================================
echo  [5/5] Warte auf Zettelwirtschaft...
:: Health-Check ueber nginx (HTTP auf FRONTEND_PORT bzw. HTTPS), max. 2 Minuten
powershell -NoProfile -ExecutionPolicy Bypass -File wait-health.ps1 -TimeoutSeconds 120
if %ERRORLEVEL% NEQ 0 (
    echo.
    if defined BACKUP_DIR echo  Die Sicherheitskopie liegt in: %BACKUP_DIR%
    pause
    exit /b 1
)

echo.
echo  Update erfolgreich abgeschlossen.
echo.
if defined BACKUP_DIR (
    echo  Sicherheitskopie: %BACKUP_DIR%
    echo  ^(Kann nach erfolgreichem Test geloescht werden^)
)
echo.
timeout /t 5 /nobreak >nul
