# Convert-Env-To-Config.ps1 - Migriert eine Docker-Setup .env nach config.toml.
#
# Vom Setup-Wizard aufgerufen wenn eine Bestandsinstallation erkannt wurde.
#
# Aufruf:
#   .\Convert-Env-To-Config.ps1 -EnvPath "C:\altes-zw\.env" -OutPath "C:\Users\.\Documents\Zettelwirtschaft\config.toml" -DataDir "C:\Users\.\Documents\Zettelwirtschaft\data"

param(
    [Parameter(Mandatory)] [string]$EnvPath,
    [Parameter(Mandatory)] [string]$OutPath,
    [Parameter(Mandatory)] [string]$DataDir
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $EnvPath)) {
    throw ".env nicht gefunden: $EnvPath"
}

# .env parsen — pro Zeile KEY=VALUE
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

# Pfade absolutieren (Forward-Slashes fuer TOML, Windows toleriert das)
$dataPath = $DataDir.Replace('\', '/')

$port = Get-Or "FRONTEND_PORT" "8080"
$pinEnabled = (Get-Or "PIN_ENABLED" "false").ToLower()
$pinCode = Get-Or "PIN_CODE" ""
$ollamaModel = Get-Or "OLLAMA_MODEL" "qwen2.5:7b-instruct"
$embeddingModel = Get-Or "EMBEDDING_MODEL" "bge-m3"
$emailKey = Get-Or "EMAIL_ENCRYPTION_KEY" ""

# Wenn kein EMAIL_ENCRYPTION_KEY in der .env war, lassen wir es leer — User
# muss IMAP-Konten neu anlegen (Passwoerter waren mit dem alten Key
# verschluesselt, sind also eh nicht migrierbar).

$toml = @"
# config.toml - Migriert aus .env am $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
# Original: $EnvPath

SERVER_HOST = "0.0.0.0"
SERVER_PORT = $port

DATABASE_URL = "sqlite+aiosqlite:///$dataPath/zettelwirtschaft.db"
UPLOAD_DIR = "$dataPath/uploads"
ARCHIVE_DIR = "$dataPath/archive"
THUMBNAIL_DIR = "$dataPath/thumbnails"
WATCH_DIR = "$dataPath/watch"
EXPORT_DIR = ""

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "$ollamaModel"
OLLAMA_TIMEOUT = 300
OLLAMA_MAX_RETRIES = 2

OCR_LANGUAGES = "deu+eng"
LOG_LEVEL = "INFO"

PIN_ENABLED = $(if ($pinEnabled -eq "true") { "true" } else { "false" })
PIN_CODE = "$pinCode"

# Native: ChromaDB embedded statt HTTP — kein separater Service noetig.
CHROMADB_MODE = "embedded"
CHROMADB_PATH = "$dataPath/chromadb"
EMBEDDING_MODEL = "$embeddingModel"

EMAIL_ENCRYPTION_KEY = "$emailKey"
"@

# Output-Ordner sicherstellen
$outDir = Split-Path -Parent $OutPath
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

# Write
$toml | Out-File -FilePath $OutPath -Encoding utf8

Write-Host "[OK] config.toml geschrieben: $OutPath"
