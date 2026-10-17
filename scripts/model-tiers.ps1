# model-tiers.ps1 - Gemeinsame Modellauswahl der Installer (per Dot-Sourcing).
#
# Einzige Stelle fuer die Hardware-Stufen und Modellnamen der drei Installer:
#   - install-gui.ps1 / install.ps1 (Docker):  . "$PSScriptRoot\scripts\model-tiers.ps1"
#   - New-NativeConfig.ps1 / Convert-Env-To-Config.ps1 (Native, liegen im
#     selben Ordner):                           . "$PSScriptRoot\model-tiers.ps1"
# Ausgeliefert ueber release-manifest.txt (Docker-Paket, Pfad scripts/) und
# build-native.ps1 + setup-native.nsi (Native, flach im Installationsordner).
#
# Stufen (Entscheidung aus dem 70-Dokumente-Benchmark):
#   LLM:       gemma4:e2b-it-qat (Standard, 4,3 GB, CPU-tauglich);
#              gemma4:12b-it-qat nur bei NVIDIA-GPU mit >= 10 GB VRAM.
#              VRAM unbekannt -> e2b.
#   Embedding: qwen3-embedding:4b (Standard, 2,5 GB);
#              qwen3-embedding:0.6b bei < 12 GB RAM (e2b 4,3 GB + 4b 2,5 GB
#              passen auf 8 GB nicht neben das Betriebssystem). RAM unbekannt
#              -> 0.6b (die sichere Variante, wie beim VRAM).
#
# Die Defaults muessen zu backend/app/config.py passen, die Mindestversion zu
# OLLAMA_MIN_VERSION in backend/app/services/ollama_client.py und
# setup-native.nsi (backend/tests/test_docs_consistency.py prueft beides).
# Windows PowerShell 5.1 kompatibel; die Datei ist UTF-8 MIT BOM (Umlaute).

$ZwLlmDefaultModel       = 'gemma4:e2b-it-qat'
$ZwLlmGpuModel           = 'gemma4:12b-it-qat'
$ZwEmbeddingDefaultModel = 'qwen3-embedding:4b'
$ZwEmbeddingLeanModel    = 'qwen3-embedding:0.6b'

# Bisherige Defaults (bis v1.5). Bestehende Installationen ohne expliziten
# Eintrag in ihrer .env liefen damit — sie werden bei einer Migration bzw.
# einem Update eingetragen, damit sich das Modell nicht still aendert.
$ZwLegacyLlmModel        = 'qwen2.5:7b-instruct'
$ZwLegacyEmbeddingModel  = 'bge-m3'

$ZwGpuMinVramGB          = 10
$ZwLeanRamBelowGB        = 12

$ZwOllamaMinVersion      = '0.30.3'
$ZwOllamaTestedVersion   = '0.40.5'

# Auswahllisten der Installer (Reihenfolge = Anzeige).
$ZwLlmChoices       = @($ZwLlmDefaultModel, $ZwLlmGpuModel, $ZwLegacyLlmModel)
$ZwEmbeddingChoices = @($ZwEmbeddingDefaultModel, $ZwEmbeddingLeanModel, $ZwLegacyEmbeddingModel)

function Get-ZwTotalRamGB {
    # Gerundete GB; 0 = unbekannt.
    try {
        $bytes = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
        return [int][math]::Round($bytes / 1GB)
    } catch {
        return 0
    }
}

function Get-ZwNvidiaVramGB {
    # Groesster Grafikspeicher (GB) aller NVIDIA-GPUs laut nvidia-smi, $null = unbekannt.
    # Win32_VideoController.AdapterRAM ist ein 32-Bit-Feld und endet bei 4 GB —
    # fuer die 10-GB-Schwelle unbrauchbar.
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue | Select-Object -First 1
    $exe = if ($smi) { $smi.Source } else { $null }
    if (-not $exe -and $env:ProgramFiles) {
        $candidate = Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'
        if (Test-Path -LiteralPath $candidate) { $exe = $candidate }
    }
    if (-not $exe) { return $null }
    try {
        $out = & $exe --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
    } catch {
        return $null
    }
    return ConvertFrom-ZwNvidiaSmiMemory $out
}

