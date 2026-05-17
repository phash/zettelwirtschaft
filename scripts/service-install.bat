@echo off
:: service-install.bat - Registriert ZettelwirtschaftBackend als Windows-Service.
::
:: Aufruf vom Installer mit:
::   service-install.bat <INSTALL_DIR> <CONFIG_PATH> <LOG_DIR>
::
:: Idempotent: wenn der Service existiert, wird er neu konfiguriert (gleich
:: einem Upgrade-Pfad).

setlocal enabledelayedexpansion

set "INSTALL_DIR=%~1"
set "CONFIG_PATH=%~2"
set "LOG_DIR=%~3"

if "%INSTALL_DIR%"=="" (
    echo [FEHLER] INSTALL_DIR fehlt. Aufruf: service-install.bat INSTALL_DIR CONFIG_PATH LOG_DIR
    exit /b 1
)
if "%CONFIG_PATH%"=="" (
    echo [FEHLER] CONFIG_PATH fehlt.
    exit /b 1
)
if "%LOG_DIR%"=="" (
    echo [FEHLER] LOG_DIR fehlt.
    exit /b 1
)

set "NSSM=%INSTALL_DIR%\nssm.exe"
set "BACKEND_EXE=%INSTALL_DIR%\backend\zettelwirtschaft-backend.exe"
set "BACKEND_DIR=%INSTALL_DIR%\backend"
set "SERVICE=ZettelwirtschaftBackend"

if not exist "%NSSM%" (
    echo [FEHLER] nssm.exe fehlt: %NSSM%
    exit /b 1
)
if not exist "%BACKEND_EXE%" (
    echo [FEHLER] Backend-EXE fehlt: %BACKEND_EXE%
    exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Bestehenden Service stoppen (idempotent fuer Upgrade-Pfad)
sc query "%SERVICE%" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Stoppe vorhandenen Service...
    net stop "%SERVICE%" >nul 2>&1
    "%NSSM%" remove "%SERVICE%" confirm >nul 2>&1
    timeout /t 2 /nobreak >nul
)

echo [INFO] Installiere Service %SERVICE%...
"%NSSM%" install "%SERVICE%" "%BACKEND_EXE%"
if errorlevel 1 (
    echo [FEHLER] nssm install fehlgeschlagen.
    exit /b 1
)

"%NSSM%" set "%SERVICE%" AppDirectory "%BACKEND_DIR%"
"%NSSM%" set "%SERVICE%" AppEnvironmentExtra "ZETTELWIRTSCHAFT_CONFIG=%CONFIG_PATH%"
"%NSSM%" set "%SERVICE%" AppStdout "%LOG_DIR%\backend.log"
"%NSSM%" set "%SERVICE%" AppStderr "%LOG_DIR%\backend.log"
"%NSSM%" set "%SERVICE%" AppRotateFiles 1
"%NSSM%" set "%SERVICE%" AppRotateBytes 10485760
"%NSSM%" set "%SERVICE%" AppStdoutCreationDisposition 4
"%NSSM%" set "%SERVICE%" AppStderrCreationDisposition 4
"%NSSM%" set "%SERVICE%" Start SERVICE_AUTO_START
"%NSSM%" set "%SERVICE%" Description "Zettelwirtschaft Dokumentenmanagement"
"%NSSM%" set "%SERVICE%" DisplayName "Zettelwirtschaft"

:: Ollama als optionale Abhaengigkeit — nur setzen wenn vorhanden,
:: sonst startet unser Service nicht.
sc query "Ollama" >nul 2>&1
if not errorlevel 1 (
    "%NSSM%" set "%SERVICE%" DependOnService "Ollama"
    echo [INFO] Service-Dependency auf "Ollama" gesetzt.
) else (
    echo [INFO] Ollama-Service nicht gefunden — Dependency uebersprungen.
)

echo [INFO] Starte Service...
net start "%SERVICE%"
if errorlevel 1 (
    echo [WARNUNG] Service-Start fehlgeschlagen. Pruefe %LOG_DIR%\backend.log
    exit /b 1
)

echo [INFO] Service "%SERVICE%" laeuft.
endlocal
exit /b 0
