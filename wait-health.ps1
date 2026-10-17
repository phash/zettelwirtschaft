# wait-health.ps1 - Wartet, bis Zettelwirtschaft (Docker) antwortet.
#
# Gemeinsamer Helfer fuer start.bat und update.bat. Das Backend ist seit N-001
# nur intern erreichbar, daher wird ueber nginx geprueft:
#   - HTTP:  http://localhost:<FRONTEND_PORT>/api/health  (FRONTEND_PORT aus .env)
#   - HTTPS: https://localhost/api/health, wenn COMPOSE_FILE in .env das
#            SSL-Overlay enthaelt (siehe generate-mounts.ps1). Selbstsigniertes
#            Zertifikat -> curl -k.
#
# Exit 0 = erreichbar (URL wird ausgegeben, mit -OpenBrowser auch geoeffnet),
# Exit 1 = Zeitlimit ueberschritten.
# -UrlOnly: nur die Basis-URL ausgeben (Pipeline) und ohne Pruefung beenden -
# damit z.B. install-gui.ps1 dieselbe HTTP/HTTPS-Erkennung nutzt.

param(
    [string]$ProjectDir = $PSScriptRoot,
    [int]$TimeoutSeconds = 120,
    [switch]$OpenBrowser,
    [switch]$UrlOnly
)

$envFile = Join-Path $ProjectDir '.env'
$port = '8080'
$https = $false
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
        if ($line -match '^\s*FRONTEND_PORT\s*=\s*(\d+)\s*$') { $port = $Matches[1] }
        if ($line -match '^\s*COMPOSE_FILE\s*=.*docker-compose\.ssl\.yml') { $https = $true }
    }
}
$baseUrl = if ($https) { 'https://localhost' } else { "http://localhost:$port" }
if ($UrlOnly) { Write-Output $baseUrl; exit 0 }
$healthUrl = "$baseUrl/api/health"

Write-Host "  Warte auf $healthUrl " -NoNewline
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ok = $false
while ((Get-Date) -lt $deadline) {
    # curl.exe ist seit Windows 10 1803 Teil des Systems.
    & curl.exe -sfk -o NUL --max-time 5 $healthUrl 2>$null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Write-Host '.' -NoNewline
    Start-Sleep -Seconds 2
}
Write-Host ''

if (-not $ok) {
    Write-Host "  [FEHLER] Zettelwirtschaft antwortet nach $TimeoutSeconds Sekunden nicht ($healthUrl)." -ForegroundColor Red
    Write-Host '  Pruefe die Logs mit: docker compose logs backend' -ForegroundColor Red
    exit 1
}

Write-Host "  Zettelwirtschaft laeuft: $baseUrl" -ForegroundColor Green
if ($OpenBrowser) { Start-Process $baseUrl }
exit 0
