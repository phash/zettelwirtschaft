# Zettelwirtschaft - Grafischer Windows-Installer
$ErrorActionPreference = "Stop"
$script:ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $script:ProjectDir
# Modell-Stufen (gemeinsam mit install.ps1 und dem Native-Setup)
. (Join-Path $script:ProjectDir "scripts\model-tiers.ps1")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# --- Versionierung ---
$newVersionFile  = Join-Path $script:ProjectDir "VERSION"
$instVersionFile = Join-Path $script:ProjectDir "data\.version"
$script:NewVersion       = if (Test-Path $newVersionFile)  { (Get-Content $newVersionFile  -Raw).Trim() } else { "unbekannt" }
$script:InstalledVersion = if (Test-Path $instVersionFile) { (Get-Content $instVersionFile -Raw).Trim() } else { "" }

# --- Migrationspfade: Neue .env-Variablen pro Version ---
# Format: Version -> @{ VarName = DefaultValue; Comment = "Beschreibung" }
$script:ConfigMigrations = [ordered]@{
    "1.0.2" = @{
        "EXPORT_DIR" = @{ value = ""; comment = "# Zielordner fuer verarbeitete Dokumente (leer = deaktiviert)" }
    }
    "1.0.9" = @{
        "CHROMADB_HOST" = @{ value = "chromadb"; comment = "# ChromaDB (RAG / KI-Assistent)" }
    }
    # v1.6 (neue Modell-Defaults): bisherige Modelle festschreiben uebernimmt
    # Add-ZwLegacyModelDefaults (scripts\model-tiers.ps1, auch von update.bat genutzt).
    # Neue Versionen hier ergaenzen:
    # "1.1.0" = @{ "NEW_VAR" = @{ value = "default"; comment = "# Beschreibung" } }
}

# --- State ---
$script:Step           = 0
$script:IsUpdate       = $false
$script:BackupDir      = ""
$script:ExistingInstall = $false
$script:Config         = @{ Port=8080; WatchEnabled=$false; Model=$ZwLlmDefaultModel; EmbeddingModel=$ZwEmbeddingDefaultModel; PinEnabled=$false; PinCode="" }
$script:Checks         = @{ DockerOK=$false; DockerRun=$false; GPU=$false; GPUName=""; VramGB=$null; RAM=0; FreeGB=0 }
$script:Job            = $null
$script:Phase          = 0
# Nicht-fatale Fehler (Backup, Health-Timeout, Modell-Download): die Installation
# ist nutzbar, wird aber als "mit Warnungen" abgeschlossen.
$script:Warnings       = @()
$script:HasSources     = Test-Path (Join-Path $script:ProjectDir "backend")

# --- Colors ---
$cAccent  = [System.Drawing.Color]::FromArgb(0, 150, 136)
$cHeader  = [System.Drawing.Color]::FromArgb(38, 50, 56)
$cOK      = [System.Drawing.Color]::FromArgb(56, 142, 60)
$cWarn    = [System.Drawing.Color]::FromArgb(245, 124, 0)
$cErr     = [System.Drawing.Color]::FromArgb(211, 47, 47)
$cInfo    = [System.Drawing.Color]::FromArgb(25, 118, 210)
$cSub     = [System.Drawing.Color]::FromArgb(117, 117, 117)
$cBorder  = [System.Drawing.Color]::FromArgb(224, 224, 224)
$cUpdate  = [System.Drawing.Color]::FromArgb(0, 121, 107)

# --- Form ---
$form = New-Object System.Windows.Forms.Form
$form.Text = "Zettelwirtschaft - Installation"
$form.ClientSize = [System.Drawing.Size]::new(640, 480)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
$form.BackColor = [System.Drawing.Color]::White

# Header
$pnlHeader = New-Object System.Windows.Forms.Panel
$pnlHeader.Dock = "Top"; $pnlHeader.Height = 70; $pnlHeader.BackColor = $cHeader

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "Zettelwirtschaft"
$lblTitle.Location = [System.Drawing.Point]::new(20, 10)
$lblTitle.Size = [System.Drawing.Size]::new(400, 30)
$lblTitle.Font = New-Object System.Drawing.Font("Segoe UI", 16, [System.Drawing.FontStyle]::Bold)
$lblTitle.ForeColor = [System.Drawing.Color]::White
$pnlHeader.Controls.Add($lblTitle)

$lblStep = New-Object System.Windows.Forms.Label
$lblStep.Text = "Willkommen"
$lblStep.Location = [System.Drawing.Point]::new(20, 42)
$lblStep.Size = [System.Drawing.Size]::new(400, 20)
$lblStep.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblStep.ForeColor = [System.Drawing.Color]::FromArgb(176, 190, 197)
$pnlHeader.Controls.Add($lblStep)

$lblVersion = New-Object System.Windows.Forms.Label
$lblVersion.Text = "v$($script:NewVersion)"
$lblVersion.Location = [System.Drawing.Point]::new(560, 10)
$lblVersion.Size = [System.Drawing.Size]::new(60, 20)
$lblVersion.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$lblVersion.ForeColor = [System.Drawing.Color]::FromArgb(120, 160, 170)
$lblVersion.TextAlign = "MiddleRight"
$pnlHeader.Controls.Add($lblVersion)
$form.Controls.Add($pnlHeader)

# Content
$pnlContent = New-Object System.Windows.Forms.Panel
$pnlContent.Location = [System.Drawing.Point]::new(0, 70)
$pnlContent.Size = [System.Drawing.Size]::new(640, 358)
$pnlContent.BackColor = [System.Drawing.Color]::White
$form.Controls.Add($pnlContent)

# Button Bar
$pnlButtons = New-Object System.Windows.Forms.Panel
$pnlButtons.Dock = "Bottom"; $pnlButtons.Height = 52
$pnlButtons.BackColor = [System.Drawing.Color]::FromArgb(250, 250, 250)

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "Zurueck"; $btnBack.Size = [System.Drawing.Size]::new(90, 34)
$btnBack.Location = [System.Drawing.Point]::new(340, 9)
$btnBack.FlatStyle = "Flat"; $btnBack.FlatAppearance.BorderColor = $cBorder
$btnBack.Enabled = $false
$pnlButtons.Controls.Add($btnBack)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "Weiter"; $btnNext.Size = [System.Drawing.Size]::new(100, 34)
$btnNext.Location = [System.Drawing.Point]::new(436, 9)
$btnNext.FlatStyle = "Flat"; $btnNext.BackColor = $cAccent
$btnNext.ForeColor = [System.Drawing.Color]::White; $btnNext.FlatAppearance.BorderSize = 0
$pnlButtons.Controls.Add($btnNext)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Abbrechen"; $btnCancel.Size = [System.Drawing.Size]::new(90, 34)
$btnCancel.Location = [System.Drawing.Point]::new(542, 9)
$btnCancel.FlatStyle = "Flat"; $btnCancel.FlatAppearance.BorderColor = $cBorder
$pnlButtons.Controls.Add($btnCancel)
$form.Controls.Add($pnlButtons)

# Timer for async jobs
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500

# --- Helper: Add check/info items ---
function Add-CheckItem {
    param([string]$Text, [bool]$OK, [ref]$Y, [string]$Type = "")
    $icon = New-Object System.Windows.Forms.Label
    $icon.Location = [System.Drawing.Point]::new(30, $Y.Value)
    $icon.Size = [System.Drawing.Size]::new(24, 24)
    $icon.Font = New-Object System.Drawing.Font("Segoe UI", 11)
    if ($OK) { $icon.Text = [char]0x2713; $icon.ForeColor = $cOK }
    elseif ($Type -eq "Warnung") { $icon.Text = "!"; $icon.ForeColor = $cWarn }
    else { $icon.Text = "X"; $icon.ForeColor = $cErr }
    $pnlContent.Controls.Add($icon)
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = [System.Drawing.Point]::new(56, $Y.Value + 2)
    $lbl.Size = [System.Drawing.Size]::new(530, 22); $lbl.Text = $Text
    $pnlContent.Controls.Add($lbl)
    $Y.Value += 32
}

