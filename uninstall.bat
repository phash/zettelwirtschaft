@echo off
setlocal enabledelayedexpansion
title Zettelwirtschaft - Deinstallation
cd /d "%~dp0"

:: Hinweis: enabledelayedexpansion entfernt "!" aus echo-Texten - keine
:: Ausrufezeichen in Meldungen verwenden. Klammern in echo-Texten innerhalb
:: von ( ... )-Bloecken als ^( ^) escapen.

echo.
echo  ===================================
echo   Zettelwirtschaft - Deinstallation
echo  ===================================
echo.
echo  WARNUNG: Dies stoppt alle Zettelwirtschaft-Services
echo  und entfernt die Docker-Container und -Images.
echo  Daten und KI-Modelle bleiben erhalten, ausser du bestaetigst
echo  das Loeschen gleich ausdruecklich.
echo.
set /p CONFIRM="  Fortfahren? (j/n): "
if /i not "%CONFIRM%"=="j" (
    echo  Abgebrochen.
    timeout /t 2 /nobreak >nul
    exit /b 0
)

:: Compose-Projektname fuer die Volume-Namen (Default: Ordnername)
set "PROJECT="
for /f "tokens=2" %%n in ('docker compose config 2^>nul ^| findstr /b /c:"name:"') do if not defined PROJECT set "PROJECT=%%n"

echo.
echo  [1/3] Stoppe und entferne Container und Images...
:: Volumes bleiben hier bewusst erhalten (Vektor-Index, KI-Modelle) -
:: geloescht wird nur nach ausdruecklicher Bestaetigung weiter unten.
docker compose down --rmi local --remove-orphans

:: Desktop-Verknuepfung entfernen
echo  [2/3] Entferne Desktop-Verknuepfung...
if exist "%USERPROFILE%\Desktop\Zettelwirtschaft.lnk" (
    del "%USERPROFILE%\Desktop\Zettelwirtschaft.lnk"
    echo         Verknuepfung entfernt.
) else (
    echo         Keine Verknuepfung gefunden.
)

echo  [3/3] Daten und KI-Modelle...
echo.
echo  Daten: Dokumente, Datenbank, Backups ^(Ordner data^) und der
echo  Suchindex des KI-Assistenten ^(Docker-Volume chromadb-data^).
set /p DELETE_DATA="  Auch alle Daten loeschen? (j/n): "
if /i "%DELETE_DATA%"=="j" (
    echo.
    echo  WARNUNG: Alle Dokumente und die Datenbank werden unwiderruflich geloescht.
    set /p CONFIRM2="  Wirklich loeschen? Eingabe 'JA' zum Bestaetigen: "
    if /i "!CONFIRM2!"=="JA" (
        if exist data (
            rmdir /s /q data
            echo  Datenverzeichnis geloescht.
        )
        call :remove_volume chromadb-data
    ) else (
        echo  Daten werden beibehalten.
    )
) else (
    echo  Daten werden beibehalten.
)

echo.
echo  KI-Modelle: die heruntergeladenen Ollama-Sprachmodelle ^(Docker-Volume
echo  ollama-models, mehrere GB^). Behalten spart bei einer Neuinstallation den
echo  erneuten Download.
set /p DELETE_MODELS="  KI-Modelle loeschen? (j/n): "
if /i "%DELETE_MODELS%"=="j" (
    call :remove_volume ollama-models
) else (
    echo  KI-Modelle werden beibehalten.
)

echo.
echo  Deinstallation abgeschlossen.
echo  Die Projektdateien verbleiben in: %~dp0
echo.
pause
exit /b 0

:: ------------------------------------------------------------
:: :remove_volume <compose-volume-name>
:: Entfernt das benannte Volume dieses Compose-Projekts (ueber die
:: Compose-Labels, unabhaengig vom Ordnernamen).
:: ------------------------------------------------------------
:remove_volume
set "FOUND="
if defined PROJECT (
    for /f "delims=" %%v in ('docker volume ls -q --filter "label=com.docker.compose.project=%PROJECT%" --filter "label=com.docker.compose.volume=%~1"') do (
        set "FOUND=1"
        docker volume rm "%%v" >nul
        if !ERRORLEVEL! EQU 0 (
            echo  Volume %%v geloescht.
        ) else (
            echo  [WARNUNG] Volume %%v konnte nicht geloescht werden.
        )
    )
)
if not defined FOUND echo  Kein Volume "%~1" gefunden.
exit /b 0
