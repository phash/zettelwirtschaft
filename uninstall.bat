@echo off
setlocal enabledelayedexpansion
title Zettelwirtschaft - Deinstallation
cd /d "%~dp0"

echo.
echo  ===================================
echo   Zettelwirtschaft - Deinstallation
echo  ===================================
echo.
echo  WARNUNG: Dies stoppt alle Zettelwirtschaft-Services
echo  und entfernt die Docker-Container.
echo.
set /p CONFIRM="  Fortfahren? (j/n): "
if /i not "%CONFIRM%"=="j" (
    echo  Abgebrochen.
    timeout /t 2 /nobreak >nul
    exit /b 0
)

echo.
echo  [1/3] Stoppe und entferne Container...
docker compose down --volumes --remove-orphans 2>nul
docker compose down --remove-orphans 2>nul

echo  [2/3] Entferne Docker-Images...
docker compose down --rmi local 2>nul

:: Desktop-Verknuepfung entfernen
echo  [3/3] Entferne Desktop-Verknuepfung...
if exist "%USERPROFILE%\Desktop\Zettelwirtschaft.lnk" (
    del "%USERPROFILE%\Desktop\Zettelwirtschaft.lnk"
    echo         Verknuepfung entfernt.
) else (
    echo         Keine Verknuepfung gefunden.
)

echo.
set /p DELETE_DATA="  Auch alle Daten loeschen (Dokumente, Datenbank)? (j/n): "
if /i "%DELETE_DATA%"=="j" (
    echo.
    echo  WARNUNG: Alle Dokumente und die Datenbank werden unwiderruflich geloescht!
    set /p CONFIRM2="  Wirklich loeschen? Eingabe 'JA' zum Bestaetigen: "
    if /i "!CONFIRM2!"=="JA" (
        if exist data (
            rmdir /s /q data
            echo  Datenverzeichnis geloescht.
        )
    ) else (
        echo  Daten werden beibehalten.
    )
) else (
    echo  Daten werden beibehalten.
)

echo.
echo  Deinstallation abgeschlossen.
echo  Die Projektdateien verbleiben in: %~dp0
echo.
pause
