@echo off
setlocal
REM ============================================================
REM Zettelwirtschaft - Manuelles Backup (Datenbank, optional Dokumente)
REM Liest InstallDir/ConfigPath/DataDir aus der Registry und ruft
REM die Backend-Exe mit --backup.
REM Argumente (Reihenfolge egal): [/full] schliesst Dokumente ein,
REM ein Nicht-Flag-Argument ueberschreibt den ConfigPath.
REM
REM ACHTUNG Batch-Syntax: in ( ... )-Bloecken muessen Klammern in echo-Texten
REM als ^( ^) escaped werden, sonst beendet ")" den Block und das ganze Skript
REM bricht mit einem Syntaxfehler ab (scripts\test-scripts.ps1 prueft das).
REM ============================================================

REM Registry-Schluessel ueberschreibbar (nur fuer scripts\test-scripts.ps1).
if not defined ZW_REGKEY set "ZW_REGKEY=HKLM\SOFTWARE\Zettelwirtschaft"

call :readreg InstallDir INSTALL_DIR
call :readreg ConfigPath CONFIG_PATH
call :readreg DataDir DATA_DIR

REM Fallback: das Skript liegt im Programmordner der Installation.
if not defined INSTALL_DIR if exist "%~dp0backend\zettelwirtschaft-backend.exe" set "INSTALL_DIR=%~dp0."

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
    echo FEHLER: ConfigPath nicht gefunden ^(Registry leer und kein Argument uebergeben^).
    pause
    exit /b 1
)

set "LOGFILE="
if not "%DATA_DIR%"=="" (
    if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
    set "LOGFILE=%DATA_DIR%\logs\backup.log"
)

echo.
echo Erstelle Backup...
if defined LOGFILE echo [%date% %time%] Starte manuelles Backup %FULL% >> "%LOGFILE%"
"%BACKEND_EXE%" --config "%CONFIG_PATH%" --backup %FULL%
set "RC=%errorlevel%"
if defined LOGFILE echo [%date% %time%] Backup beendet, Exit %RC% >> "%LOGFILE%"

echo.
if "%RC%"=="0" (
    echo Backup erfolgreich erstellt.
    if not "%DATA_DIR%"=="" echo Ablage: %DATA_DIR%\data\backups
) else (
    echo FEHLER: Backup fehlgeschlagen ^(Exit %RC%^).
    if defined LOGFILE echo Details: %LOGFILE%
)
pause
exit /b %RC%

REM ------------------------------------------------------------
REM :readreg <Wertname> <Variable>
REM Liest einen Wert aus %ZW_REGKEY% (64-Bit-Ansicht). Fallback auf die
REM 32-Bit-Ansicht: aeltere Setups schrieben als 32-Bit-Prozess nach WOW6432Node.
REM ------------------------------------------------------------
:readreg
set "%~2="
for /f "tokens=2,*" %%a in ('reg query "%ZW_REGKEY%" /v %~1 /reg:64 2^>nul ^| findstr /i /c:"%~1"') do set "%~2=%%b"
if defined %~2 exit /b 0
for /f "tokens=2,*" %%a in ('reg query "%ZW_REGKEY%" /v %~1 /reg:32 2^>nul ^| findstr /i /c:"%~1"') do set "%~2=%%b"
exit /b 0
