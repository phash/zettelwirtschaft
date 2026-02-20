# Zettelwirtschaft - Windows Installer
# PowerShell-Installationsassistent

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

# --- Hilfsfunktionen ---

function Write-Banner {
    Write-Host ""
    Write-Host "  ====================================================" -ForegroundColor Cyan
    Write-Host "   _____     _   _       _          _      _           " -ForegroundColor Cyan
    Write-Host "  |__  /___| |_| |_ ___| |_      _(_)_ __| |_ ___    " -ForegroundColor Cyan
    Write-Host "    / // _ \ __| __/ _ \ \ \ /\ / / | '__| __/ __|   " -ForegroundColor Cyan
    Write-Host "   / /|  __/ |_| ||  __/ |\ V  V /| | |  | |_\__ \  " -ForegroundColor Cyan
    Write-Host "  /____\___|\__|\__\___|_| \_/\_/ |_|_|   \__|___/   " -ForegroundColor Cyan
    Write-Host "                   _           __ _                    " -ForegroundColor Cyan
    Write-Host "           ___  __| |__   __ _/ _| |_                 " -ForegroundColor Cyan
    Write-Host "          / __|/ _  / _ \/ _  |  _| __|               " -ForegroundColor Cyan
    Write-Host "          \__ \ (__| | | | (_| | | | |_                " -ForegroundColor Cyan
    Write-Host "          |___/\___|_| |_|\__,_|_|  \__|              " -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  ====================================================" -ForegroundColor Cyan
    Write-Host "   Installationsassistent fuer Windows" -ForegroundColor White
    Write-Host "  ====================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step {
    param([string]$Number, [string]$Text)
    Write-Host "  [$Number] $Text" -ForegroundColor Yellow
}

function Write-OK {
    param([string]$Text)
    Write-Host "      [OK] $Text" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host "      [!] $Text" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Text)
    Write-Host "      [FEHLER] $Text" -ForegroundColor Red
}

function Write-Info {
    param([string]$Text)
    Write-Host "      $Text" -ForegroundColor Gray
}

function Test-PortAvailable {
    param([int]$Port)
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

function Wait-ForHealth {
    param([string]$Url, [int]$TimeoutSeconds = 120)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction SilentlyContinue
            if ($response.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 3
        $elapsed += 3
        Write-Host "." -NoNewline
    }
    Write-Host ""
    return $false
}

# --- Hauptprogramm ---

Write-Banner

# =============================================
# Schritt 1: Voraussetzungen pruefen
# =============================================
Write-Step "1/6" "Pruefe Voraussetzungen..."
Write-Host ""

# Docker installiert?
$dockerPath = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerPath) {
    Write-Err "Docker ist nicht installiert."
    Write-Host ""
    Write-Host "  Bitte installiere Docker Desktop von:" -ForegroundColor White
    Write-Host "  https://www.docker.com/products/docker-desktop/" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Nach der Installation: Starte dieses Skript erneut."
    Write-Host ""
    Read-Host "  Druecke Enter zum Beenden"
    exit 1
}
Write-OK "Docker ist installiert."

# Docker laeuft?
$dockerRunning = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerRunning = $true }
} catch {}

if (-not $dockerRunning) {
    Write-Warn "Docker Desktop laeuft nicht. Versuche zu starten..."

    $dockerDesktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktop) {
        Start-Process $dockerDesktop
        Write-Info "Warte auf Docker Desktop (kann bis zu 60 Sekunden dauern)..."
        $waited = 0
        while ($waited -lt 60) {
            Start-Sleep -Seconds 5
            $waited += 5
            Write-Host "." -NoNewline
            try {
                $null = docker info 2>&1
                if ($LASTEXITCODE -eq 0) { $dockerRunning = $true; break }
            } catch {}
        }
        Write-Host ""
    }

    if (-not $dockerRunning) {
        Write-Err "Docker Desktop konnte nicht gestartet werden."
        Write-Host "  Bitte starte Docker Desktop manuell und fuehre das Skript erneut aus."
        Read-Host "  Druecke Enter zum Beenden"
        exit 1
    }
}
Write-OK "Docker Desktop laeuft."

# RAM pruefen
$totalRAM = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($totalRAM -lt 8) {
    Write-Warn "Nur $totalRAM GB RAM erkannt. Empfohlen: mindestens 8 GB."
    Write-Info "Die LLM-Analyse kann langsam sein oder fehlschlagen."
} else {
    Write-OK "$totalRAM GB RAM erkannt."
}

# Festplattenspeicher pruefen
$drive = (Get-Item $ProjectDir).PSDrive
$freeGB = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB)
if ($freeGB -lt 10) {
    Write-Warn "Nur $freeGB GB frei auf Laufwerk $($drive.Name):. Empfohlen: mindestens 10 GB."
} else {
    Write-OK "$freeGB GB freier Speicherplatz."
}

