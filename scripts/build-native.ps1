# build-native.ps1 - Baut das Native-Windows-Bundle.
#
# Vorgehen (Ausgabe unter dist\native\):
#   1. Backend: PyInstaller -> dist\native\backend\zettelwirtschaft-backend.exe
#   2. Frontend: vite build -> dist\native\frontend\
#   3. Binaries kopieren: Tesseract + poppler aus tools\ -> dist\native\bin\
#   4. NSSM + Skripte -> dist\native\
#   5. Update-ZIP: Zettelwirtschaft-<version>-native-update.zip (Inhalt von
#      dist\native, fuer update-wizard.bat)
#   6. NSIS-Installer: Zettelwirtschaft-<version>-Native-Setup.exe (optional)
#
# Voraussetzungen am Build-Host:
#   - Python 3.13 (wie Docker-Image python:3.13-slim) + pip install -r backend/requirements.txt -r backend/requirements-build.txt
#     Das Skript nutzt das "python" im PATH fuer pip UND PyInstaller (python -m),
#     also vorher das Build-venv aktivieren (z.B. backend\.venv\Scripts\Activate.ps1).
#   - Node 22 + npm
#   - Tesseract-OCR-w64-setup-5.x portable in tools/tesseract/  (oder via Downloader)
#   - poppler-windows in tools/poppler/
#   - NSSM in tools/nssm/win64/nssm.exe
#   - (Optional) NSIS 3.x mit makensis.exe im PATH