function Add-InfoItem {
    param([string]$Text, [ref]$Y)
    $icon = New-Object System.Windows.Forms.Label
    $icon.Location = [System.Drawing.Point]::new(30, $Y.Value)
    $icon.Size = [System.Drawing.Size]::new(24, 24)
    $icon.Font = New-Object System.Drawing.Font("Segoe UI", 11)
    $icon.Text = "i"; $icon.ForeColor = $cInfo
    $pnlContent.Controls.Add($icon)
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = [System.Drawing.Point]::new(56, $Y.Value + 2)
    $lbl.Size = [System.Drawing.Size]::new(530, 22); $lbl.Text = $Text; $lbl.ForeColor = $cSub
    $pnlContent.Controls.Add($lbl)
    $Y.Value += 32
}

function Log {
    param([string]$Text)
    if ($script:logBox) { $script:logBox.AppendText("$Text`r`n"); $script:logBox.ScrollToCaret() }
}

# ============================================================
# Step 0: Welcome
# ============================================================
function Show-Welcome {
    $lblStep.Text = "Willkommen"
    $pnlContent.Controls.Clear()
    $btnBack.Enabled = $false
    $btnNext.Text = "Weiter"

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Willkommen beim Installationsassistenten"
    $lbl.Location = [System.Drawing.Point]::new(30, 20)
    $lbl.Size = [System.Drawing.Size]::new(560, 30)
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 13)
    $pnlContent.Controls.Add($lbl)

    # Bestehende Installation erkennen
    $envExists     = Test-Path (Join-Path $script:ProjectDir ".env")
    $dbExists      = Test-Path (Join-Path $script:ProjectDir "data\zettelwirtschaft.db")
    $archiveExists = Test-Path (Join-Path $script:ProjectDir "data\archive")
    $verExists     = Test-Path $instVersionFile
    # Erkennung: version-Datei allein reicht; alternativ .env + (DB oder Archiv-Ordner)
    $script:ExistingInstall = $verExists -or ($envExists -and ($dbExists -or $archiveExists))

    if ($script:ExistingInstall) {
        $instVer = if ($script:InstalledVersion) { "v$($script:InstalledVersion)" } else { "unbekannte Version" }
        $hint = New-Object System.Windows.Forms.Label
        $hint.Location = [System.Drawing.Point]::new(30, 55)
        $hint.Size = [System.Drawing.Size]::new(560, 22)
        $hint.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
        $hint.ForeColor = $cUpdate
        $hint.Text = "Bestehende Installation erkannt ($instVer) - Update oder Neuinstallation moeglich."
        $pnlContent.Controls.Add($hint)
    }

    $descY = if ($script:ExistingInstall) { 90 } else { 60 }
    $desc = New-Object System.Windows.Forms.Label
    $desc.Location = [System.Drawing.Point]::new(30, $descY)
    $desc.Size = [System.Drawing.Size]::new(560, 280)
    $desc.Text = "Zettelwirtschaft ist ein lokales Dokumentenmanagementsystem`nfuer Privathaushalte.`n`nRechnungen, Belege und Dokumente werden per Scanner oder`nSmartphone erfasst, automatisch durch KI analysiert,`nkategorisiert und durchsuchbar archiviert.`n`nFolgende Komponenten werden installiert:`n`n    Backend-Server (Dokumentenverarbeitung + API)`n    Frontend (Weboberflaeche)`n    Ollama (lokale KI fuer Dokumentenanalyse)`n    KI-Modelle für Analyse und Suche (ca. 5-10 GB Download)`n`nVoraussetzungen:`n    Docker Desktop installiert und gestartet`n    Mindestens 8 GB RAM empfohlen`n    Mindestens 10 GB freier Speicherplatz"
    $pnlContent.Controls.Add($desc)
}

# ============================================================
# Step 10: Migration / Update-Wahl
# ============================================================
function Show-Migration {
    $lblStep.Text = "Bestehende Installation"
    $pnlContent.Controls.Clear()
    $btnBack.Enabled = $true
    $btnNext.Visible = $false

    $instVer = if ($script:InstalledVersion) { "v$($script:InstalledVersion)" } else { "unbekannte Version" }

    # Semantischer Versionsvergleich
    $versionRelation = "upgrade"  # upgrade | downgrade | same | unknown
    if ($script:InstalledVersion -and $script:NewVersion) {
        try {
            $vInst = [System.Version]$script:InstalledVersion
            $vNew  = [System.Version]$script:NewVersion
            if     ($vNew -gt $vInst) { $versionRelation = "upgrade" }
            elseif ($vNew -lt $vInst) { $versionRelation = "downgrade" }
            else                       { $versionRelation = "same" }
        } catch { $versionRelation = "unknown" }
    }

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Bestehende Installation gefunden"
    $lbl.Location = [System.Drawing.Point]::new(30, 20); $lbl.Size = [System.Drawing.Size]::new(560, 30)
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 13); $lbl.ForeColor = $cHeader
    $pnlContent.Controls.Add($lbl)

    # Version-Info
    $verPanel = New-Object System.Windows.Forms.Panel
    $verPanel.Location = [System.Drawing.Point]::new(30, 60); $verPanel.Size = [System.Drawing.Size]::new(560, 50)
    $verPanel.BackColor = [System.Drawing.Color]::FromArgb(245, 250, 255)
    $pnlContent.Controls.Add($verPanel)

    $lblInstVer = New-Object System.Windows.Forms.Label
    $lblInstVer.Location = [System.Drawing.Point]::new(15, 8); $lblInstVer.Size = [System.Drawing.Size]::new(530, 20)
    $lblInstVer.Text = "Installierte Version:   $instVer"; $lblInstVer.ForeColor = $cSub
    $verPanel.Controls.Add($lblInstVer)

    $newVerColor = switch ($versionRelation) {
        "upgrade"   { $cOK }
        "downgrade" { $cErr }
        "same"      { $cSub }
        default     { $cSub }
    }
    $newVerArrow = switch ($versionRelation) {
        "upgrade"   { "  (Upgrade)" }
        "downgrade" { "  (DOWNGRADE - aelter als installierte Version!)" }
        "same"      { "  (gleiche Version)" }
        default     { "" }
    }
    $lblNewVer = New-Object System.Windows.Forms.Label
    $lblNewVer.Location = [System.Drawing.Point]::new(15, 28); $lblNewVer.Size = [System.Drawing.Size]::new(530, 20)
    $lblNewVer.Text = "Installer-Version:      v$($script:NewVersion)$newVerArrow"
    $lblNewVer.ForeColor = $newVerColor
    $lblNewVer.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
    $verPanel.Controls.Add($lblNewVer)

    # Option 1: Update / Beibehalten
    $pnlUpdate = New-Object System.Windows.Forms.Panel
    $pnlUpdate.Location = [System.Drawing.Point]::new(30, 125); $pnlUpdate.Size = [System.Drawing.Size]::new(560, 90)
    $pnlUpdate.Cursor = "Hand"
    $pnlContent.Controls.Add($pnlUpdate)

    switch ($versionRelation) {
        "upgrade" {
            $pnlUpdate.BackColor = [System.Drawing.Color]::FromArgb(232, 245, 233)
            $updateTitle = "Aktualisieren auf v$($script:NewVersion)  (empfohlen)"
            $updateDesc  = "Datenbank, Konfiguration und Dokumente bleiben erhalten. Automatisches Backup vor dem Update."
            $updateColor = $cOK
        }
        "downgrade" {
            $pnlUpdate.BackColor = [System.Drawing.Color]::FromArgb(255, 235, 238)
            $updateTitle = "Downgrade auf v$($script:NewVersion)  (nicht empfohlen)"
            $updateDesc  = "Achtung: Der Installer ist aelter als die installierte Version. Datenbank-Aenderungen koennen inkompatibel sein."
            $updateColor = $cErr
        }
        "same" {
            $pnlUpdate.BackColor = [System.Drawing.Color]::FromArgb(245, 250, 255)
            $updateTitle = "Installation reparieren / neu konfigurieren"
            $updateDesc  = "Gleiche Version. Docker-Images werden neu eingespielt, Konfiguration kann angepasst werden."
            $updateColor = $cSub
        }
        default {
            $pnlUpdate.BackColor = [System.Drawing.Color]::FromArgb(245, 250, 255)
            $updateTitle = "Installation aktualisieren"
            $updateDesc  = "Datenbank, Konfiguration und Dokumente bleiben erhalten. Automatisches Backup vor dem Update."
            $updateColor = $cSub
        }
    }

    $lblUpdate = New-Object System.Windows.Forms.Label
    $lblUpdate.Location = [System.Drawing.Point]::new(15, 10); $lblUpdate.Size = [System.Drawing.Size]::new(530, 22)
    $lblUpdate.Text = $updateTitle
    $lblUpdate.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
    $lblUpdate.ForeColor = $updateColor
    $pnlUpdate.Controls.Add($lblUpdate)

    $lblUpdateDesc = New-Object System.Windows.Forms.Label
    $lblUpdateDesc.Location = [System.Drawing.Point]::new(15, 36); $lblUpdateDesc.Size = [System.Drawing.Size]::new(530, 44)
    $lblUpdateDesc.Text = $updateDesc
    $lblUpdateDesc.ForeColor = $cSub
    $pnlUpdate.Controls.Add($lblUpdateDesc)

    $doUpdate = {
        $script:IsUpdate = $true
        $script:Step = 10
        $btnNext.Visible = $true
        Show-Prerequisites
    }
    $pnlUpdate.add_Click($doUpdate)
    $lblUpdate.add_Click($doUpdate)
    $lblUpdateDesc.add_Click($doUpdate)

    # Option 2: Reinstall
    $pnlReinstall = New-Object System.Windows.Forms.Panel
    $pnlReinstall.Location = [System.Drawing.Point]::new(30, 230); $pnlReinstall.Size = [System.Drawing.Size]::new(560, 80)
    $pnlReinstall.BackColor = [System.Drawing.Color]::FromArgb(255, 243, 224)
    $pnlReinstall.Cursor = "Hand"
    $pnlContent.Controls.Add($pnlReinstall)

    $lblReinstall = New-Object System.Windows.Forms.Label
    $lblReinstall.Location = [System.Drawing.Point]::new(15, 10); $lblReinstall.Size = [System.Drawing.Size]::new(450, 22)
    $lblReinstall.Text = "Neu installieren  (Daten werden geloescht)"
    $lblReinstall.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
    $lblReinstall.ForeColor = $cWarn
    $pnlReinstall.Controls.Add($lblReinstall)

    $lblReinstallDesc = New-Object System.Windows.Forms.Label
    $lblReinstallDesc.Location = [System.Drawing.Point]::new(15, 34); $lblReinstallDesc.Size = [System.Drawing.Size]::new(520, 36)
    $lblReinstallDesc.Text = "Neue Konfiguration, leere Datenbank. Backup der alten Daten wird erstellt."
    $lblReinstallDesc.ForeColor = $cSub
    $pnlReinstall.Controls.Add($lblReinstallDesc)

    $pnlReinstall.add_Click({
        $r = [System.Windows.Forms.MessageBox]::Show(
            "Wirklich neu installieren?`n`nDie bestehende Datenbank und alle Dokumente werden geloescht.`nEin Backup wird vorher erstellt.",
            "Neu installieren", "YesNo", "Warning")
        if ($r -eq "Yes") {
            $script:IsUpdate = $false
            $script:Step = 10
            $btnNext.Visible = $true
            Show-Prerequisites
        }
    })
    $lblReinstall.add_Click($pnlReinstall.Click)
    $lblReinstallDesc.add_Click($pnlReinstall.Click)
}