# GPU pruefen
$hasNvidiaGPU = $false
try {
    $gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
    if ($gpu) {
        $hasNvidiaGPU = $true
        Write-OK "NVIDIA GPU erkannt: $($gpu.Name). GPU-Beschleunigung wird aktiviert."
    } else {
        Write-Info "Keine NVIDIA GPU erkannt. LLM laeuft auf CPU (langsamer, aber funktioniert)."
    }
} catch {
    Write-Info "GPU-Erkennung fehlgeschlagen. LLM laeuft auf CPU."
}

Write-Host ""

# =============================================
# Schritt 2: Konfiguration abfragen
# =============================================
Write-Step "2/6" "Konfiguration"
Write-Host ""

# Frontend-Port
$defaultPort = 8080
do {
    $portInput = Read-Host "  Frontend-Port (Standard: $defaultPort)"
    if ([string]::IsNullOrWhiteSpace($portInput)) { $portInput = "$defaultPort" }

    $port = 0
    if (-not [int]::TryParse($portInput, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        Write-Warn "Ungueltiger Port. Bitte eine Zahl zwischen 1 und 65535 eingeben."
        continue
    }

    if (-not (Test-PortAvailable $port)) {
        Write-Warn "Port $port ist bereits belegt. Bitte einen anderen Port waehlen."
        continue
    }

    Write-OK "Port $port ist verfuegbar."
    break
} while ($true)

# Watch-Ordner
$watchDir = ""
$enableWatch = Read-Host "  Watch-Ordner aktivieren? Dateien werden automatisch importiert (j/n, Standard: n)"
if ($enableWatch -eq "j") {
    $defaultWatch = Join-Path $ProjectDir "data\watch"
    $watchInput = Read-Host "  Watch-Ordner-Pfad (Standard: $defaultWatch)"
    if ([string]::IsNullOrWhiteSpace($watchInput)) { $watchDir = $defaultWatch } else { $watchDir = $watchInput }
    Write-OK "Watch-Ordner: $watchDir"
}

# LLM-Modell basierend auf RAM
if ($totalRAM -gt 16) {
    $defaultModel = "llama3.1"
    Write-Info "RAM > 16 GB: Empfehle llama3.1 (bessere Qualitaet)."
} else {
    $defaultModel = "llama3.2"
    Write-Info "RAM <= 16 GB: Empfehle llama3.2 (ressourcenschonend)."
}
$modelInput = Read-Host "  LLM-Modell (Standard: $defaultModel)"
if ([string]::IsNullOrWhiteSpace($modelInput)) { $model = $defaultModel } else { $model = $modelInput }
Write-OK "LLM-Modell: $model"

# PIN-Schutz
$pinEnabled = $false
$pinCode = ""
$enablePin = Read-Host "  PIN-Schutz aktivieren? Schuetzt die Weboberflaeche mit einer PIN (j/n, Standard: n)"
if ($enablePin -eq "j") {
    do {
        $pin1 = Read-Host "  PIN eingeben (mind. 4 Zeichen)" -AsSecureString
        $pin1Plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pin1))
        if ($pin1Plain.Length -lt 4) {
            Write-Warn "PIN muss mindestens 4 Zeichen lang sein."
            continue
        }
        $pin2 = Read-Host "  PIN bestaetigen" -AsSecureString
        $pin2Plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pin2))
        if ($pin1Plain -ne $pin2Plain) {
            Write-Warn "PINs stimmen nicht ueberein. Bitte erneut eingeben."
            continue
        }
        $pinEnabled = $true
        $pinCode = $pin1Plain
        Write-OK "PIN-Schutz wird aktiviert."
        break
    } while ($true)
}

Write-Host ""

# =============================================
# Schritt 3: .env generieren
# =============================================
Write-Step "3/6" "Erstelle Konfigurationsdatei..."

$envContent = @"
# Zettelwirtschaft - Konfiguration
# Generiert am $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

# Frontend-Port
FRONTEND_PORT=$port

# Ollama (lokales LLM)
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=$model

# Verzeichnisse
UPLOAD_DIR=./data/uploads
ARCHIVE_DIR=./data/archive
"@

if ($watchDir) {
    # Fuer Docker muss der Pfad relativ oder als Volume sein
    $envContent += "`nWATCH_DIR=./data/watch"
}

if ($pinEnabled) {
    $envContent += "`n`n# PIN-Schutz"
    $envContent += "`nPIN_ENABLED=true"
    $envContent += "`nPIN_CODE=$pinCode"
}

$envContent += @"

# OCR
OCR_LANGUAGES=deu+eng

# Logging
LOG_LEVEL=INFO
"@

Set-Content -Path (Join-Path $ProjectDir ".env") -Value $envContent -Encoding UTF8
Write-OK ".env erstellt."

# Watch-Ordner anlegen falls aktiviert
if ($watchDir) {
    if (-not (Test-Path $watchDir)) {
        New-Item -ItemType Directory -Path $watchDir -Force | Out-Null
        Write-OK "Watch-Ordner erstellt: $watchDir"
    }
}