param(
    [switch]$SkipFrontend,
    [switch]$SkipInstaller,
    [switch]$SkipUpdateZip,
    # INFRA-04: tools/ (Tesseract, poppler, NSSM) muss den gepinnten SHA-256
    # aus fetch-build-tools.ps1 entsprechen - die Binaries laufen beim Kunden
    # als LocalSystem. Nur fuer lokale Experimente mit anderen Versionen.
    [switch]$AllowUnpinnedTools,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$DistRoot = Join-Path $RepoRoot "dist\native"

# Native Kommandos werfen trotz ErrorActionPreference=Stop nicht -> Exit-Code
# nach jedem Aufruf pruefen, sonst baut z.B. PyInstaller nach einem
# gescheiterten pip install mit falschen Paketen weiter.
function Assert-ExitCode([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what fehlgeschlagen (Exit $LASTEXITCODE)" }
}

if (-not $Version) {
    $Version = (Get-Content (Join-Path $RepoRoot "VERSION")).Trim()
}
Write-Host "==> Build Zettelwirtschaft Native $Version" -ForegroundColor Cyan
$python = (Get-Command python -ErrorAction Stop).Source
Write-Host "    Python: $python" -ForegroundColor DarkGray

# Saubere dist
if (Test-Path $DistRoot) {
    Remove-Item -Recurse -Force $DistRoot
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "bin") | Out-Null

# --- 1. Backend (PyInstaller) ---
Write-Host "==> [1/6] PyInstaller Backend-Bundle" -ForegroundColor Yellow
Push-Location (Join-Path $RepoRoot "backend")
try {
    # Native braucht chromadb (full) statt chromadb-client (HTTP-only).
    # Beide installiert -> Namespace-Konflikt im Bundle, PersistentClient
    # crasht zur Laufzeit mit "http-only client mode".
    Write-Host "  Sicherstellen: chromadb-client deinstalliert" -ForegroundColor DarkGray
    # Beide Distributionen schreiben in denselben Ordner "chromadb". War
    # chromadb-client installiert, loescht sein Uninstall die gemeinsamen
    # Dateien - pip haelt chromadb trotzdem fuer installiert ("already
    # satisfied") und das Bundle bekaeme kein embedded Chroma. Deshalb dann
    # chromadb erzwungen neu installieren.
    python -m pip show chromadb-client 2>&1 | Out-Null
    $hadClient = ($LASTEXITCODE -eq 0)
    python -m pip uninstall -y chromadb-client 2>&1 | Out-Null
    python -m pip install -r requirements-build.txt 2>&1 | Select-Object -Last 3
    Assert-ExitCode "pip install -r requirements-build.txt"
    if ($hadClient) {
        Write-Host "  chromadb nach Entfernen von chromadb-client neu installieren" -ForegroundColor DarkGray
        $chromaReq = (Select-String -Path requirements-build.txt -Pattern '^\s*chromadb[<>=!~ ]').Line.Trim()
        python -m pip install --force-reinstall --no-deps $chromaReq 2>&1 | Select-Object -Last 1
        Assert-ExitCode "pip install --force-reinstall chromadb"
    }
    # Vor dem Bundling pruefen statt erst zur Laufzeit: PersistentClient muss
    # importierbar sein (sonst "http-only client mode" im Dienst).
    python -c "import tempfile, chromadb; d = tempfile.mkdtemp(); c = chromadb.PersistentClient(path=d); c.get_or_create_collection('build_check'); print('chromadb', chromadb.__version__, 'embedded ok')"
    Assert-ExitCode "chromadb (embedded) PersistentClient"

    # python -m statt "pyinstaller" aus dem PATH: garantiert derselbe
    # Interpreter (und dieselben Pakete) wie beim pip install oben.
    python -m PyInstaller zettelwirtschaft.spec --clean --noconfirm
    Assert-ExitCode "PyInstaller"
    Copy-Item -Recurse -Force "dist\zettelwirtschaft-backend" (Join-Path $DistRoot "backend")
    # Aufteilen von Stapel-Scans braucht pdfium.dll (pypdfium2_raw) im Bundle -
    # fehlt sie, scheitert jede Aufteilung erst zur Laufzeit.
    $pdfiumDll = Get-ChildItem -Path (Join-Path $DistRoot "backend") -Recurse -Filter "pdfium.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pdfiumDll) { throw "pdfium.dll fehlt im Backend-Bundle (pypdfium2_raw) - Build abgebrochen" }
    Write-Host "  pdfium.dll: $($pdfiumDll.FullName)" -ForegroundColor DarkGray
    # pypdfium2 liest beim Import seine version.json (Hooks aus
    # pyinstaller-hooks-contrib) - fehlt sie, scheitert schon der Import.
    foreach ($pkg in @("pypdfium2", "pypdfium2_raw")) {
        $versionJson = Get-ChildItem -Path (Join-Path $DistRoot "backend") -Recurse -Filter "version.json" -ErrorAction SilentlyContinue |
            Where-Object { $_.Directory.Name -eq $pkg } | Select-Object -First 1
        if (-not $versionJson) { throw "$pkg\version.json fehlt im Backend-Bundle - Build abgebrochen" }
    }
    # Laufzeit-Check am fertigen Bundle: DLL laden und im Speicher schneiden.
    $backendExe = Join-Path $DistRoot "backend\zettelwirtschaft-backend.exe"
    & $backendExe --self-test-pdfium
    Assert-ExitCode "pdfium-Selbsttest (zettelwirtschaft-backend.exe --self-test-pdfium)"
} finally {
    Pop-Location
}

# --- 2. Frontend (Vite-Build) ---
if (-not $SkipFrontend) {
    Write-Host "==> [2/6] Frontend Vite-Build" -ForegroundColor Yellow
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        npm ci
        Assert-ExitCode "npm ci"
        npm run build
        Assert-ExitCode "vite build"
        Copy-Item -Recurse -Force "dist" (Join-Path $DistRoot "frontend")
    } finally {
        Pop-Location
    }
}

# --- 3. Bundled Binaries (Tesseract + poppler) ---
Write-Host "==> [3/6] Bundled Binaries kopieren" -ForegroundColor Yellow
$ToolsDir = Join-Path $RepoRoot "tools"
$present = @(@{ C = 'tesseract'; P = 'tesseract' }, @{ C = 'poppler'; P = 'poppler' }, @{ C = 'nssm'; P = 'nssm\win64\nssm.exe' }) |
    Where-Object { Test-Path (Join-Path $ToolsDir $_.P) } | ForEach-Object { $_.C }
if ($present) {
    & (Join-Path $PSScriptRoot "fetch-build-tools.ps1") -VerifyOnly -Components $present
    if ($LASTEXITCODE -ne 0) {
        if (-not $AllowUnpinnedTools) { throw "tools/ entspricht nicht den gepinnten SHA-256 (fetch-build-tools.ps1) - Build abgebrochen (-AllowUnpinnedTools nur fuer lokale Tests)" }
        Write-Warning "tools/ weicht von den Pins ab - NICHT ausliefern (-AllowUnpinnedTools)"
    }
}

$tesseractSrc = Join-Path $ToolsDir "tesseract"
if (Test-Path $tesseractSrc) {
    Copy-Item -Recurse -Force "$tesseractSrc\*" (Join-Path $DistRoot "bin")
} else {
    Write-Warning "tools/tesseract/ fehlt - OCR im Native-Build wird nicht funktionieren"
    Write-Warning "Download: https://github.com/UB-Mannheim/tesseract/wiki"
}

$popplerSrc = Join-Path $ToolsDir "poppler"
if (Test-Path $popplerSrc) {
    Copy-Item -Recurse -Force $popplerSrc (Join-Path $DistRoot "bin\poppler")
} else {
    Write-Warning "tools/poppler/ fehlt - PDF-Konvertierung im Native-Build deaktiviert"
    Write-Warning "Download: https://github.com/oschwartz10612/poppler-windows/releases"
}

# --- 4. NSSM + Service-Skripte ---
Write-Host "==> [4/6] NSSM + Service-Skripte" -ForegroundColor Yellow
$nssmSrc = Join-Path $ToolsDir "nssm\win64\nssm.exe"
if (Test-Path $nssmSrc) {
    Copy-Item -Force $nssmSrc (Join-Path $DistRoot "nssm.exe")
} else {
    Write-Warning "tools/nssm/win64/nssm.exe fehlt - Service-Install wird scheitern"
    Write-Warning "Download: https://nssm.cc/release/nssm-2.24.zip"
}

Copy-Item -Force (Join-Path $RepoRoot "config.toml.example") (Join-Path $DistRoot "config.toml.example")
Copy-Item -Force (Join-Path $RepoRoot "VERSION") (Join-Path $DistRoot "VERSION")

# Service-/Update-/Backup-/Restore-/Konfig-Skripte ins Bundle (landen in
# Setup.exe UND im Update-ZIP; setup-native.nsi nimmt sie aus dist\native).
$bundleScripts = @(
    "service-install.bat", "service-uninstall.bat",
    "update-wizard.bat", "update-wizard.ps1",
    "backup.bat", "restore-backup.bat", "restore-backup.ps1",
    "Convert-Env-To-Config.ps1", "New-NativeConfig.ps1", "new-secret.ps1",
    # Von New-NativeConfig/Convert-Env dot-gesourct (Modell-Stufen, Mindestversion).
    "model-tiers.ps1",
    # Zugriffsrechte Datenordner/config.toml (SEC-DATA-03): New-NativeConfig,
    # setup-native.nsi und update-wizard.ps1.
    "secure-acl.ps1",
    # Zugriffsrechte Programmordner (INFRA-03): setup-native.nsi + update-wizard.ps1.
    "secure-install-dir.ps1",
    # Uebernahme manuell eingerichteter Installationen (setup-native.nsi, ab 1.6.1).
    "legacy-install.ps1",
    # Scanner-Freigabe des Eingangsordners (setup-native.nsi + Startmenue, ab 1.7).
    "freigabe-einrichten.bat", "freigabe-einrichten.ps1"
)
foreach ($s in $bundleScripts) {
    Copy-Item -Force (Join-Path $RepoRoot "scripts\$s") (Join-Path $DistRoot $s)
}

# --- 5. Update-ZIP ---
$UpdateZip = Join-Path $RepoRoot "Zettelwirtschaft-$Version-native-update.zip"
if (-not $SkipUpdateZip) {
    Write-Host "==> [5/6] Update-ZIP" -ForegroundColor Yellow
    if (Test-Path $UpdateZip) { Remove-Item -Force $UpdateZip }
    Compress-Archive -Path "$DistRoot\*" -DestinationPath $UpdateZip -CompressionLevel Optimal
}

# --- 6. NSIS-Installer ---
$SetupExe = Join-Path $RepoRoot "Zettelwirtschaft-$Version-Native-Setup.exe"
$builtSetup = $false
if (-not $SkipInstaller) {
    Write-Host "==> [6/6] NSIS-Installer" -ForegroundColor Yellow
    $makensis = Get-Command makensis -ErrorAction SilentlyContinue
    if (-not $makensis) {
        Write-Warning "makensis nicht im PATH - Installer-Schritt uebersprungen"
        Write-Warning "Download: https://nsis.sourceforge.io/Download"
    } else {
        Push-Location $RepoRoot
        try {
            # /WX: Warnungen sind Fehler (z.B. unbekannte $variablen in Strings).
            & makensis /WX "/DVERSION=$Version" "/DDIST=$DistRoot" "setup-native.nsi"
            Assert-ExitCode "NSIS build"
            $builtSetup = $true
        } finally {
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "==> Fertig: $DistRoot" -ForegroundColor Green
Write-Host "    Backend:    $DistRoot\backend\zettelwirtschaft-backend.exe" -ForegroundColor Gray
Write-Host "    Frontend:   $DistRoot\frontend\" -ForegroundColor Gray
if (-not $SkipUpdateZip) { Write-Host "    Update-ZIP: $UpdateZip" -ForegroundColor Gray }
if ($builtSetup) { Write-Host "    Setup-EXE:  $SetupExe" -ForegroundColor Gray }