function ConvertFrom-ZwNvidiaSmiMemory {
    # Ausgabe von "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits"
    # (MiB, eine Zeile pro GPU) -> groesster Wert in GB (1 Nachkommastelle), $null wenn leer.
    param($Lines)
    $max = $null
    foreach ($line in @($Lines)) {
        $mib = 0
        if ([int]::TryParse("$line".Trim(), [ref]$mib) -and $mib -gt 0) {
            $gb = [math]::Round($mib / 1024, 1)
            if ($null -eq $max -or $gb -gt $max) { $max = $gb }
        }
    }
    return $max
}

function Get-ZwModelTier {
    # Reine Entscheidungsfunktion (testbar): RamGB 0 = unbekannt, VramGB $null = unbekannt.
    param(
        [int]$RamGB = 0,
        $VramGB = $null
    )
    $gpu = ($null -ne $VramGB) -and ([double]$VramGB -ge $ZwGpuMinVramGB)
    if ($gpu) {
        $llm = $ZwLlmGpuModel
        $llmReason = "NVIDIA-GPU mit $VramGB GB Grafikspeicher erkannt: $ZwLlmGpuModel (genauer, ca. 7,2 GB)."
    } elseif ($null -ne $VramGB) {
        $llm = $ZwLlmDefaultModel
        $llmReason = "NVIDIA-GPU mit $VramGB GB Grafikspeicher (unter $ZwGpuMinVramGB GB): $ZwLlmDefaultModel (ca. 4,3 GB)."
    } else {
        $llm = $ZwLlmDefaultModel
        $llmReason = "Keine NVIDIA-GPU mit mindestens $ZwGpuMinVramGB GB Grafikspeicher erkannt: $ZwLlmDefaultModel (ca. 4,3 GB, läuft auch auf der CPU)."
    }

    $lean = ($RamGB -le 0) -or ($RamGB -lt $ZwLeanRamBelowGB)
    if ($RamGB -le 0) {
        $embedding = $ZwEmbeddingLeanModel
        $embeddingReason = "Arbeitsspeicher unbekannt: $ZwEmbeddingLeanModel (kleines Suchmodell, passt sicher neben das Analysemodell)."
    } elseif ($lean) {
        $embedding = $ZwEmbeddingLeanModel
        $embeddingReason = "Weniger als $ZwLeanRamBelowGB GB Arbeitsspeicher: $ZwEmbeddingLeanModel statt $ZwEmbeddingDefaultModel, damit Analyse- und Suchmodell gemeinsam in den Speicher passen."
    } else {
        $embedding = $ZwEmbeddingDefaultModel
        $embeddingReason = "$RamGB GB Arbeitsspeicher: $ZwEmbeddingDefaultModel (beste Suchtreffer, ca. 2,5 GB)."
    }

    return [pscustomobject]@{
        LlmModel        = $llm
        EmbeddingModel  = $embedding
        Gpu             = [bool]$gpu
        Lean            = [bool]$lean
        RamGB           = $RamGB
        VramGB          = $VramGB
        LlmReason       = $llmReason
        EmbeddingReason = $embeddingReason
    }
}

function Get-ZwHardwareModelTier {
    # Modellstufe fuer diesen Rechner.
    return Get-ZwModelTier -RamGB (Get-ZwTotalRamGB) -VramGB (Get-ZwNvidiaVramGB)
}