# ============================================================
# Step 1: Prerequisites
# ============================================================
function Show-Prerequisites {
    $lblStep.Text = if ($script:IsUpdate) { "Update - Schritt 1 von 3 - Voraussetzungen" } else { "Schritt 1 von 4 - Voraussetzungen" }
    $pnlContent.Controls.Clear()
    $btnNext.Visible = $true
    $btnNext.Enabled = $true
    $btnNext.Text = "Weiter"
    $y = 20

    try { $null = Get-Command docker -ErrorAction Stop; $script:Checks.DockerOK = $true } catch { $script:Checks.DockerOK = $false }
    Add-CheckItem "Docker installiert" $script:Checks.DockerOK ([ref]$y)

    if ($script:Checks.DockerOK) {
        try { $null = docker info 2>&1; $script:Checks.DockerRun = ($LASTEXITCODE -eq 0) } catch { $script:Checks.DockerRun = $false }
    }
    Add-CheckItem "Docker Desktop laeuft" $script:Checks.DockerRun ([ref]$y) $(if (-not $script:Checks.DockerRun) { "Fehler" })

    $script:Checks.RAM = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $ramOK = $script:Checks.RAM -ge 8
    Add-CheckItem "$($script:Checks.RAM) GB RAM" $ramOK ([ref]$y) $(if (-not $ramOK) { "Warnung" })

    $drive = (Get-Item $script:ProjectDir).PSDrive
    $script:Checks.FreeGB = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB)
    $diskOK = $script:Checks.FreeGB -ge 10
    Add-CheckItem "$($script:Checks.FreeGB) GB freier Speicherplatz" $diskOK ([ref]$y) $(if (-not $diskOK) { "Warnung" })

    try {
        $gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
        if ($gpu) {
            $script:Checks.GPU = $true; $script:Checks.GPUName = @($gpu)[0].Name
            $script:Checks.VramGB = Get-ZwNvidiaVramGB
            $vramText = if ($null -ne $script:Checks.VramGB) { ", $($script:Checks.VramGB) GB Grafikspeicher" } else { "" }
            Add-InfoItem "NVIDIA GPU: $($script:Checks.GPUName)$vramText" ([ref]$y)
        } else { Add-InfoItem "Keine NVIDIA GPU (LLM laeuft auf CPU)" ([ref]$y) }
    } catch { Add-InfoItem "GPU-Erkennung fehlgeschlagen" ([ref]$y) }

    if (-not $script:Checks.DockerOK) {
        $y += 10
        $err = New-Object System.Windows.Forms.Label
        $err.Location = [System.Drawing.Point]::new(30, $y); $err.Size = [System.Drawing.Size]::new(560, 40)
        $err.ForeColor = $cErr; $err.Text = "Docker muss installiert sein:`nhttps://docker.com/products/docker-desktop/"
        $pnlContent.Controls.Add($err)
        $btnNext.Enabled = $false
    } elseif (-not $script:Checks.DockerRun) {
        $y += 10
        $err = New-Object System.Windows.Forms.Label
        $err.Location = [System.Drawing.Point]::new(30, $y); $err.Size = [System.Drawing.Size]::new(560, 40)
        $err.ForeColor = $cWarn; $err.Text = "Bitte starte Docker Desktop und klicke dann 'Weiter'."
        $pnlContent.Controls.Add($err)
    }
}

