# Convert-Env-To-Config.ps1 - Migriert eine Docker-Setup .env nach config.toml.
#
# Wird manuell beim Umstieg Docker -> Native aufgerufen (siehe CLAUDE.md,
# "Migration Docker -> Native"); das Setup ruft es NICHT selbst auf. Liegt nach
# der Installation im Programmordner, zusammen mit New-NativeConfig.ps1, das die
# Datei tatsaechlich schreibt (UTF-8 ohne BOM, TOML-escaped).
#
# Aufruf:
#   .\Convert-Env-To-Config.ps1 -EnvPath "C:\altes-zw\.env" -OutPath "C:\Users\.\Documents\Zettelwirtschaft\config.toml" -DataDir "C:\Users\.\Documents\Zettelwirtschaft\data"
#
# -DataDir ist der Ordner, der zettelwirtschaft.db, archive\ usw. enthaelt (also
# <Datenordner>\data). -FrontendDistDir ist optional; Default ist der
# frontend-Ordner neben diesem Skript (= Programmordner der Native-Installation).

param(
    [Parameter(Mandatory)] [string]$EnvPath,
    [Parameter(Mandatory)] [string]$OutPath,
    [Parameter(Mandatory)] [string]$DataDir,
    [string]$FrontendDistDir = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $EnvPath)) {
    throw ".env nicht gefunden: $EnvPath"
}

# .env parsen - pro Zeile KEY=VALUE
$env_map = @{}
Get-Content $EnvPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    $env_map[$key] = $val
}

function Get-Or {
    param([string]$key, $default)
    if ($env_map.ContainsKey($key) -and $env_map[$key] -ne "") { return $env_map[$key] }
    return $default
}

$port = Get-Or "FRONTEND_PORT" "8080"
$pinEnabled = (Get-Or "PIN_ENABLED" "false").ToLower() -eq "true"
$pinCode = Get-Or "PIN_CODE" ""

# Ohne EMAIL_ENCRYPTION_KEY in der .env erzeugt New-NativeConfig.ps1 einen neuen.
# Bestehende IMAP-Konten muessen dann neu angelegt werden (ihre Passwoerter
# waren mit dem alten Key verschluesselt, sind also nicht migrierbar).
$emailKey = Get-Or "EMAIL_ENCRYPTION_KEY" ""

if ($pinEnabled -and $pinCode.Trim().Length -lt 4) {
    throw "PIN_ENABLED=true, aber PIN_CODE in $EnvPath ist leer oder kuerzer als 4 Zeichen. Bitte PIN_CODE setzen oder PIN_ENABLED=false."
}

if (-not $FrontendDistDir) {
    $candidate = Join-Path $PSScriptRoot "frontend"
    if (Test-Path -LiteralPath (Join-Path $candidate "index.html")) { $FrontendDistDir = $candidate }
}
if (-not $FrontendDistDir) {
    Write-Warning "Kein frontend-Ordner gefunden - FRONTEND_DIST_DIR bleibt leer (die Weboberflaeche wird dann nicht ausgeliefert). Mit -FrontendDistDir angeben."
}

# New-NativeConfig erwartet den Datenordner (Eltern von data\).
$dataRoot = Split-Path -Parent ($DataDir.TrimEnd('\', '/'))

$cfgArgs = @{
    OutPath         = $OutPath
    DataDir         = $dataRoot
    FrontendDistDir = $FrontendDistDir
    Port            = [int]$port
    PinCode         = $pinCode
    OllamaModel     = (Get-Or "OLLAMA_MODEL" "qwen2.5:7b-instruct")
    EmbeddingModel  = (Get-Or "EMBEDDING_MODEL" "bge-m3")
    EmailKey        = $emailKey
    HeaderComment   = "Migriert aus $EnvPath"
}
if (-not $pinEnabled) { $cfgArgs.DisablePin = $true }

& (Join-Path $PSScriptRoot "New-NativeConfig.ps1") @cfgArgs
if ($LASTEXITCODE -ne 0) { throw "config.toml konnte nicht geschrieben werden (siehe Meldung oben)." }
