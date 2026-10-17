# New-NativeConfig.ps1 - Schreibt die config.toml einer Native-Installation.
#
# Einzige Stelle, die eine config.toml erzeugt:
#   - setup-native.nsi ruft das Skript bei der Erstinstallation auf,
#   - Convert-Env-To-Config.ps1 (Docker -> Native) uebergibt die Werte aus der
#     alten .env.
# Vorher schrieben beide die Datei unabhaengig: NSIS per FileWrite (ANSI-Bytes,
# kaputter Fernet-Key-Einzeiler), Convert-Env per Out-File (UTF-8-BOM unter
# Windows PowerShell 5.1). tomllib lehnt beides ab -> Dienst startet nicht.
#
# Garantien: UTF-8 ohne BOM, TOML-Strings korrekt escaped, Pfade mit "/",
# EMAIL_ENCRYPTION_KEY im Fernet-Format (wird erzeugt, wenn keiner uebergeben
# wird), PIN_ENABLED nie mit leerem/zu kurzem PIN (das Backend wuerde beim
# Start abbrechen). Windows PowerShell 5.1 kompatibel.
#
# Aufruf:
#   .\New-NativeConfig.ps1 -OutPath "C:\Users\Max\Documents\Zettelwirtschaft\config.toml" `
#       -DataDir "C:\Users\Max\Documents\Zettelwirtschaft" -FrontendDistDir "C:\Program Files\Zettelwirtschaft\frontend" `
#       -PinCode 123456
# Ohne -OllamaModel/-EmbeddingModel waehlt model-tiers.ps1 die Modelle passend
# zur Hardware (NVIDIA-GPU >= 10 GB VRAM -> gemma4:12b-it-qat, < 12 GB RAM ->
# qwen3-embedding:0.6b); das Native-Setup hat keine eigene Modellauswahl.
# Exit 0 = geschrieben, 1 = Fehler (Meldung auf stderr).

param(
    [Parameter(Mandatory = $true)] [string]$OutPath,
    # Datenordner der Installation; Datenbank, Archiv usw. liegen in <DataDir>\data.
    [Parameter(Mandatory = $true)] [string]$DataDir,
    [string]$FrontendDistDir = "",
    [int]$Port = 8080,
    # INFRA-07: Bind-Adresse. "0.0.0.0" = im LAN erreichbar (Smartphone),
    # "127.0.0.1" = nur dieser PC (kein PIN/Cookie/Dokument im Klartext im WLAN).
    [ValidateSet('0.0.0.0', '127.0.0.1')] [string]$ServerHost = '0.0.0.0',
    # Optional HTTPS (SERVER_SSL_CERTFILE/KEYFILE); nur zusammen angeben.
    [string]$SslCertFile = "",
    [string]$SslKeyFile = "",
    # PIN-Schutz ist Default; nur mit -DisablePin wird PIN_ENABLED = false.
    [switch]$DisablePin,
    [string]$PinCode = "",
    # Leer = nach Hardware-Stufe (model-tiers.ps1).
    [string]$OllamaModel = "",
    [string]$EmbeddingModel = "",
    # Leer = neuen Fernet-Key erzeugen.
    [string]$EmailKey = "",
    # Zusaetzliche Kommentarzeile im Kopf (z.B. Herkunft bei einer Migration).
    [string]$HeaderComment = "",
    [string]$Version = ""
)

$ErrorActionPreference = 'Stop'
$PinEnabled = -not $DisablePin
. (Join-Path $PSScriptRoot 'model-tiers.ps1')
. (Join-Path $PSScriptRoot 'secure-acl.ps1')