# ============================================================
# Step 2: Configuration
# ============================================================
function Show-Configuration {
    $lblStep.Text = "Schritt 2 von 4 - Konfiguration"
    $pnlContent.Controls.Clear()
    $y = 20

    # Port
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = [System.Drawing.Point]::new(30, $y+3); $lbl.Size = [System.Drawing.Size]::new(140, 22); $lbl.Text = "Frontend-Port:"
    $pnlContent.Controls.Add($lbl)
    $script:txtPort = New-Object System.Windows.Forms.TextBox
    $script:txtPort.Location = [System.Drawing.Point]::new(180, $y); $script:txtPort.Size = [System.Drawing.Size]::new(80, 26)
    $script:txtPort.Text = $script:Config.Port.ToString()
    $pnlContent.Controls.Add($script:txtPort)
    $y += 40

    # Watch folder (immer ./data/watch im Docker-Kontext)
    $script:chkWatch = New-Object System.Windows.Forms.CheckBox
    $script:chkWatch.Location = [System.Drawing.Point]::new(30, $y); $script:chkWatch.Size = [System.Drawing.Size]::new(560, 22)
    $script:chkWatch.Text = "Eingangsordner aktivieren (neue Dateien werden automatisch importiert)"
    $script:chkWatch.Checked = $script:Config.WatchEnabled
    $pnlContent.Controls.Add($script:chkWatch)
    $y += 26

    $watchInfoPath = Join-Path $script:ProjectDir "data\watch"
    $lblWatchInfo = New-Object System.Windows.Forms.Label
    $lblWatchInfo.Location = [System.Drawing.Point]::new(50, $y); $lblWatchInfo.Size = [System.Drawing.Size]::new(560, 20)
    $lblWatchInfo.Text = "Ordner: $watchInfoPath"
    $lblWatchInfo.ForeColor = $cSub
    $lblWatchInfo.Enabled = $script:chkWatch.Checked
    $pnlContent.Controls.Add($lblWatchInfo)

    $script:chkWatch.add_CheckedChanged({ $lblWatchInfo.Enabled = $script:chkWatch.Checked }.GetNewClosure())
    $y += 32

    # KI-Modelle: Vorschlag nach Hardware-Stufe (scripts\model-tiers.ps1)
    # Hinweise brauchen bis zu 3 Zeilen (Modellnamen werden nicht umbrochen, Segoe UI 8: 39 px).
    $tier = Get-ZwModelTier -RamGB $script:Checks.RAM -VramGB $script:Checks.VramGB
    $hintFont = New-Object System.Drawing.Font("Segoe UI", 8)

    $lbl2 = New-Object System.Windows.Forms.Label
    $lbl2.Location = [System.Drawing.Point]::new(30, $y+3); $lbl2.Size = [System.Drawing.Size]::new(140, 22); $lbl2.Text = "Analysemodell:"
    $pnlContent.Controls.Add($lbl2)
    $script:cmbModel = New-Object System.Windows.Forms.ComboBox
    $script:cmbModel.Location = [System.Drawing.Point]::new(180, $y); $script:cmbModel.Size = [System.Drawing.Size]::new(160, 26)
    $script:cmbModel.DropDownStyle = "DropDownList"
    $script:cmbModel.Items.AddRange([object[]]$ZwLlmChoices)
    $script:cmbModel.SelectedItem = $tier.LlmModel
    $pnlContent.Controls.Add($script:cmbModel)
    $lblMI = New-Object System.Windows.Forms.Label
    $lblMI.Location = [System.Drawing.Point]::new(350, $y-4); $lblMI.Size = [System.Drawing.Size]::new(250, 42); $lblMI.ForeColor = $cSub; $lblMI.Font = $hintFont
    $lblMI.Text = if ($tier.Gpu) {
        "Empfohlen: $($tier.LlmModel) — NVIDIA-GPU mit $($tier.VramGB) GB erkannt. $ZwLegacyLlmModel = bisheriger Standard."
    } else {
        "Empfohlen: $($tier.LlmModel) (läuft auch auf der CPU). $ZwLlmGpuModel nur mit NVIDIA-GPU ab $ZwGpuMinVramGB GB."
    }
    $pnlContent.Controls.Add($lblMI)
    $y += 42

    $lbl3 = New-Object System.Windows.Forms.Label
    $lbl3.Location = [System.Drawing.Point]::new(30, $y+3); $lbl3.Size = [System.Drawing.Size]::new(140, 22); $lbl3.Text = "Suchmodell:"
    $pnlContent.Controls.Add($lbl3)
    $script:cmbEmbedding = New-Object System.Windows.Forms.ComboBox
    $script:cmbEmbedding.Location = [System.Drawing.Point]::new(180, $y); $script:cmbEmbedding.Size = [System.Drawing.Size]::new(160, 26)
    $script:cmbEmbedding.DropDownStyle = "DropDownList"
    $script:cmbEmbedding.Items.AddRange([object[]]$ZwEmbeddingChoices)
    $script:cmbEmbedding.SelectedItem = $tier.EmbeddingModel
    $pnlContent.Controls.Add($script:cmbEmbedding)
    $lblEI = New-Object System.Windows.Forms.Label
    $lblEI.Location = [System.Drawing.Point]::new(350, $y-4); $lblEI.Size = [System.Drawing.Size]::new(250, 42); $lblEI.ForeColor = $cSub; $lblEI.Font = $hintFont
    $lblEI.Text = if ($tier.Lean) {
        "Empfohlen: $ZwEmbeddingLeanModel — unter $ZwLeanRamBelowGB GB RAM passt $ZwEmbeddingDefaultModel nicht neben das Analysemodell."
    } else {
        "Empfohlen: $ZwEmbeddingDefaultModel (beste Suchtreffer). Unter $ZwLeanRamBelowGB GB RAM: $ZwEmbeddingLeanModel."
    }
    $pnlContent.Controls.Add($lblEI)
    $y += 44

    # Separator
    $sep = New-Object System.Windows.Forms.Label
    $sep.Location = [System.Drawing.Point]::new(30, $y); $sep.Size = [System.Drawing.Size]::new(560, 1); $sep.BorderStyle = "Fixed3D"
    $pnlContent.Controls.Add($sep)
    $y += 12

    # PIN (B1: per Default aktiviert, auto-generierter 6-stelliger PIN vorbefuellt)
    $script:chkPin = New-Object System.Windows.Forms.CheckBox
    $script:chkPin.Location = [System.Drawing.Point]::new(30, $y); $script:chkPin.Size = [System.Drawing.Size]::new(560, 22)
    $script:chkPin.Text = "PIN-Schutz aktivieren (empfohlen)"
    $script:chkPin.Checked = $true
    $pnlContent.Controls.Add($script:chkPin)
    $y += 30

    # Auto-generierten Default-PIN setzen (numerisch, 6 Stellen, krypto-RNG)
    $rngPinDef = [Security.Cryptography.RandomNumberGenerator]::Create()
    $pinBytesDef = New-Object byte[] 4
    $rngPinDef.GetBytes($pinBytesDef)
    $pinNumDef = [BitConverter]::ToUInt32($pinBytesDef, 0) % 1000000
    $defaultPin = "{0:D6}" -f $pinNumDef

    $lp1 = New-Object System.Windows.Forms.Label
    $lp1.Location = [System.Drawing.Point]::new(50, $y+3); $lp1.Size = [System.Drawing.Size]::new(100, 22); $lp1.Text = "PIN:"
    $pnlContent.Controls.Add($lp1)
    $script:txtPin1 = New-Object System.Windows.Forms.TextBox
    $script:txtPin1.Location = [System.Drawing.Point]::new(180, $y); $script:txtPin1.Size = [System.Drawing.Size]::new(150, 26)
    $script:txtPin1.UseSystemPasswordChar = $false
    $script:txtPin1.Text = $defaultPin
    $pnlContent.Controls.Add($script:txtPin1)
    $y += 30

    $lp2 = New-Object System.Windows.Forms.Label
    $lp2.Location = [System.Drawing.Point]::new(50, $y+3); $lp2.Size = [System.Drawing.Size]::new(100, 22); $lp2.Text = "Bestaetigen:"
    $pnlContent.Controls.Add($lp2)
    $script:txtPin2 = New-Object System.Windows.Forms.TextBox
    $script:txtPin2.Location = [System.Drawing.Point]::new(180, $y); $script:txtPin2.Size = [System.Drawing.Size]::new(150, 26)
    $script:txtPin2.UseSystemPasswordChar = $false
    $script:txtPin2.Text = $defaultPin
    $pnlContent.Controls.Add($script:txtPin2)
    $y += 30

    $lblPinHint = New-Object System.Windows.Forms.Label
    $lblPinHint.Location = [System.Drawing.Point]::new(50, $y); $lblPinHint.Size = [System.Drawing.Size]::new(540, 32)
    $lblPinHint.Text = "Ohne PIN ist die Weboberflaeche fuer jedes Geraet im Heimnetz offen. Den PIN bitte notieren — wird in der .env gespeichert."
    $lblPinHint.ForeColor = [System.Drawing.Color]::FromArgb(180, 120, 0)
    $pnlContent.Controls.Add($lblPinHint)

    $script:chkPin.add_CheckedChanged({ $script:txtPin1.Enabled = $script:chkPin.Checked; $script:txtPin2.Enabled = $script:chkPin.Checked })

    $btnNext.Text = "Installieren"
}

