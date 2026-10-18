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
. (Join-Path $PSScriptRoot "model-tiers.ps1")

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
    # Fehlt ein Modell in der alten .env, lief die Docker-Installation mit dem
    # damaligen Default (bis v1.5) -- der bleibt, sonst wuerde die Migration
    # still das Analysemodell wechseln bzw. den ganzen Vektor-Index neu bauen.
    # Bewusst NICHT die neuen Defaults/Hardware-Stufen aus model-tiers.ps1.
    OllamaModel     = (Get-Or "OLLAMA_MODEL" $ZwLegacyLlmModel)
    EmbeddingModel  = (Get-Or "EMBEDDING_MODEL" $ZwLegacyEmbeddingModel)
    EmailKey        = $emailKey
    HeaderComment   = "Migriert aus $EnvPath"
}
if (-not $pinEnabled) { $cfgArgs.DisablePin = $true }

& (Join-Path $PSScriptRoot "New-NativeConfig.ps1") @cfgArgs
if ($LASTEXITCODE -ne 0) { throw "config.toml konnte nicht geschrieben werden (siehe Meldung oben)." }

# Aufteilen von Stapel-Scans (v1.7): in der .env gesetzte SPLIT_*-Werte
# uebernehmen. Nur gepruefte Zahl-/Bool-Werte (TOML ohne Quoting); ungueltige
# Werte werden mit Warnung uebersprungen - das Backend nutzt dann den Default.
$splitKeys = [ordered]@{
    SPLIT_AUTO_ENABLED     = 'bool'
    SPLIT_MIN_PAGES        = 'int'
    SPLIT_USE_LLM          = 'bool'
    SPLIT_MAX_PAGES        = 'int'
    SPLIT_TIME_BUDGET_S    = 'int'
    SPLIT_BLANK_INK        = 'float'
    SPLIT_MAX_PARTS        = 'int'
    SPLIT_KEEP_SOURCE_DAYS = 'int'
    SPLIT_THUMBNAIL_WIDTH  = 'int'
}
$splitLines = @()
foreach ($k in $splitKeys.Keys) {
    $v = [string](Get-Or $k "")
    if ($v -eq "") { continue }
    $kind = $splitKeys[$k]
    if ($kind -eq 'bool') {
        $valid = @('true', 'false') -contains $v.ToLower()
        $v = $v.ToLower()
    } elseif ($kind -eq 'int') {
        $valid = $v -match '^\d{1,9}$'
    } else {
        $valid = $v -match '^\d+(\.\d+)?$'
    }
    if (-not $valid) {
        Write-Warning "$k=$v in $EnvPath ist ungueltig - nicht uebernommen (Default gilt)."
        continue
    }
    $splitLines += "$k = $v"
}
if ($splitLines.Count -gt 0) {
    $outFull = (Resolve-Path -LiteralPath $OutPath).ProviderPath
    $text = "`r`n# Aufteilen von Stapel-Scans (aus $([System.IO.Path]::GetFileName($EnvPath)))`r`n" + ($splitLines -join "`r`n") + "`r`n"
    [System.IO.File]::AppendAllText($outFull, $text, (New-Object System.Text.UTF8Encoding($false)))
}