function ConvertTo-TomlString([string]$value) {
    # TOML Basic String: Backslash und Anfuehrungszeichen escapen,
    # Steuerzeichen sind in Werten dieser Datei nicht zulaessig.
    if ($value -match '[\x00-\x1F\x7F]') { throw "Ungueltiges Steuerzeichen in Wert: $value" }
    return '"' + $value.Replace('\', '\\').Replace('"', '\"') + '"'
}

function ConvertTo-TomlPath([string]$path) {
    return ConvertTo-TomlString ($path.Replace('\', '/').TrimEnd('/'))
}

try {
    if ($Port -lt 1 -or $Port -gt 65535) { throw "Ungueltiger Port: $Port" }
    if ([bool]$SslCertFile -ne [bool]$SslKeyFile) { throw "HTTPS: -SslCertFile und -SslKeyFile nur gemeinsam angeben." }
    if ($PinEnabled -and $PinCode.Trim().Length -lt 4) {
        throw "PIN-Schutz ist aktiv, aber der PIN ist leer oder kuerzer als 4 Zeichen."
    }
    if (-not $EmailKey) {
        # Format wird direkt danach geprueft (leer/kaputt -> Abbruch).
        $EmailKey = [string](& (Join-Path $PSScriptRoot 'new-secret.ps1') -Kind fernet)
    }
    if ($EmailKey -notmatch '^[A-Za-z0-9_-]{43}=$') {
        throw "EMAIL_ENCRYPTION_KEY hat kein gueltiges Fernet-Format (44 Zeichen URL-safe Base64)."
    }
    if (-not $OllamaModel -or -not $EmbeddingModel) {
        $tier = Get-ZwHardwareModelTier
        if (-not $OllamaModel) { $OllamaModel = $tier.LlmModel; Write-Output "[INFO] $($tier.LlmReason)" }
        if (-not $EmbeddingModel) { $EmbeddingModel = $tier.EmbeddingModel; Write-Output "[INFO] $($tier.EmbeddingReason)" }
    }

    $data = $DataDir.Replace('\', '/').TrimEnd('/') + '/data'
    # Vorab berechnen: ein '""'-Literal in "...$(...)..." liest der Parser als
    # escaptes Anfuehrungszeichen des aeusseren Strings (-> kaputte Zeile).
    $frontendValue = if ($FrontendDistDir) { ConvertTo-TomlPath $FrontendDistDir } else { ConvertTo-TomlString '' }
    $title = if ($Version) { "Zettelwirtschaft Native $Version" } else { "Zettelwirtschaft Native" }

    $lines = @(
        "# $title - config.toml, erzeugt am $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    )
    if ($HeaderComment) { $lines += "# $($HeaderComment -replace '[\r\n]+', ' ')" }
    $lines += @(
        "# Aenderungen werden nach einem Neustart des Dienstes wirksam.",
        "",
        "# 0.0.0.0 = im Heimnetz erreichbar (Smartphone), 127.0.0.1 = nur dieser PC.",
        "# Im LAN ohne HTTPS laufen PIN, Sitzungs-Cookie und Dokumente unverschluesselt",
        "# durchs (W)LAN - HTTPS siehe SERVER_SSL_* (config.toml.example).",
        "SERVER_HOST = $(ConvertTo-TomlString $ServerHost)",
        "SERVER_PORT = $Port"
    )
    if ($SslCertFile) {
        $lines += @(
            "SERVER_SSL_CERTFILE = $(ConvertTo-TomlPath $SslCertFile)",
            "SERVER_SSL_KEYFILE = $(ConvertTo-TomlPath $SslKeyFile)"
        )
    }
    $lines += @(
        "",
        "DATABASE_URL = $(ConvertTo-TomlString "sqlite+aiosqlite:///$data/zettelwirtschaft.db")",
        "UPLOAD_DIR = $(ConvertTo-TomlPath "$data/uploads")",
        "ARCHIVE_DIR = $(ConvertTo-TomlPath "$data/archive")",
        "THUMBNAIL_DIR = $(ConvertTo-TomlPath "$data/thumbnails")",
        "WATCH_DIR = $(ConvertTo-TomlPath "$data/watch")",
        "EXPORT_DIR = `"`"",
        "FRONTEND_DIST_DIR = $frontendValue",
        "",
        "OLLAMA_BASE_URL = `"http://localhost:11434`"",
        "# Analysemodell (exakter Ollama-Tag); fehlende Modelle laedt der Dienst beim Start.",
        "# Benoetigt Ollama >= $ZwOllamaMinVersion (getestet mit $ZwOllamaTestedVersion).",
        "OLLAMA_MODEL = $(ConvertTo-TomlString $OllamaModel)",
        "OLLAMA_TIMEOUT = 300",
        "OLLAMA_MAX_RETRIES = 2",
        "",
        "OCR_LANGUAGES = `"deu+eng`"",
        "LOG_LEVEL = `"INFO`"",
        "",
        "PIN_ENABLED = $(if ($PinEnabled) { 'true' } else { 'false' })",
        "PIN_CODE = $(ConvertTo-TomlString $PinCode)",
        "",
        "# Native: ChromaDB embedded im Backend-Prozess, kein separater Dienst.",
        "CHROMADB_MODE = `"embedded`"",
        "CHROMADB_PATH = $(ConvertTo-TomlPath "$data/chromadb")",
        "# Suchmodell. Ein Wechsel baut den Vektor-Index beim naechsten Start neu auf.",
        "EMBEDDING_MODEL = $(ConvertTo-TomlString $EmbeddingModel)",
        "",
        "# Schluessel fuer gespeicherte E-Mail-Passwoerter. NICHT aendern, sonst",
        "# koennen bestehende IMAP-Passwoerter nicht mehr entschluesselt werden.",
        "EMAIL_ENCRYPTION_KEY = $(ConvertTo-TomlString $EmailKey)"
    )

    $outDir = Split-Path -Parent $OutPath
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    }
    # UTF-8 OHNE BOM: tomllib (Python) lehnt eine BOM ab.
    $text = ($lines -join "`r`n") + "`r`n"
    # SEC-DATA-03: PIN_CODE + EMAIL_ENCRYPTION_KEY - Datei erst anlegen und auf
    # SYSTEM/Administratoren/dieses Konto einschraenken, dann befuellen (sonst
    # erbt sie z.B. unter C:\Zettelwirtschaft "Authentifizierte Benutzer: Aendern").
    if (-not (Test-Path -LiteralPath $OutPath)) {
        [System.IO.File]::WriteAllText($OutPath, '')
    }
    $null = Protect-ZwPath -LiteralPath $OutPath
    [System.IO.File]::WriteAllText($OutPath, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "[OK] config.toml geschrieben: $OutPath"
    exit 0
} catch {
    [Console]::Error.WriteLine("[FEHLER] config.toml nicht geschrieben: $($_.Exception.Message)")
    exit 1
}