# ============================================================
# Step 3: Installation (Fresh) / Update
# ============================================================
$script:progressBar = $null
$script:logBox = $null
$script:stepLabels = @()

function Show-Installation {
    if ($script:IsUpdate) {
        $lblStep.Text = "Update - Schritt 2 von 3 - Aktualisierung"
        $steps = @("Sicherheitskopie erstellen und Konfiguration migrieren", "Docker-Images laden", "Container starten", "Backend starten", "Eingangsordner abgleichen")
    } else {
        $lblStep.Text = "Schritt 3 von 4 - Installation"
        $steps = @("Konfiguration erstellen", "Docker-Images laden", "Container starten", "Backend starten", "KI-Modelle laden")
    }

    $pnlContent.Controls.Clear()
    $btnBack.Enabled = $false; $btnNext.Visible = $false

    $script:progressBar = New-Object System.Windows.Forms.ProgressBar
    $script:progressBar.Location = [System.Drawing.Point]::new(30, 15)
    $script:progressBar.Size = [System.Drawing.Size]::new(580, 22); $script:progressBar.Style = "Continuous"
    $pnlContent.Controls.Add($script:progressBar)

    $script:stepLabels = @()
    $y = 48
    foreach ($s in $steps) {
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Location = [System.Drawing.Point]::new(30, $y); $lbl.Size = [System.Drawing.Size]::new(560, 20)
        $lbl.Text = "       $s"; $lbl.ForeColor = $cSub
        $pnlContent.Controls.Add($lbl)
        $script:stepLabels += $lbl
        $y += 22
    }

    $script:logBox = New-Object System.Windows.Forms.TextBox
    $script:logBox.Location = [System.Drawing.Point]::new(30, $y + 8)
    $script:logBox.Size = [System.Drawing.Size]::new(580, 358 - $y - 18)
    $script:logBox.Multiline = $true; $script:logBox.ScrollBars = "Vertical"
    $script:logBox.ReadOnly = $true; $script:logBox.BackColor = [System.Drawing.Color]::FromArgb(250, 250, 250)
    $script:logBox.Font = New-Object System.Drawing.Font("Consolas", 8.5)
    $pnlContent.Controls.Add($script:logBox)

    $script:Phase = 0
    Run-Phase
}

function Set-StepStatus {
    param([int]$Idx, [string]$Status)
    if ($Idx -ge $script:stepLabels.Count) { return }
    $text = $script:stepLabels[$Idx].Text.TrimStart()
    if ($text.Length -gt 2 -and $text[1] -eq ' ') { $text = $text.Substring(2).TrimStart() }
    switch ($Status) {
        "wait"   { $script:stepLabels[$Idx].Text = "       $text"; $script:stepLabels[$Idx].ForeColor = $cSub }
        "active" { $script:stepLabels[$Idx].Text = "  >  $text";  $script:stepLabels[$Idx].ForeColor = $cAccent }
        "done"   { $script:stepLabels[$Idx].Text = "  +  $text";  $script:stepLabels[$Idx].ForeColor = $cOK }
        "error"  { $script:stepLabels[$Idx].Text = "  X  $text";  $script:stepLabels[$Idx].ForeColor = $cErr }
        "warn"   { $script:stepLabels[$Idx].Text = "  !  $text";  $script:stepLabels[$Idx].ForeColor = $cWarn }
        "skip"   { $script:stepLabels[$Idx].Text = "  -  $text";  $script:stepLabels[$Idx].ForeColor = $cSub }
    }
}

# --- Phases dispatcher ---
function Run-Phase {
    if ($script:IsUpdate) {
        switch ($script:Phase) {
            0 { Phase-Backup }
            1 { Phase-Pull }
            2 { Phase-Up }
            3 { Phase-Health }
            4 { Phase-MigrateConfig }
            5 { Phase-Shortcut }
        }
    } else {
        switch ($script:Phase) {
            0 { Phase-Config }
            1 { Phase-Pull }
            2 { Phase-Up }
            3 { Phase-Health }
            4 { Phase-Model }
            5 { Phase-Shortcut }
        }
    }
}

function Next-Phase {
    $script:Phase++
    if ($script:Phase -le 5) { Run-Phase } else { $script:Step = 4; Show-Complete }
}

# ---- Fresh-install phases ----

