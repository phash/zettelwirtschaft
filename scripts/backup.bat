@echo off
setlocal
REM ============================================================
REM Zettelwirtschaft - Manuelles Backup (Datenbank, optional Dokumente)
REM Liest InstallDir/ConfigPath/DataDir aus der Registry und ruft
REM die Backend-Exe mit --backup.
REM Argumente (Reihenfolge egal): [/full] schliesst Dokumente ein,
REM ein Nicht-Flag-Argument ueberschreibt den ConfigPath.
REM ============================================================

for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Zettelwirtschaft" /v InstallDir 2^>nul ^| findstr /i "InstallDir"') do set "INSTALL_DIR=%%b"
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Zettelwirtschaft" /v ConfigPath 2^>nul ^| findstr /i "ConfigPath"') do set "CONFIG_PATH=%%b"
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Zettelwirtschaft" /v DataDir 2^>nul ^| findstr /i "DataDir"') do set "DATA_DIR=%%b"

:: Argumente verarbeiten: /full (Dokumente einschliessen) und/oder ConfigPath-Override
set "FULL="
:argloop
if "%~1"=="" goto argdone
if /i "%~1"=="/full" (
    set "FULL=--full"
) else if /i "%~1"=="--full" (
    set "FULL=--full"
) else (
    set "CONFIG_PATH=%~1"
)
shift
goto argloop
:argdone

set "BACKEND_EXE=%INSTALL_DIR%\backend\zettelwirtschaft-backend.exe"

if not exist "%BACKEND_EXE%" (
    echo FEHLER: Backend-Exe nicht gefunden: %BACKEND_EXE%
    echo Ist Zettelwirtschaft Native installiert?
    pause
    exit /b 1
)
if "%CONFIG_PATH%"=="" (
    echo FEHLER: ConfigPath nicht gefunden (Registry leer und kein Argument uebergeben).
    pause
    exit /b 1
)

if not "%DATA_DIR%"=="" if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
set "LOGFILE=%DATA_DIR%\logs\backup.log"

echo.
echo Erstelle Backup...
echo [%date% %time%] Starte manuelles Backup >> "%LOGFILE%"
"%BACKEND_EXE%" --config "%CONFIG_PATH%" --backup %FULL%
set "RC=%errorlevel%"
echo [%date% %time%] Backup beendet, Exit %RC% >> "%LOGFILE%"

echo.
if "%RC%"=="0" (
    echo Backup erfolgreich erstellt.
    if not "%DATA_DIR%"=="" echo Ablage: %DATA_DIR%\data\backups
) else (
    echo FEHLER: Backup fehlgeschlagen (Exit %RC%).
    if not "%LOGFILE%"=="" echo Details: %LOGFILE%
)
pause
exit /b %RC%
