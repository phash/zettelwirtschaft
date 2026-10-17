# scripts/ci-local.ps1
# Lokaler CI-Lauf — ersetzt das frühere .github/workflows/ci.yml.
#
# Verwendung:
#   pwsh scripts/ci-local.ps1            # alles
#   pwsh scripts/ci-local.ps1 -SkipE2E   # ohne Playwright
#   pwsh scripts/ci-local.ps1 -SkipDocker
#   pwsh scripts/ci-local.ps1 -SkipScripts  # ohne Installer-/Ops-Skript-Smoke-Tests
#
# E2E laeuft gegen einen eigenen Preview-Server des aktuellen Builds (Port
# E2E_PORT, Standard 4181 — startet Playwright selbst per webServer), NIE gegen
# Port 8080: dort laeuft die echte Installation. Alle API-Aufrufe sind gemockt.
param(
    [switch]$SkipE2E,
    [switch]$SkipDocker,
    [switch]$SkipFrontendBuild,
    [switch]$SkipBackendTests,
    [switch]$SkipScripts
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$failed = @()

function Run-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  $Name" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        & $Action
        $sw.Stop()
        Write-Host "[OK] $Name ($([math]::Round($sw.Elapsed.TotalSeconds, 1))s)" -ForegroundColor Green
    } catch {
        $sw.Stop()
        Write-Host "[FAIL] $Name ($([math]::Round($sw.Elapsed.TotalSeconds, 1))s)" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        $script:failed += $Name
    }
}

# Backend-Tests (pytest)
if (-not $SkipBackendTests) {
    Run-Step "Backend pytest" {
        Push-Location backend
        try {
            python -m pytest tests/ -q --tb=short
            if ($LASTEXITCODE -ne 0) { throw "pytest exit $LASTEXITCODE" }
        } finally { Pop-Location }
    }
}

# Frontend-Build (npm ci + build) und Unit-Tests (Vitest)
if (-not $SkipFrontendBuild) {
    Run-Step "Frontend npm build" {
        Push-Location frontend
        try {
            if (-not (Test-Path node_modules)) {
                npm ci
                if ($LASTEXITCODE -ne 0) { throw "npm ci exit $LASTEXITCODE" }
            }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build exit $LASTEXITCODE" }
        } finally { Pop-Location }
    }
    Run-Step "Frontend Vitest" {
        Push-Location frontend
        try {
            npm test
            if ($LASTEXITCODE -ne 0) { throw "vitest exit $LASTEXITCODE" }
        } finally { Pop-Location }
    }
}

# Smoke-Tests fuer Installer- und Betriebs-Skripte (.bat/.ps1/NSIS-Config)
if (-not $SkipScripts) {
    Run-Step "Skript-Smoke-Tests" {
        pwsh -NoProfile -File scripts/test-scripts.ps1
        if ($LASTEXITCODE -ne 0) { throw "test-scripts exit $LASTEXITCODE" }
    }
}

# Docker-Build (backend + frontend)
if (-not $SkipDocker) {
    Run-Step "Docker build backend" {
        docker build -t zettelwirtschaft-backend ./backend
        if ($LASTEXITCODE -ne 0) { throw "docker build backend exit $LASTEXITCODE" }
    }
    Run-Step "Docker build frontend" {
        docker build --build-context repo=. -t zettelwirtschaft-frontend ./frontend
        if ($LASTEXITCODE -ne 0) { throw "docker build frontend exit $LASTEXITCODE" }
    }
}

# E2E-Tests (Playwright) — gemockte API, kein Backend/Stack noetig. Playwright
# baut das Frontend und startet `vite preview --strictPort` auf E2E_PORT
# (Standard 4181); belegter Port -> Abbruch statt Ausweichen. Beide Projekte
# (Desktop-Chromium + Pixel 5), damit mobile-only-Regressionen auffallen.
if (-not $SkipE2E) {
    Run-Step "Playwright E2E" {
        Push-Location e2e
        $savedBaseUrl = $env:BASE_URL
        try {
            if (-not (Test-Path node_modules)) {
                npm ci
                if ($LASTEXITCODE -ne 0) { throw "e2e npm ci exit $LASTEXITCODE" }
                npx playwright install --with-deps chromium
            }
            npm run typecheck
            if ($LASTEXITCODE -ne 0) { throw "e2e typecheck exit $LASTEXITCODE" }
            # Ein gesetztes BASE_URL wuerde den eigenen Preview-Server umgehen.
            Remove-Item Env:BASE_URL -ErrorAction SilentlyContinue
            npx playwright test --reporter=list
            if ($LASTEXITCODE -ne 0) { throw "playwright exit $LASTEXITCODE" }
        } finally {
            if ($null -ne $savedBaseUrl) { $env:BASE_URL = $savedBaseUrl }
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
if ($failed.Count -eq 0) {
    Write-Host "  ALLE CHECKS GRUEN" -ForegroundColor Green
    exit 0
} else {
    Write-Host "  FEHLGESCHLAGEN: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