function Phase-Config {
    Set-StepStatus 0 "active"
    Log "Erstelle Konfiguration..."

    $envPath = Join-Path $script:ProjectDir ".env"

    # .env erstellen (Fresh Install - niemals Reinstall hier, der geht ueber Update)
    $env = "# Zettelwirtschaft - Konfiguration`n"
    $env += "FRONTEND_PORT=$($script:Config.Port)`n"
    $env += "OLLAMA_BASE_URL=http://ollama:11434`n"
    $env += "OLLAMA_MODEL=$($script:Config.Model)`n"
    $env += "UPLOAD_DIR=./data/uploads`n"
    $env += "ARCHIVE_DIR=./data/archive`n"
    if ($script:Config.WatchEnabled) { $env += "WATCH_DIR=./data/watch`n" }
    # PIN-Schutz: immer explizit, damit kein Default-Fallback im Backend greift.
    if ($script:Config.PinEnabled) {
        $env += "PIN_ENABLED=true`nPIN_CODE=$($script:Config.PinCode)`n"
    } else {
        $env += "PIN_ENABLED=false`n"
    }
    $env += "OCR_LANGUAGES=deu+eng`n"
    $env += "LOG_LEVEL=INFO`n"
    $env += "`n# ChromaDB (RAG / KI-Assistent)`n"
    $env += "CHROMADB_HOST=chromadb`n"
    $env += "EMBEDDING_MODEL=$($script:Config.EmbeddingModel)`n"
    # Fernet-Schluessel fuer E-Mail-Passwoerter automatisch generieren
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $emailKeyBytes = New-Object byte[] 32
    $rng.GetBytes($emailKeyBytes)
    $emailKey = [Convert]::ToBase64String($emailKeyBytes).Replace('+','-').Replace('/','_')
    $env += "`n# E-Mail-Verschluesselung (automatisch generiert, nicht aendern!)`n"
    $env += "EMAIL_ENCRYPTION_KEY=$emailKey`n"
    [System.IO.File]::WriteAllText($envPath, $env)
    Log "  .env erstellt (mit EMAIL_ENCRYPTION_KEY)"

    # data-Verzeichnisse anlegen
    @("data", "data\uploads", "data\archive") | ForEach-Object {
        $d = Join-Path $script:ProjectDir $_
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
    if ($script:Config.WatchEnabled) {
        $watchDir = Join-Path $script:ProjectDir "data\watch"
        if (-not (Test-Path $watchDir)) { New-Item -ItemType Directory -Path $watchDir -Force | Out-Null }
        Log "  Eingangsordner: $watchDir"
    }

    # GPU override
    if ($script:Checks.GPU) {
        $ov = "services:`n  ollama:`n    deploy:`n      resources:`n        reservations:`n          devices:`n            - driver: nvidia`n              count: all`n              capabilities: [gpu]`n"
        [System.IO.File]::WriteAllText((Join-Path $script:ProjectDir "docker-compose.override.yml"), $ov)
        Log "  GPU-Beschleunigung aktiviert"
    }

    $script:progressBar.Value = 10
    Set-StepStatus 0 "done"
    Next-Phase
}

# ---- Update phases ----

function Phase-Backup {
    Set-StepStatus 0 "active"
    Log "Erstelle Sicherheitskopie..."

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $script:BackupDir = Join-Path $script:ProjectDir "data\backups\pre-update_$timestamp"
    New-Item -ItemType Directory -Path $script:BackupDir -Force | Out-Null

    $dbPath  = Join-Path $script:ProjectDir "data\zettelwirtschaft.db"
    $envPath = Join-Path $script:ProjectDir ".env"

    if (Test-Path $dbPath) {
        Copy-Item $dbPath (Join-Path $script:BackupDir "zettelwirtschaft.db") -Force
        Log "  Datenbank gesichert"
    }
    if (Test-Path $envPath) {
        Copy-Item $envPath (Join-Path $script:BackupDir ".env") -Force
        Log "  Konfiguration gesichert"
    }

    # .env VOR dem Container-Neustart migrieren (nach ihrer Sicherung): der
    # Backend-Container liest sie nur beim Erstellen (env_file). Spaeter
    # eingetragene Werte griffen erst beim naechsten Neustart — bis dahin liefe
    # das neue Backend z.B. mit den neuen Modell-Defaults (Download mehrerer GB,
    # Neuaufbau des Vektor-Index in ein anderes Suchmodell).
    Log "Pruefe Konfiguration auf neue Variablen..."
    Apply-ConfigMigrations -FromVersion $script:InstalledVersion -EnvPath $envPath
    foreach ($key in @(Add-ZwLegacyModelDefaults -EnvPath $envPath -InstalledVersion $script:InstalledVersion)) {
        Log "  $key mit bisherigem Standard festgeschrieben (neue Standards ab v1.6 gelten nur für Neuinstallationen)"
    }

    Log "  Backup-Verzeichnis: $($script:BackupDir)"
    $script:progressBar.Value = 10

    # Vollstaendiges Backup (DB + Dokumente) direkt im laufenden Backend-Container.
    # Nicht ueber die HTTP-API: die verlangt bei aktivem PIN-Schutz (Default)
    # eine Anmeldung und antwortete mit 401. Fehlschlag ist nicht fatal (lokale
    # Kopie oben existiert) -> Warnung ueber den Timer-Handler.
    Log "Erstelle vollständiges Backup im Container (Datenbank + Dokumente)..."
    $dir = $script:ProjectDir
    $script:Job = Start-Job -ScriptBlock {
        param($d); Set-Location $d
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $out = & cmd /c "docker compose exec -T backend python -m app.entrypoint --backup --full 2>&1"
        $rc = $LASTEXITCODE
        if ($rc -ne 0) {
            throw "Vollständiges Backup fehlgeschlagen (Exit $rc, Backend läuft evtl. nicht) - die lokale Sicherheitskopie bleibt erhalten."
        }
        $last = @($out) | Where-Object { "$_".Trim() } | Select-Object -Last 1
        Write-Output "Backup erstellt: $last"
    } -ArgumentList $dir
    $timer.Start()
}

function Phase-MigrateConfig {
    Set-StepStatus 4 "active"
    # Neue Variablen migriert Phase-Backup (vor dem Container-Start).
    Log "Gleiche Eingangsordner ab..."

    $envPath = Join-Path $script:ProjectDir ".env"

    # Das Update hat keine Konfigurationsseite: die bestehende .env ist die
    # einzige Quelle fuer den Eingangsordner. Frueher wurde hier gegen
    # $script:Config.WatchEnabled abgeglichen — das ist im Update-Pfad immer
    # der Default $false und hat einen aktiven Eingangsordner auskommentiert.
    # Ist WATCH_DIR aktiv, nur den Ordner sicherstellen.
    if ((Test-Path $envPath) -and ((Get-Content $envPath -Raw) -match '(?m)^\s*WATCH_DIR\s*=')) {
        $watchDir = Join-Path $script:ProjectDir "data\watch"
        if (-not (Test-Path $watchDir)) {
            New-Item -ItemType Directory -Path $watchDir -Force | Out-Null
            Log "  Eingangsordner angelegt: $watchDir"
        }
    }

    $script:progressBar.Value = 95
    Set-StepStatus 4 "done"
    Next-Phase
}

function Apply-ConfigMigrations {
    param([string]$FromVersion, [string]$EnvPath)

    if (-not (Test-Path $EnvPath)) { return }
    $envContent = Get-Content $EnvPath -Raw
    $changed = $false

    # Vorabversionen wie "1.6.0-dev" oder "1.6.0-rc1": [version] kennt keinen
    # Suffix und warf hier — nur den numerischen Teil vergleichen. Nicht
    # parsebar -> wie unbekannte Version behandeln (alle Migrationen; sie sind
    # idempotent, weil nur fehlende aktive Keys ergaenzt werden).
    $from = $null
    if ($FromVersion) {
        $parsed = $null
        if ([version]::TryParse(($FromVersion.Trim() -split '-', 2)[0], [ref]$parsed)) { $from = $parsed }
    }

    foreach ($ver in $script:ConfigMigrations.Keys) {
        # Nur Versionen anwenden die neuer sind als die installierte
        if ($from -and [version]$ver -le $from) { continue }

        $vars = $script:ConfigMigrations[$ver]
        foreach ($varName in $vars.Keys) {
            # Nur aktive Zeilen zaehlen: eine auskommentierte setzt den Wert
            # nicht, das Backend nahme sonst seinen (neuen) Default.
            if ($envContent -notmatch "(?m)^\s*$([regex]::Escape($varName))\s*=") {
                $comment = $vars[$varName].comment
                $value   = $vars[$varName].value
                $envContent += "`n$comment`n$varName=$value"
                $changed = $true
                Log "  $varName hinzugefuegt (neu in v$ver)"
            }
        }
    }

    if ($changed) {
        [System.IO.File]::WriteAllText($EnvPath, $envContent)
        Log "  .env aktualisiert"
    } else {
        Log "  Keine neuen Variablen erforderlich"
    }
}

# ---- Shared phases ----

function Phase-Pull {
    Set-StepStatus 1 "active"
    if ($script:HasSources) { Set-StepStatus 1 "skip"; Next-Phase; return }
    Log "Lade Docker-Images herunter..."
    $script:progressBar.Style = "Marquee"
    $script:progressBar.MarqueeAnimationSpeed = 30
    $dir = $script:ProjectDir
    $script:Job = Start-Job -ScriptBlock {
        param($d); Set-Location $d
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        & cmd /c "chcp 65001 >nul & docker compose pull 2>&1" | ForEach-Object {
            # Bei \r-basiertem Output: letzten nicht-leeren Teil nehmen (aktuellster Stand)
            $parts = "$_" -split "`r"
            $last = $parts | ForEach-Object {
                ($_ -replace '\x1b\[[0-9;?]*[a-zA-Z]', '' -replace '[^\x20-\x7E]', '').Trim()
            } | Where-Object { $_.Length -gt 0 } | Select-Object -Last 1
            if (-not $last) { return }
            # Intermediate Downloading/Extracting-Fortschrittszeilen unterdrücken
            if ($last -match '^[a-f0-9]{12}\s+(Downloading|Extracting)\s+\[') { return }
            Write-Output $last
        }
        if ($LASTEXITCODE -ne 0) { throw "Docker pull fehlgeschlagen (Exit $LASTEXITCODE)" }
    } -ArgumentList $dir
    $timer.Start()
}

function Phase-Up {
    Set-StepStatus 2 "active"
    Log "Starte Container..."
    $script:progressBar.Value = if ($script:IsUpdate) { 55 } else { 50 }
    $dir = $script:ProjectDir
    $src = $script:HasSources
    $script:Job = Start-Job -ScriptBlock {
        param($d, $s); Set-Location $d
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $cmd = if ($s) { "docker compose up --build -d" } else { "docker compose up -d" }
        & cmd /c "chcp 65001 >nul & $cmd 2>&1" | ForEach-Object {
            $parts = "$_" -split "`r"
            foreach ($part in $parts) {
                $clean = $part -replace '\x1b\[[0-9;?]*[a-zA-Z]', '' -replace '[^\x20-\x7E]', ''
                $clean = $clean.Trim()
                if ($clean.Length -gt 0) { Write-Output $clean }
            }
        }
        if ($LASTEXITCODE -ne 0) { throw "Container-Start fehlgeschlagen (Exit $LASTEXITCODE)" }
    } -ArgumentList $dir, $src
    $timer.Start()
}

function Phase-Health {
    Set-StepStatus 3 "active"
    Log "Warte auf Backend..."
    $script:progressBar.Value = if ($script:IsUpdate) { 75 } else { 70 }
    # Gemeinsamer Helfer wie start.bat/update.bat: prueft ueber nginx
    # (FRONTEND_PORT aus .env bzw. HTTPS, wenn das SSL-Overlay aktiv ist), nicht
    # direkt auf 8000. Eigener Prozess, damit die Ausgabe im Log landet.
    $script:Job = Start-Job -ArgumentList $script:ProjectDir -ScriptBlock {
        param($d); Set-Location $d
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $d "wait-health.ps1") -ProjectDir $d -TimeoutSeconds 120 2>&1 |
            ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) {
            throw "Zettelwirtschaft antwortet nach 120 s nicht. Prüfen mit: docker compose logs backend"
        }
    }
    $timer.Start()
}

