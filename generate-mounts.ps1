# generate-mounts.ps1 - Bereitet die Docker-Compose-Konfiguration vor dem Start vor.
#
# Wird von start.bat und update.bat VOR jedem "docker compose up" aufgerufen
# (immer, nicht nur wenn data\.host-mounts.json existiert - sonst bleiben alte
# Mounts nach dem Leeren der Ordner-Einstellungen stehen).
#
# 1. docker-compose.override.yml
#    - Volume-Mounts fuer externe Windows-Ordner aus data\.host-mounts.json
#      (vom Backend geschrieben, Einstellungen -> Ordner). Lange Bind-Syntax mit
#      YAML-Single-Quotes: Windows-Pfade wie V:\Scans\eingang enthalten
#      Backslashes, die in doppelt gequoteten YAML-Strings Escape-Sequenzen
#      waeren (-> "found unknown escape character", kein compose-Befehl laeuft
#      mehr).
#    - Ein vorhandener "ollama:"-Block (GPU-Konfiguration der Installer) bleibt
#      erhalten.
#    - Ohne Mounts und ohne GPU-Block wird die Datei entfernt.
# 2. HTTPS-Overlay: liegen ssl\cert.pem + ssl\key.pem vor, wird COMPOSE_FILE
#    in .env gesetzt (docker-compose.yml + docker-compose.ssl.yml + ggf.
#    Override). So nutzt JEDER compose-Aufruf (start/stop/update, auch manuell)
#    dieselbe Konfiguration und HTTPS faellt nicht still auf HTTP zurueck.
#    Ohne Zertifikat werden COMPOSE_FILE/COMPOSE_PATH_SEPARATOR entfernt.
# 3. Pruefung mit "docker compose config -q". Ist das Ergebnis ungueltig, wird
#    ohne Host-Ordner erneut erzeugt (Exit 2 = Start moeglich, Ordner fehlen);
#    ist auch das ungueltig, wird der vorherige Stand wiederhergestellt (Exit 1).
# Exit 0 = alles uebernommen.

param(
    [string]$ProjectDir = $PSScriptRoot,
    [switch]$SkipValidation
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$mountsFile   = Join-Path $ProjectDir 'data\.host-mounts.json'
$overrideFile = Join-Path $ProjectDir 'docker-compose.override.yml'
$envFile      = Join-Path $ProjectDir '.env'
$sslCompose   = Join-Path $ProjectDir 'docker-compose.ssl.yml'

function ConvertTo-YamlSingleQuoted([string]$value) {
    return "'" + $value.Replace("'", "''") + "'"
}

function Read-TextOrNull([string]$path) {
    if (Test-Path -LiteralPath $path) { return [System.IO.File]::ReadAllText($path) }
    return $null
}

function Write-TextOrDelete([string]$path, $text) {
    if ($null -eq $text) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    } else {
        [System.IO.File]::WriteAllText($path, $text, $Utf8NoBom)
    }
}

function Remove-TrailingBlank([string[]]$lines) {
    $n = $lines.Count
    while ($n -gt 0 -and -not $lines[$n - 1].Trim()) { $n-- }
    if ($n -eq 0) { return @() }
    return $lines[0..($n - 1)]
}

# Liefert den "  ollama:"-Block (bis zum naechsten Service bzw. Top-Level-Key).
function Get-OllamaBlock([string]$yaml) {
    if (-not $yaml) { return @() }
    $block = @()
    $inBlock = $false
    foreach ($line in ($yaml -split "`r?`n")) {
        if ($line -match '^  ollama:\s*$') { $inBlock = $true; $block += $line; continue }
        if ($inBlock) {
            if ($line -match '^\S' -or $line -match '^  \S') { break }
            $block += $line
        }
    }
    return Remove-TrailingBlank $block
}

# --- 1. Override zusammenbauen ---
$oldOverride = Read-TextOrNull $overrideFile
$oldEnv = Read-TextOrNull $envFile

$mounts = $null
if (Test-Path -LiteralPath $mountsFile) {
    $mounts = [System.IO.File]::ReadAllText($mountsFile) | ConvertFrom-Json
}

$volumes = @()
foreach ($m in @(@{ Key = 'watch'; Target = '/app/external/watch' }, @{ Key = 'export'; Target = '/app/external/export' })) {
    $src = if ($mounts) { [string]$mounts.($m.Key) } else { '' }
    if ($src.Trim()) {
        $volumes += "      - type: bind"
        $volumes += "        source: $(ConvertTo-YamlSingleQuoted $src.Trim())"
        $volumes += "        target: $($m.Target)"
        # Wie die Kurzsyntax: fehlenden Host-Ordner anlegen statt Startfehler.
        $volumes += "        bind:"
        $volumes += "          create_host_path: true"
    }
}