function Get-ZwEnvValue {
    # Letzter aktiver (nicht auskommentierter) Wert von $Name in einer .env, $null wenn keiner.
    # Wie python-dotenv: spaetere Zeilen gewinnen, umschliessende Anfuehrungszeichen entfallen.
    param([string]$EnvPath, [string]$Name)
    if (-not (Test-Path -LiteralPath $EnvPath)) { return $null }
    $value = $null
    foreach ($line in [System.IO.File]::ReadAllLines($EnvPath)) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*?)\s*$") {
            $v = $Matches[1]
            if ($v.Length -ge 2 -and (($v[0] -eq '"' -and $v[-1] -eq '"') -or ($v[0] -eq "'" -and $v[-1] -eq "'"))) {
                $v = $v.Substring(1, $v.Length - 2)
            }
            $value = $v
        }
    }
    if ([string]::IsNullOrWhiteSpace($value)) { return $null }
    return $value
}

function Get-ZwInstalledModels {
    # Modelle einer bestehenden Docker-Installation (.env). Fehlt ein Eintrag,
    # lief die Installation mit dem bisherigen Default (vor v1.6) — der gilt,
    # nicht der neue. $null, wenn es keine .env gibt.
    param([string]$EnvPath)
    if (-not (Test-Path -LiteralPath $EnvPath)) { return $null }
    $llm = Get-ZwEnvValue -EnvPath $EnvPath -Name 'OLLAMA_MODEL'
    $embedding = Get-ZwEnvValue -EnvPath $EnvPath -Name 'EMBEDDING_MODEL'
    return [pscustomobject]@{
        LlmModel       = $(if ($llm) { $llm } else { $ZwLegacyLlmModel })
        EmbeddingModel = $(if ($embedding) { $embedding } else { $ZwLegacyEmbeddingModel })
        LlmExplicit       = [bool]$llm
        EmbeddingExplicit = [bool]$embedding
    }
}
function Add-ZwLegacyModelDefaults {
    # Update einer bestehenden Docker-Installation (install-gui.ps1, update.bat):
    # fehlt OLLAMA_MODEL bzw. EMBEDDING_MODEL als aktive Zeile in der .env, lief
    # die Installation mit dem bisherigen Default. Der wird eingetragen, damit
    # das Update (neue Backend-Defaults ab v1.6) das Modell nicht still wechselt
    # (neues Suchmodell = Neuaufbau des Vektor-Index, mehrere GB Download).
    # MUSS vor "docker compose up" laufen: der Container liest die .env nur beim
    # Erstellen. Ab installierter Version 1.6.0 (data\.version) nichts tun —
    # dann hat der Benutzer einen fehlenden Eintrag selbst entfernt.
    # Rueckgabe: die ergaenzten Schluessel.
    param([string]$EnvPath, [string]$InstalledVersion = '')
    if (-not (Test-Path -LiteralPath $EnvPath)) { return @() }
    $parsed = $null
    if ($InstalledVersion -and [version]::TryParse(($InstalledVersion.Trim() -split '-', 2)[0], [ref]$parsed) -and $parsed -ge [version]'1.6.0') {
        return @()
    }
    $content = [System.IO.File]::ReadAllText($EnvPath)
    $nl = if ($content -match "`r`n") { "`r`n" } else { "`n" }
    $append = ''
    $added = @()
    foreach ($entry in @(
        @('OLLAMA_MODEL', $ZwLegacyLlmModel, 'KI-Analysemodell'),
        @('EMBEDDING_MODEL', $ZwLegacyEmbeddingModel, 'Suchmodell')
    )) {
        if (-not (Get-ZwEnvValue -EnvPath $EnvPath -Name $entry[0])) {
            $append += "# $($entry[2]) (bisheriger Standard, beim Update auf v1.6 festgeschrieben)$nl$($entry[0])=$($entry[1])$nl"
            $added += $entry[0]
        }
    }
    if ($added.Count -gt 0) {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { $append = $nl + $append }
        # UTF-8 ohne BOM wie die uebrige .env (docker compose / python-dotenv).
        [System.IO.File]::AppendAllText($EnvPath, $append, (New-Object System.Text.UTF8Encoding($false)))
    }
    return $added
}
