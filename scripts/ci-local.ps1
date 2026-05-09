# scripts/ci-local.ps1
# Lokaler CI-Lauf — ersetzt das frühere .github/workflows/ci.yml.
#
# Verwendung:
#   pwsh scripts/ci-local.ps1            # alles
#   pwsh scripts/ci-local.ps1 -SkipE2E   # ohne Playwright
#   pwsh scripts/ci-local.ps1 -SkipDocker
param(
    [switch]$SkipE2E,
    [switch]$SkipDocker,
    [switch]$SkipFrontendBuild,
    [switch]$SkipBackendTests
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

# Frontend-Build (npm ci + build)
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
}

# Docker-Build (backend + frontend)
if (-not $SkipDocker) {
    Run-Step "Docker build backend" {
        docker build -t zettelwirtschaft-backend ./backend
        if ($LASTEXITCODE -ne 0) { throw "docker build backend exit $LASTEXITCODE" }
    }
    Run-Step "Docker build frontend" {
        docker build -t zettelwirtschaft-frontend ./frontend
        if ($LASTEXITCODE -ne 0) { throw "docker build frontend exit $LASTEXITCODE" }
    }
}

# E2E-Tests (Playwright) — braucht laufenden Stack
if (-not $SkipE2E) {
    Run-Step "Playwright E2E" {
        # Stack hochfahren falls nötig
        $running = (docker compose ps --format json 2>&1 | Out-String)
        if ($running -notmatch "frontend") {
            docker compose up -d
            if ($LASTEXITCODE -ne 0) { throw "docker compose up exit $LASTEXITCODE" }
            Start-Sleep -Seconds 10
        }
        Push-Location e2e
        try {
            if (-not (Test-Path node_modules)) {
                npm ci
                if ($LASTEXITCODE -ne 0) { throw "e2e npm ci exit $LASTEXITCODE" }
                npx playwright install --with-deps chromium
            }
            npx playwright test --project=chromium --reporter=list
            if ($LASTEXITCODE -ne 0) { throw "playwright exit $LASTEXITCODE" }
        } finally { Pop-Location }
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