# data-Verzeichnis sicherstellen
$dataDir = Join-Path $ProjectDir "data"
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}

# docker-compose.override.yml fuer GPU-Konfiguration (falls keine NVIDIA GPU)
if (-not $hasNvidiaGPU) {
    $overrideContent = @"
# Automatisch generiert: GPU-Reservierung deaktiviert (keine NVIDIA GPU erkannt)
services:
  ollama:
    deploy:
      resources:
        reservations: {}
"@
    Set-Content -Path (Join-Path $ProjectDir "docker-compose.override.yml") -Value $overrideContent -Encoding UTF8
    Write-OK "docker-compose.override.yml erstellt (GPU deaktiviert)."
}

Write-Host ""

# =============================================
# Schritt 4: Services bauen und starten
# =============================================
Write-Step "4/6" "Baue und starte Services (dies kann einige Minuten dauern)..."
Write-Host ""

docker compose up --build -d 2>&1 | ForEach-Object { Write-Info $_ }

if ($LASTEXITCODE -ne 0) {
    Write-Err "Services konnten nicht gestartet werden."
    Write-Host ""
    Write-Host "  Moegliche Ursachen:" -ForegroundColor White
    Write-Host "  - Docker Desktop hat nicht genuegend Ressourcen"
    Write-Host "  - Port-Konflikt"
    Write-Host "  - Netzwerk-Problem beim Herunterladen der Images"
    Write-Host ""
    Read-Host "  Druecke Enter zum Beenden"
    exit 1
}

Write-OK "Container gestartet."
Write-Host ""

# =============================================
# Schritt 5: Auf Backend warten + LLM laden
# =============================================
Write-Step "5/6" "Warte auf Backend-Start"

$healthUrl = "http://localhost:8000/api/health"
$healthy = Wait-ForHealth -Url $healthUrl -TimeoutSeconds 120

if ($healthy) {
    Write-Host ""
    Write-OK "Backend ist bereit."
} else {
    Write-Host ""
    Write-Warn "Backend antwortet noch nicht. Es startet moeglicherweise noch im Hintergrund."
}

Write-Host ""
Write-Info "Lade LLM-Modell '$model' herunter (kann 2-5 Minuten dauern)..."
Write-Info "Modellgroesse: ca. 2-4 GB"
Write-Host ""

docker compose exec ollama ollama pull $model 2>&1 | ForEach-Object { Write-Info $_ }

if ($LASTEXITCODE -eq 0) {
    Write-OK "LLM-Modell '$model' ist bereit."
} else {
    Write-Warn "LLM-Modell konnte nicht geladen werden. Versuche es spaeter mit:"
    Write-Info "docker compose exec ollama ollama pull $model"
}

Write-Host ""

# =============================================
# Schritt 6: Desktop-Verknuepfung + Abschluss
# =============================================
Write-Step "6/6" "Erstelle Desktop-Verknuepfung..."

try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Zettelwirtschaft.lnk")
    $shortcut.TargetPath = Join-Path $ProjectDir "start.bat"
    $shortcut.WorkingDirectory = $ProjectDir
    $shortcut.Description = "Zettelwirtschaft - Dokumentenverwaltung"
    $shortcut.IconLocation = "shell32.dll,21"
    $shortcut.Save()
    Write-OK "Desktop-Verknuepfung erstellt."
} catch {
    Write-Warn "Desktop-Verknuepfung konnte nicht erstellt werden."
}

# --- Zusammenfassung ---
Write-Host ""
Write-Host "  ====================================================" -ForegroundColor Green
Write-Host "   Installation abgeschlossen!" -ForegroundColor Green
Write-Host "  ====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Zettelwirtschaft ist erreichbar unter:" -ForegroundColor White
Write-Host "  http://localhost:$port" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Nuetzliche Befehle:" -ForegroundColor White
Write-Host "    start.bat       - System starten + Browser oeffnen" -ForegroundColor Gray
Write-Host "    stop.bat        - System stoppen" -ForegroundColor Gray
Write-Host "    update.bat      - System aktualisieren" -ForegroundColor Gray
Write-Host "    uninstall.bat   - System deinstallieren" -ForegroundColor Gray
Write-Host ""
if ($watchDir) {
    Write-Host "  Watch-Ordner: $watchDir" -ForegroundColor Gray
    Write-Host "  Dateien dort werden automatisch importiert." -ForegroundColor Gray
    Write-Host ""
}
Write-Host "  LLM-Modell: $model" -ForegroundColor Gray
Write-Host "  Ollama-Modelle verwalten:" -ForegroundColor Gray
Write-Host "    docker compose exec ollama ollama list" -ForegroundColor Gray
Write-Host ""

# Browser oeffnen
$openBrowser = Read-Host "  Browser jetzt oeffnen? (j/n, Standard: j)"
if ($openBrowser -ne "n") {
    Start-Process "http://localhost:$port"
}

Write-Host ""
Write-Host "  Viel Spass mit Zettelwirtschaft!" -ForegroundColor Cyan
Write-Host ""
