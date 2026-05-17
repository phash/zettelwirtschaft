# build-native.ps1 - Baut das Native-Windows-Bundle.
#
# Vorgehen:
#   1. Backend: PyInstaller -> dist/backend/zettelwirtschaft-backend.exe
#   2. Frontend: vite build -> dist/frontend/
#   3. Binaries kopieren: Tesseract + poppler aus tools/ -> dist/bin/
#   4. NSSM + Skripte -> dist/
#   5. NSIS-Installer bauen (optional)
#
# Voraussetzungen am Build-Host:
#   - Python 3.12 + pip install -r backend/requirements.txt -r backend/requirements-build.txt
#   - Node 22 + npm
#   - Tesseract-OCR-w64-setup-5.x portable in tools/tesseract/  (oder via Downloader)
#   - poppler-windows in tools/poppler/
#   - NSSM in tools/nssm/win64/nssm.exe
#   - (Optional) NSIS 3.x mit makensis.exe im PATH

param(
    [switch]$SkipFrontend,
    [switch]$SkipInstaller,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$DistRoot = Join-Path $RepoRoot "dist\native"

if (-not $Version) {
    $Version = (Get-Content (Join-Path $RepoRoot "VERSION")).Trim()
}
Write-Host "==> Build Zettelwirtschaft Native $Version" -ForegroundColor Cyan

# Saubere dist
if (Test-Path $DistRoot) {
    Remove-Item -Recurse -Force $DistRoot
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "bin") | Out-Null

# --- 1. Backend (PyInstaller) ---
Write-Host "==> [1/5] PyInstaller Backend-Bundle" -ForegroundColor Yellow
Push-Location (Join-Path $RepoRoot "backend")
try {
    pyinstaller zettelwirtschaft.spec --clean --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    Copy-Item -Recurse -Force "dist\zettelwirtschaft-backend" (Join-Path $DistRoot "backend")
} finally {
    Pop-Location
}

# --- 2. Frontend (Vite-Build) ---
if (-not $SkipFrontend) {
    Write-Host "==> [2/5] Frontend Vite-Build" -ForegroundColor Yellow
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        npm ci
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "vite build failed" }
        Copy-Item -Recurse -Force "dist" (Join-Path $DistRoot "frontend")
    } finally {
        Pop-Location
    }
}

# --- 3. Bundled Binaries (Tesseract + poppler) ---
Write-Host "==> [3/5] Bundled Binaries kopieren" -ForegroundColor Yellow
$ToolsDir = Join-Path $RepoRoot "tools"

$tesseractSrc = Join-Path $ToolsDir "tesseract"
if (Test-Path $tesseractSrc) {
    Copy-Item -Recurse -Force "$tesseractSrc\*" (Join-Path $DistRoot "bin")
} else {
    Write-Warning "tools/tesseract/ fehlt — OCR im Native-Build wird nicht funktionieren"
    Write-Warning "Download: https://github.com/UB-Mannheim/tesseract/wiki"
}

$popplerSrc = Join-Path $ToolsDir "poppler"
if (Test-Path $popplerSrc) {
    Copy-Item -Recurse -Force $popplerSrc (Join-Path $DistRoot "bin\poppler")
} else {
    Write-Warning "tools/poppler/ fehlt — PDF-Konvertierung im Native-Build deaktiviert"
    Write-Warning "Download: https://github.com/oschwartz10612/poppler-windows/releases"
}

# --- 4. NSSM + Service-Skripte ---
Write-Host "==> [4/5] NSSM + Service-Skripte" -ForegroundColor Yellow
$nssmSrc = Join-Path $ToolsDir "nssm\win64\nssm.exe"
if (Test-Path $nssmSrc) {
    Copy-Item -Force $nssmSrc (Join-Path $DistRoot "nssm.exe")
} else {
    Write-Warning "tools/nssm/win64/nssm.exe fehlt — Service-Install wird scheitern"
    Write-Warning "Download: https://nssm.cc/release/nssm-2.24.zip"
}

Copy-Item -Force (Join-Path $RepoRoot "scripts\service-install.bat") (Join-Path $DistRoot "service-install.bat")
Copy-Item -Force (Join-Path $RepoRoot "scripts\service-uninstall.bat") (Join-Path $DistRoot "service-uninstall.bat")
Copy-Item -Force (Join-Path $RepoRoot "config.toml.example") (Join-Path $DistRoot "config.toml.example")
Copy-Item -Force (Join-Path $RepoRoot "VERSION") (Join-Path $DistRoot "VERSION")

# --- 5. NSIS-Installer ---
if (-not $SkipInstaller) {
    Write-Host "==> [5/5] NSIS-Installer" -ForegroundColor Yellow
    $makensis = Get-Command makensis -ErrorAction SilentlyContinue
    if (-not $makensis) {
        Write-Warning "makensis nicht im PATH — Installer-Schritt uebersprungen"
        Write-Warning "Download: https://nsis.sourceforge.io/Download"
    } else {
        Push-Location $RepoRoot
        try {
            & makensis "/DVERSION=$Version" "/DDIST=$DistRoot" "setup-native.nsi"
            if ($LASTEXITCODE -ne 0) { throw "NSIS build failed" }
        } finally {
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "==> Fertig: $DistRoot" -ForegroundColor Green
Write-Host "    Backend:   $DistRoot\backend\zettelwirtschaft-backend.exe" -ForegroundColor Gray
Write-Host "    Frontend:  $DistRoot\frontend\" -ForegroundColor Gray
Write-Host "    Setup-EXE: $RepoRoot\Zettelwirtschaft-$Version-Setup.exe (falls makensis lief)" -ForegroundColor Gray
