@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft - Eingangsordner fuer Netzwerk-Scanner freigeben
REM (Startmenue "Scanner-Freigabe einrichten"). Ruft freigabe-einrichten.ps1
REM auf; die Administrator-Rechte fordert das Skript selbst an (-Elevate).
REM Aufruf: freigabe-einrichten.bat [-Remove] [-NewPassword] [-WatchDir "<Ordner>"]
REM
REM F8: Die Argumente gehen unveraendert an "powershell -File" - nie in einen
REM Befehls-String fuer PowerShell eingebettet. Pfade mit Leerzeichen, Anfuehrungszeichen
REM oder "&" bleiben so erhalten, und "&" fuehrt keinen Befehl aus.
REM ============================================================

set "DIR=%~dp0"
set "SCRIPT=%DIR%freigabe-einrichten.ps1"
if not exist "%SCRIPT%" (
    echo FEHLER: %SCRIPT% nicht gefunden.
    pause
    exit /b 1
)

chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Elevate %*
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
