@echo off
:: service-uninstall.bat - Entfernt den Zettelwirtschaft-Service.
::
:: Aufruf vom Uninstaller:
::   service-uninstall.bat <INSTALL_DIR>

setlocal

set "INSTALL_DIR=%~1"
set "NSSM=%INSTALL_DIR%\nssm.exe"
set "SERVICE=ZettelwirtschaftBackend"

if "%INSTALL_DIR%"=="" (
    echo [FEHLER] INSTALL_DIR fehlt.
    exit /b 1
)

:: Service stoppen (idempotent — Fehler ignorieren)
echo [INFO] Stoppe %SERVICE%...
net stop "%SERVICE%" >nul 2>&1

if exist "%NSSM%" (
    "%NSSM%" remove "%SERVICE%" confirm >nul 2>&1
) else (
    sc delete "%SERVICE%" >nul 2>&1
)

:: Firewall-Regel entfernen
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-NetFirewallRule -DisplayName 'Zettelwirtschaft' -ErrorAction SilentlyContinue" >nul 2>&1

echo [INFO] Service entfernt.
endlocal
exit /b 0