$ollamaBlock = @(Get-OllamaBlock $oldOverride)
$httpsOn = (Test-Path -LiteralPath $sslCompose) -and
           (Test-Path -LiteralPath (Join-Path $ProjectDir 'ssl\cert.pem')) -and
           (Test-Path -LiteralPath (Join-Path $ProjectDir 'ssl\key.pem'))

# Schreibt Override + .env fuer die gegebenen Volume-Zeilen.
# Rueckgabe: $true, wenn ein Override geschrieben wurde.
function Write-ComposeConfig([string[]]$volumeLines) {
    $override = $null
    if ($volumeLines.Count -gt 0 -or $ollamaBlock.Count -gt 0) {
        $lines = @(
            '# Automatisch erzeugt von generate-mounts.ps1 (bei jedem Start neu).',
            '# Host-Ordner in der Weboberflaeche aendern (Einstellungen -> Ordner).',
            'services:'
        )
        if ($volumeLines.Count -gt 0) {
            $lines += '  backend:'
            $lines += '    volumes:'
            $lines += $volumeLines
        }
        $lines += $ollamaBlock
        $override = ($lines -join "`n") + "`n"
    }
    Write-TextOrDelete $overrideFile $override

    if ($null -ne $oldEnv) {
        $nl = if ($oldEnv -match "`r`n") { "`r`n" } else { "`n" }
        $envLines = @(Remove-TrailingBlank @($oldEnv -split "`r?`n" | Where-Object { $_ -notmatch '^\s*(COMPOSE_FILE|COMPOSE_PATH_SEPARATOR)\s*=' }))
        if ($httpsOn) {
            $files = @('docker-compose.yml', 'docker-compose.ssl.yml')
            if ($null -ne $override) { $files += 'docker-compose.override.yml' }
            $envLines += 'COMPOSE_PATH_SEPARATOR=;'
            $envLines += "COMPOSE_FILE=$($files -join ';')"
        }
        $newEnv = ($envLines -join $nl) + $nl
        if ($newEnv -ne $oldEnv) { [System.IO.File]::WriteAllText($envFile, $newEnv, $Utf8NoBom) }
    } elseif ($httpsOn) {
        Write-Warning '.env fehlt - HTTPS-Overlay kann nicht dauerhaft aktiviert werden.'
    }
    return ($null -ne $override)
}

# Rueckgabe: $null = gueltig, sonst die Fehlermeldung von docker compose.
function Test-ComposeConfig {
    if ($SkipValidation) { return $null }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warning 'docker nicht gefunden - Compose-Konfiguration nicht geprueft.'
        return $null
    }
    Push-Location $ProjectDir
    try {
        $out = & docker compose config -q 2>&1
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rc -eq 0) { return $null }
    return (@($out) -join "`n")
}

$null = Write-ComposeConfig $volumes
$err = Test-ComposeConfig
if ($null -eq $err) {
    if ($volumes.Count -gt 0) { Write-Host '  Host-Ordner konfiguriert.' }
    if ($httpsOn) { Write-Host '  HTTPS aktiv (ssl\cert.pem gefunden).' }
    exit 0
}

Write-Host '  [FEHLER] Die Konfiguration mit Host-Ordnern ist ungueltig:' -ForegroundColor Red
foreach ($line in ($err -split "`n")) { Write-Host "    $line" }

# Ein fehlerhafter Host-Ordner darf den Start nicht blockieren: ohne Host-Ordner
# erneut versuchen (GPU/HTTPS bleiben).
if ($volumes.Count -gt 0) {
    $null = Write-ComposeConfig @()
    if ($null -eq (Test-ComposeConfig)) {
        Write-Host '  Host-Ordner wurden NICHT eingebunden - bitte in den Einstellungen pruefen.' -ForegroundColor Yellow
        exit 2
    }
}

# Auch ohne Host-Ordner ungueltig: vorherigen Stand wiederherstellen.
Write-TextOrDelete $overrideFile $oldOverride
if ($null -ne $oldEnv) { [System.IO.File]::WriteAllText($envFile, $oldEnv, $Utf8NoBom) }
Write-Host '  Vorherige Konfiguration wiederhergestellt.' -ForegroundColor Red
exit 1