function Phase-Model {
    Set-StepStatus 4 "active"
    # Beide Modelle per exaktem Tag; das (kleinere) Suchmodell zuerst. Das
    # Backend wuerde fehlende Modelle beim Start auch selbst laden — hier sieht
    # der Benutzer den Fortschritt.
    $models = @(@($script:Config.EmbeddingModel, $script:Config.Model) | Where-Object { $_ } | Select-Object -Unique)
    Log "Pruefe KI-Modelle: $($models -join ', ')..."
    $script:progressBar.Value = 85
    $dir = $script:ProjectDir
    $script:Job = Start-Job -ScriptBlock {
        param($d, [string[]]$mods); Set-Location $d
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

        # Pruefen ob GENAU dieses Modell (inkl. Tag) vorhanden ist. Ollama sucht
        # Modelle per exaktem Tag: "gemma4:12b" ersetzt "gemma4:12b-it-qat"
        # nicht (sonst 404 bei jeder Analyse). Ohne Tag meint Ollama ":latest".
        $installed = @(& cmd /c "docker compose exec -T ollama ollama list 2>nul" |
            Select-Object -Skip 1 |
            ForEach-Object { ("$_".Trim() -split '\s+')[0] } |
            Where-Object { $_ })
        $failed = @()
        foreach ($mod in $mods) {
            $wanted = if ($mod -match ':') { $mod } else { "${mod}:latest" }
            if ($installed -contains $wanted) {
                Write-Output "Modell '$wanted' ist bereits vorhanden, kein Download nötig."
                continue
            }

            Write-Output "Lade KI-Modell '$mod' (je nach Modell 0,6-7 GB, kann einige Minuten dauern)..."
            $lastPct = @{}
            & cmd /c "chcp 65001 >nul & docker compose exec ollama ollama pull $mod 2>&1" | ForEach-Object {
                $parts = "$_" -split "`r"
                foreach ($part in $parts) {
                    $clean = $part -replace '\x1b\[[0-9;?]*[a-zA-Z]', '' -replace '[^\x20-\x7E]', ''
                    $clean = $clean.Trim()
                    if ($clean -match 'pulling\s+([a-f0-9]+).*?(\d+)%') {
                        $hash = $Matches[1].Substring(0, [Math]::Min(12, $Matches[1].Length))
                        $pct  = [int]$Matches[2]
                        $prev = if ($lastPct.ContainsKey($hash)) { $lastPct[$hash] } else { -10 }
                        if ($pct -ge ($prev + 10) -or $pct -ge 100) {
                            $filled = [Math]::Floor($pct / 5)
                            $bar  = "[" + ("#" * $filled) + ("." * (20 - $filled)) + "]"
                            $size = ""
                            if ($clean -match '([\d.]+\s*[KMGT]?B\s*/\s*[\d.]+\s*[KMGT]?B)') { $size = "  " + $Matches[1] }
                            Write-Output "$hash  $bar  ${pct}%$size"
                            $lastPct[$hash] = $pct
                        }
                    } elseif ($clean.Length -gt 2 -and $clean -notmatch '^\s*\d+%\s*$') {
                        Write-Output $clean
                    }
                }
            }
            if ($LASTEXITCODE -ne 0) { $failed += $mod }
        }
        if ($failed.Count -gt 0) {
            $cmds = ($failed | ForEach-Object { "docker compose exec ollama ollama pull $_" }) -join " / "
            throw "Download fehlgeschlagen: $($failed -join ', '). Später erneut: $cmds"
        }
    } -ArgumentList $dir, $models
    $timer.Start()
}

function Phase-Shortcut {
    Log "Erstelle Desktop-Verknuepfung..."
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Zettelwirtschaft.lnk")
        $sc.TargetPath = Join-Path $script:ProjectDir "start.bat"
        $sc.WorkingDirectory = $script:ProjectDir
        $sc.Description = "Zettelwirtschaft"; $sc.IconLocation = "shell32.dll,21"
        $sc.Save()
        Log "  Desktop-Verknuepfung erstellt"
    } catch { Log "  Verknuepfung konnte nicht erstellt werden" }

    # VERSION-Datei schreiben: Installer-Paket-Version ist massgeblich
    $dataDir = Join-Path $script:ProjectDir "data"
    if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir -Force | Out-Null }
    [System.IO.File]::WriteAllText($instVersionFile, $script:NewVersion)
    [System.IO.File]::WriteAllText((Join-Path $dataDir ".install-path"), $script:ProjectDir)
    Log "  Version $($script:NewVersion) gespeichert"

    $script:progressBar.Value = 100
    Next-Phase
}

# Timer: poll async jobs
$timer.add_Tick({
    if (-not $script:Job) { return }
    $output = @(Receive-Job $script:Job 2>$null)
    foreach ($line in $output) { $t = "$line".Trim(); if ($t) { Log "  $t" } }
    if ($script:Job.State -eq "Running") { return }

    $timer.Stop()
    $failed = $script:Job.State -eq "Failed"
    $errMsg = ""
    if ($failed) { try { $errMsg = $script:Job.ChildJobs[0].JobStateInfo.Reason.Message } catch { $errMsg = "Unbekannter Fehler" } }
    Remove-Job $script:Job -Force; $script:Job = $null

    # Marquee nach Phase-Pull zurücksetzen
    if ($script:Phase -eq 1) {
        $script:progressBar.Style = "Continuous"
        $script:progressBar.MarqueeAnimationSpeed = 0
    }
    switch ($script:Phase) {
        1 { $script:progressBar.Value = if ($script:IsUpdate) { 50 } else { 45 } }
        2 { $script:progressBar.Value = if ($script:IsUpdate) { 70 } else { 65 } }
        3 { $script:progressBar.Value = if ($script:IsUpdate) { 85 } else { 80 } }
        4 { $script:progressBar.Value = if ($script:IsUpdate) { 92 } else { 95 } }
    }

    # Fatal sind nur Image-Download (1) und Container-Start (2): ohne sie laeuft
    # nichts. Backup (0, Update), Health-Wartezeit (3) und Modell-Download (4)
    # lassen eine nutzbare Installation zurueck -> Warnung, weiter.
    $idx = $script:Phase
    if ($failed -and ($idx -eq 1 -or $idx -eq 2)) {
        Set-StepStatus $idx "error"
        Log ""; Log "FEHLER: $errMsg"; Log "Pruefe: docker compose logs"
        if ($script:BackupDir) { Log "Backup liegt in: $($script:BackupDir)" }
        $btnCancel.Text = "Schliessen"
        return
    }

    if ($failed) {
        Set-StepStatus $idx "warn"
        Log "WARNUNG: $errMsg"
        $script:Warnings += $errMsg
    } elseif ($idx -ge 0 -and $idx -le 4) {
        Set-StepStatus $idx "done"
    }
    Next-Phase
})

