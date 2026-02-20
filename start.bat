@echo off
title Zettelwirtschaft - Start
cd /d "%~dp0"

echo.
echo  Zettelwirtschaft wird gestartet...
echo.

:: Pruefen ob Docker laeuft
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [FEHLER] Docker Desktop laeuft nicht.
    echo  Bitte starte Docker Desktop und versuche es erneut.
    echo.
    pause
    exit /b 1
)

:: .env lesen fuer Port
set FRONTEND_PORT=8080
if exist .env (
    for /f "tokens=1,2 delims==" %%a in ('findstr /r "^FRONTEND_PORT=" .env') do set FRONTEND_PORT=%%b
)

:: Services starten
echo  Starte Services...
docker compose up -d
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [FEHLER] Services konnten nicht gestartet werden.
    pause
    exit /b 1
)

echo.
echo  Warte auf Backend...
:wait_loop
timeout /t 2 /nobreak >nul
curl -sf http://localhost:8000/api/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto wait_loop

echo  Zettelwirtschaft laeuft!
echo.
echo  Oeffne http://localhost:%FRONTEND_PORT% im Browser...
start http://localhost:%FRONTEND_PORT%
echo.
timeout /t 3 /nobreak >nul