# ============================================================
# Step 4: Complete
# ============================================================
function Show-Complete {
    $lblStep.Text = if ($script:IsUpdate) { "Update - Schritt 3 von 3 - Abgeschlossen" } else { "Installation abgeschlossen" }
    $pnlContent.Controls.Clear()
    $btnCancel.Visible = $false; $btnNext.Visible = $true; $btnNext.Text = "Schliessen"

    $hasWarnings = $script:Warnings.Count -gt 0
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = if ($hasWarnings) {
        if ($script:IsUpdate) { "Update auf v$($script:NewVersion) abgeschlossen – mit Warnungen" } else { "Installation abgeschlossen – mit Warnungen" }
    } elseif ($script:IsUpdate) { "Update auf v$($script:NewVersion) erfolgreich!" } else { "Installation erfolgreich!" }
    $lbl.Location = [System.Drawing.Point]::new(30, 20); $lbl.Size = [System.Drawing.Size]::new(560, 35)
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 15)
    $lbl.ForeColor = if ($hasWarnings) { $cWarn } else { $cOK }
    $pnlContent.Controls.Add($lbl)

    # Gleiche URL-Erkennung wie der Health-Check (HTTPS bei aktivem SSL-Overlay)
    $url = "http://localhost:$($script:Config.Port)"
    $waitHealth = Join-Path $script:ProjectDir "wait-health.ps1"
    if (Test-Path -LiteralPath $waitHealth) {
        $detected = & $waitHealth -ProjectDir $script:ProjectDir -UrlOnly
        if ($detected) { $url = "$detected" }
    }
    $link = New-Object System.Windows.Forms.LinkLabel
    $link.Text = "Zettelwirtschaft oeffnen: $url"
    $link.Location = [System.Drawing.Point]::new(30, 65); $link.Size = [System.Drawing.Size]::new(560, 25)
    $link.Font = New-Object System.Drawing.Font("Segoe UI", 11)
    $link.add_LinkClicked({ Start-Process $url })
    $pnlContent.Controls.Add($link)

    if ($script:IsUpdate -and $script:BackupDir) {
        $lblBackup = New-Object System.Windows.Forms.Label
        $lblBackup.Location = [System.Drawing.Point]::new(30, 100); $lblBackup.Size = [System.Drawing.Size]::new(560, 40)
        $lblBackup.Text = "Sicherheitskopie:  $($script:BackupDir)`n(Kann nach erfolgreicher Pruefung geloescht werden)"
        $lblBackup.ForeColor = $cSub; $lblBackup.Font = New-Object System.Drawing.Font("Consolas", 8.5)
        $pnlContent.Controls.Add($lblBackup)
    }

    $info = New-Object System.Windows.Forms.Label
    $infoY = if ($script:IsUpdate -and $script:BackupDir) { 155 } else { 110 }
    $info.Location = [System.Drawing.Point]::new(30, $infoY); $info.Size = [System.Drawing.Size]::new(560, (282 - $infoY))
    $info.Font = New-Object System.Drawing.Font("Consolas", 9)
    if ($hasWarnings) {
        # Nachzuholende Schritte statt der Befehlsliste (Meldungen enthalten den
        # jeweiligen Befehl zum erneuten Versuch).
        $info.ForeColor = $cWarn
        $info.Text = "Bitte nachholen:`r`n`r`n" + (($script:Warnings | ForEach-Object { "- $_" }) -join "`r`n`r`n")
    } else {
        $info.Text = "Nuetzliche Befehle:`r`n`r`n  start.bat        System starten + Browser oeffnen`r`n  stop.bat         System stoppen`r`n  update.bat       System aktualisieren`r`n  uninstall.bat    System deinstallieren"
    }
    $pnlContent.Controls.Add($info)

    $script:chkBrowser = New-Object System.Windows.Forms.CheckBox
    $script:chkBrowser.Location = [System.Drawing.Point]::new(30, 290); $script:chkBrowser.Size = [System.Drawing.Size]::new(300, 22)
    $script:chkBrowser.Text = "Browser jetzt oeffnen"; $script:chkBrowser.Checked = $true
    $pnlContent.Controls.Add($script:chkBrowser)
}

# ============================================================
# Button Handlers
# ============================================================
$btnNext.add_Click({
    switch ($script:Step) {
        0 {
            if ($script:ExistingInstall) {
                $script:Step = 10
                $btnBack.Enabled = $true
                Show-Migration
            } else {
                $script:Step = 1
                $btnBack.Enabled = $true
                Show-Prerequisites
            }
        }
        10 {
            # Migration-Schritt: Weiter-Button wird durch Panel-Klick aktiviert
            # Wenn IsUpdate gesetzt: direkt zur Installation
            if ($script:IsUpdate) {
                $script:Step = 3; Show-Installation
            } else {
                $script:Step = 2; $btnNext.Text = "Installieren"; Show-Configuration
            }
        }
        1 {
            if (-not $script:Checks.DockerRun) {
                try { $null = docker info 2>&1; $script:Checks.DockerRun = ($LASTEXITCODE -eq 0) } catch {}
                if (-not $script:Checks.DockerRun) {
                    [System.Windows.Forms.MessageBox]::Show("Docker Desktop laeuft nicht.", "Fehler", "OK", "Warning"); return
                }
            }
            if ($script:IsUpdate) {
                # Bei Update: direkt zur Installation (Konfiguration wird migriert)
                $script:Step = 3; Show-Installation
            } else {
                $script:Step = 2; $btnNext.Text = "Installieren"; Show-Configuration
            }
        }
        2 {
            $p = 0
            if (-not [int]::TryParse($script:txtPort.Text, [ref]$p) -or $p -lt 1 -or $p -gt 65535) {
                [System.Windows.Forms.MessageBox]::Show("Ungueltiger Port (1-65535).", "Fehler", "OK", "Warning"); return
            }
            if ($script:chkPin.Checked) {
                if ($script:txtPin1.Text.Length -lt 4) {
                    [System.Windows.Forms.MessageBox]::Show("PIN muss mind. 4 Zeichen haben.", "Fehler", "OK", "Warning"); return
                }
                if ($script:txtPin1.Text -ne $script:txtPin2.Text) {
                    [System.Windows.Forms.MessageBox]::Show("PINs stimmen nicht ueberein.", "Fehler", "OK", "Warning"); return
                }
            }
            $script:Config.Port = $p
            $script:Config.WatchEnabled = $script:chkWatch.Checked
            $script:Config.Model = $script:cmbModel.SelectedItem
            $script:Config.EmbeddingModel = $script:cmbEmbedding.SelectedItem
            $script:Config.PinEnabled = $script:chkPin.Checked
            if ($script:chkPin.Checked) { $script:Config.PinCode = $script:txtPin1.Text }
            $script:Step = 3; Show-Installation
        }
        4 {
            if ($script:chkBrowser -and $script:chkBrowser.Checked) { Start-Process "http://localhost:$($script:Config.Port)" }
            $form.Close()
        }
    }
})

$btnBack.add_Click({
    switch ($script:Step) {
        1  { $script:Step = 0; $btnBack.Enabled = $false; $btnNext.Text = "Weiter"; $btnNext.Visible = $true; Show-Welcome }
        10 { $script:Step = 0; $btnBack.Enabled = $false; $btnNext.Text = "Weiter"; $btnNext.Visible = $true; Show-Welcome }
        2  {
            if ($script:ExistingInstall) {
                $script:Step = 10; $btnNext.Text = "Weiter"; Show-Migration
            } else {
                $script:Step = 1; $btnNext.Text = "Weiter"; Show-Prerequisites
            }
        }
    }
})

$btnCancel.add_Click({
    if ($script:Job) { $timer.Stop(); Stop-Job $script:Job -EA SilentlyContinue; Remove-Job $script:Job -Force -EA SilentlyContinue; $script:Job = $null }
    $form.Close()
})

$form.add_FormClosing({
    param($s, $e)
    if ($script:Job -and $script:Job.State -eq "Running") {
        $r = [System.Windows.Forms.MessageBox]::Show("Installation laeuft noch. Abbrechen?", "Abbrechen", "YesNo", "Warning")
        if ($r -eq "No") { $e.Cancel = $true; return }
        $timer.Stop(); Stop-Job $script:Job -EA SilentlyContinue; Remove-Job $script:Job -Force -EA SilentlyContinue
    }
})

# --- Start ---
Show-Welcome
[System.Windows.Forms.Application]::Run($form)
