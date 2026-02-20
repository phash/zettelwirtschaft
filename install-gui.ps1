# Zettelwirtschaft - Grafischer Windows-Installer
$ErrorActionPreference = "Stop"
$script:ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $script:ProjectDir

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# --- State ---
$script:Step = 0
$script:Config = @{ Port=8080; WatchEnabled=$false; WatchDir=""; Model="llama3.2"; PinEnabled=$false; PinCode="" }
$script:Checks = @{ DockerOK=$false; DockerRun=$false; GPU=$false; GPUName=""; RAM=0; FreeGB=0 }
$script:Job = $null
$script:Phase = 0
$script:HasSources = Test-Path (Join-Path $script:ProjectDir "backend")

# --- Colors ---
$cAccent  = [System.Drawing.Color]::FromArgb(0, 150, 136)
$cHeader  = [System.Drawing.Color]::FromArgb(38, 50, 56)
$cOK      = [System.Drawing.Color]::FromArgb(56, 142, 60)
$cWarn    = [System.Drawing.Color]::FromArgb(245, 124, 0)
$cErr     = [System.Drawing.Color]::FromArgb(211, 47, 47)
$cInfo    = [System.Drawing.Color]::FromArgb(25, 118, 210)
$cSub     = [System.Drawing.Color]::FromArgb(117, 117, 117)
$cBorder  = [System.Drawing.Color]::FromArgb(224, 224, 224)

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

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Willkommen beim Installationsassistenten"
    $lbl.Location = [System.Drawing.Point]::new(30, 20)
    $lbl.Size = [System.Drawing.Size]::new(560, 30)
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 13)
    $pnlContent.Controls.Add($lbl)

    # Reinstall-Erkennung
    $envExists = Test-Path (Join-Path $script:ProjectDir ".env")
    $dbExists = Test-Path (Join-Path $script:ProjectDir "data\zettelwirtschaft.db")
    $isReinstall = $envExists -and $dbExists

    if ($isReinstall) {
        $hint = New-Object System.Windows.Forms.Label
        $hint.Location = [System.Drawing.Point]::new(30, 55)
        $hint.Size = [System.Drawing.Size]::new(560, 40)
        $hint.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
        $hint.ForeColor = $cOK
        $hint.Text = "Bestehende Installation erkannt.`nDaten und Konfiguration werden beibehalten."
        $pnlContent.Controls.Add($hint)
    }

    $descY = if ($isReinstall) { 100 } else { 60 }
    $desc = New-Object System.Windows.Forms.Label
    $desc.Location = [System.Drawing.Point]::new(30, $descY)
    $desc.Size = [System.Drawing.Size]::new(560, 280)
    $desc.Text = "Zettelwirtschaft ist ein lokales Dokumentenmanagementsystem`nfuer Privathaushalte.`n`nRechnungen, Belege und Dokumente werden per Scanner oder`nSmartphone erfasst, automatisch durch KI analysiert,`nkategorisiert und durchsuchbar archiviert.`n`nFolgende Komponenten werden installiert:`n`n    Backend-Server (Dokumentenverarbeitung + API)`n    Frontend (Weboberflaeche)`n    Ollama (lokale KI fuer Dokumentenanalyse)`n    LLM-Sprachmodell (ca. 2-4 GB Download)`n`nVoraussetzungen:`n    Docker Desktop installiert und gestartet`n    Mindestens 8 GB RAM empfohlen`n    Mindestens 10 GB freier Speicherplatz"
    $pnlContent.Controls.Add($desc)
}

# ============================================================
# Step 1: Prerequisites
# ============================================================
function Show-Prerequisites {
    $lblStep.Text = "Schritt 1 von 4 - Voraussetzungen"
    $pnlContent.Controls.Clear()
    $btnNext.Enabled = $true
    $y = 20

    # Docker installed?
    try { $null = Get-Command docker -ErrorAction Stop; $script:Checks.DockerOK = $true } catch { $script:Checks.DockerOK = $false }
    Add-CheckItem "Docker installiert" $script:Checks.DockerOK ([ref]$y)

    # Docker running?
    if ($script:Checks.DockerOK) {
        try { $null = docker info 2>&1; $script:Checks.DockerRun = ($LASTEXITCODE -eq 0) } catch { $script:Checks.DockerRun = $false }
    }
    Add-CheckItem "Docker Desktop laeuft" $script:Checks.DockerRun ([ref]$y) $(if (-not $script:Checks.DockerRun) { "Fehler" })

    # RAM
    $script:Checks.RAM = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $ramOK = $script:Checks.RAM -ge 8
    Add-CheckItem "$($script:Checks.RAM) GB RAM" $ramOK ([ref]$y) $(if (-not $ramOK) { "Warnung" })

    # Disk
    $drive = (Get-Item $script:ProjectDir).PSDrive
    $script:Checks.FreeGB = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB)
    $diskOK = $script:Checks.FreeGB -ge 10
    Add-CheckItem "$($script:Checks.FreeGB) GB freier Speicherplatz" $diskOK ([ref]$y) $(if (-not $diskOK) { "Warnung" })

    # GPU
    try {
        $gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
        if ($gpu) {
            $script:Checks.GPU = $true; $script:Checks.GPUName = $gpu.Name
            Add-InfoItem "NVIDIA GPU: $($gpu.Name)" ([ref]$y)
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
    $lbl.Location = [System.Drawing.Point]::new(30, $y+3); $lbl.Size = [System.Drawing.Size]::new(120, 22); $lbl.Text = "Frontend-Port:"
    $pnlContent.Controls.Add($lbl)
    $script:txtPort = New-Object System.Windows.Forms.TextBox
    $script:txtPort.Location = [System.Drawing.Point]::new(160, $y); $script:txtPort.Size = [System.Drawing.Size]::new(80, 26)
    $script:txtPort.Text = $script:Config.Port.ToString()
    $pnlContent.Controls.Add($script:txtPort)
    $y += 40

    # Watch folder
    $script:chkWatch = New-Object System.Windows.Forms.CheckBox
    $script:chkWatch.Location = [System.Drawing.Point]::new(30, $y); $script:chkWatch.Size = [System.Drawing.Size]::new(560, 22)
    $script:chkWatch.Text = "Watch-Ordner aktivieren (automatischer Import)"
    $script:chkWatch.Checked = $script:Config.WatchEnabled
    $pnlContent.Controls.Add($script:chkWatch)
    $y += 28

    $script:txtWatch = New-Object System.Windows.Forms.TextBox
    $script:txtWatch.Location = [System.Drawing.Point]::new(50, $y); $script:txtWatch.Size = [System.Drawing.Size]::new(430, 26)
    $script:txtWatch.Text = if ($script:Config.WatchDir) { $script:Config.WatchDir } else { Join-Path $script:ProjectDir "data\watch" }
    $script:txtWatch.Enabled = $script:chkWatch.Checked
    $pnlContent.Controls.Add($script:txtWatch)

    $btnBrowse = New-Object System.Windows.Forms.Button
    $btnBrowse.Location = [System.Drawing.Point]::new(486, $y); $btnBrowse.Size = [System.Drawing.Size]::new(80, 26)
    $btnBrowse.Text = "Waehlen..."; $btnBrowse.FlatStyle = "Flat"; $btnBrowse.FlatAppearance.BorderColor = $cBorder
    $btnBrowse.add_Click({ $fbd = New-Object System.Windows.Forms.FolderBrowserDialog; if ($fbd.ShowDialog() -eq "OK") { $script:txtWatch.Text = $fbd.SelectedPath } })
    $pnlContent.Controls.Add($btnBrowse)
    $script:chkWatch.add_CheckedChanged({ $script:txtWatch.Enabled = $script:chkWatch.Checked; $btnBrowse.Enabled = $script:chkWatch.Checked })
    $y += 45

    # LLM Model
    $lbl2 = New-Object System.Windows.Forms.Label
    $lbl2.Location = [System.Drawing.Point]::new(30, $y+3); $lbl2.Size = [System.Drawing.Size]::new(120, 22); $lbl2.Text = "LLM-Modell:"
    $pnlContent.Controls.Add($lbl2)
    $script:cmbModel = New-Object System.Windows.Forms.ComboBox
    $script:cmbModel.Location = [System.Drawing.Point]::new(160, $y); $script:cmbModel.Size = [System.Drawing.Size]::new(160, 26)
    $script:cmbModel.DropDownStyle = "DropDownList"
    $script:cmbModel.Items.AddRange(@("llama3.2", "llama3.1", "mistral"))
    $script:cmbModel.SelectedItem = if ($script:Checks.RAM -gt 16) { "llama3.1" } else { "llama3.2" }
    $pnlContent.Controls.Add($script:cmbModel)
    $lblMI = New-Object System.Windows.Forms.Label
    $lblMI.Location = [System.Drawing.Point]::new(330, $y+3); $lblMI.Size = [System.Drawing.Size]::new(260, 22); $lblMI.ForeColor = $cSub
    $lblMI.Text = if ($script:Checks.RAM -gt 16) { "Empfohlen: llama3.1 (>16 GB)" } else { "Empfohlen: llama3.2" }
    $pnlContent.Controls.Add($lblMI)
    $y += 50

    # Separator
    $sep = New-Object System.Windows.Forms.Label
    $sep.Location = [System.Drawing.Point]::new(30, $y); $sep.Size = [System.Drawing.Size]::new(560, 1); $sep.BorderStyle = "Fixed3D"
    $pnlContent.Controls.Add($sep)
    $y += 15

    # PIN
    $script:chkPin = New-Object System.Windows.Forms.CheckBox
    $script:chkPin.Location = [System.Drawing.Point]::new(30, $y); $script:chkPin.Size = [System.Drawing.Size]::new(560, 22)
    $script:chkPin.Text = "PIN-Schutz aktivieren"
    $pnlContent.Controls.Add($script:chkPin)
    $y += 32

    $lp1 = New-Object System.Windows.Forms.Label
    $lp1.Location = [System.Drawing.Point]::new(50, $y+3); $lp1.Size = [System.Drawing.Size]::new(100, 22); $lp1.Text = "PIN:"
    $pnlContent.Controls.Add($lp1)
    $script:txtPin1 = New-Object System.Windows.Forms.TextBox
    $script:txtPin1.Location = [System.Drawing.Point]::new(160, $y); $script:txtPin1.Size = [System.Drawing.Size]::new(150, 26)
    $script:txtPin1.UseSystemPasswordChar = $true; $script:txtPin1.Enabled = $false
    $pnlContent.Controls.Add($script:txtPin1)
    $y += 32

    $lp2 = New-Object System.Windows.Forms.Label
    $lp2.Location = [System.Drawing.Point]::new(50, $y+3); $lp2.Size = [System.Drawing.Size]::new(100, 22); $lp2.Text = "Bestaetigen:"
    $pnlContent.Controls.Add($lp2)
    $script:txtPin2 = New-Object System.Windows.Forms.TextBox
    $script:txtPin2.Location = [System.Drawing.Point]::new(160, $y); $script:txtPin2.Size = [System.Drawing.Size]::new(150, 26)
    $script:txtPin2.UseSystemPasswordChar = $true; $script:txtPin2.Enabled = $false
    $pnlContent.Controls.Add($script:txtPin2)

    $script:chkPin.add_CheckedChanged({ $script:txtPin1.Enabled = $script:chkPin.Checked; $script:txtPin2.Enabled = $script:chkPin.Checked })
}

# ============================================================
# Step 3: Installation
# ============================================================
$script:progressBar = $null
$script:logBox = $null
$script:stepLabels = @()

function Show-Installation {
    $lblStep.Text = "Schritt 3 von 4 - Installation"
    $pnlContent.Controls.Clear()
    $btnBack.Enabled = $false; $btnNext.Visible = $false

    $script:progressBar = New-Object System.Windows.Forms.ProgressBar
    $script:progressBar.Location = [System.Drawing.Point]::new(30, 15)
    $script:progressBar.Size = [System.Drawing.Size]::new(580, 22); $script:progressBar.Style = "Continuous"
    $pnlContent.Controls.Add($script:progressBar)

    $steps = @("Konfiguration erstellen", "Docker-Images laden", "Container starten", "Backend starten", "LLM-Modell laden")
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
    $text = $script:stepLabels[$Idx].Text.TrimStart()
    # Remove any leading status chars
    if ($text.Length -gt 2 -and $text[1] -eq ' ') { $text = $text.Substring(2).TrimStart() }
    switch ($Status) {
        "wait"   { $script:stepLabels[$Idx].Text = "       $text"; $script:stepLabels[$Idx].ForeColor = $cSub }
        "active" { $script:stepLabels[$Idx].Text = "  >  $text"; $script:stepLabels[$Idx].ForeColor = $cAccent }
        "done"   { $script:stepLabels[$Idx].Text = "  +  $text"; $script:stepLabels[$Idx].ForeColor = $cOK }
        "error"  { $script:stepLabels[$Idx].Text = "  X  $text"; $script:stepLabels[$Idx].ForeColor = $cErr }
        "skip"   { $script:stepLabels[$Idx].Text = "  -  $text"; $script:stepLabels[$Idx].ForeColor = $cSub }
    }
}

# --- Installation Phases ---
function Run-Phase {
    switch ($script:Phase) {
        0 { Phase-Config }
        1 { Phase-Pull }
        2 { Phase-Up }
        3 { Phase-Health }
        4 { Phase-Model }
        5 { Phase-Shortcut }
    }
}

function Next-Phase {
    $script:Phase++
    if ($script:Phase -le 5) { Run-Phase } else { $script:Step = 4; Show-Complete }
}

function Phase-Config {
    Set-StepStatus 0 "active"
    Log "Erstelle Konfiguration..."

    # Reinstall-Erkennung: .env und Datenbank vorhanden?
    $envPath = Join-Path $script:ProjectDir ".env"
    $dbPath = Join-Path $script:ProjectDir "data\zettelwirtschaft.db"
    $isReinstall = (Test-Path $envPath) -and (Test-Path $dbPath)

    if ($isReinstall) {
        Log "  Bestehende Installation erkannt - Konfiguration und Daten werden beibehalten."
        Log "  .env wird nicht ueberschrieben."
    } else {
        # .env neu erstellen
        $env = "# Zettelwirtschaft - Konfiguration`n"
        $env += "FRONTEND_PORT=$($script:Config.Port)`n"
        $env += "OLLAMA_BASE_URL=http://ollama:11434`n"
        $env += "OLLAMA_MODEL=$($script:Config.Model)`n"
        $env += "UPLOAD_DIR=./data/uploads`n"
        $env += "ARCHIVE_DIR=./data/archive`n"
        if ($script:Config.WatchEnabled) { $env += "WATCH_DIR=./data/watch`n" }
        if ($script:Config.PinEnabled) { $env += "PIN_ENABLED=true`nPIN_CODE=$($script:Config.PinCode)`n" }
        $env += "OCR_LANGUAGES=deu+eng`n"
        $env += "LOG_LEVEL=INFO`n"
        [System.IO.File]::WriteAllText($envPath, $env)
        Log "  .env erstellt"
    }

    # data dir
    $dataDir = Join-Path $script:ProjectDir "data"
    if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir -Force | Out-Null }
    if ($script:Config.WatchEnabled -and $script:Config.WatchDir) {
        if (-not (Test-Path $script:Config.WatchDir)) { New-Item -ItemType Directory -Path $script:Config.WatchDir -Force | Out-Null }
        Log "  Watch-Ordner erstellt"
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

function Phase-Pull {
    Set-StepStatus 1 "active"
    if ($script:HasSources) { Set-StepStatus 1 "skip"; Next-Phase; return }
    Log "Lade Docker-Images herunter..."
    $script:progressBar.Value = 15
    $dir = $script:ProjectDir
    $script:Job = Start-Job -ScriptBlock {
        param($d); Set-Location $d
        & cmd /c "docker compose pull 2>&1" | ForEach-Object { Write-Output $_ }
        if ($LASTEXITCODE -ne 0) { throw "Docker pull fehlgeschlagen (Exit $LASTEXITCODE)" }
    } -ArgumentList $dir
    $timer.Start()
}

function Phase-Up {
    Set-StepStatus 2 "active"
    Log "Starte Container..."
    $script:progressBar.Value = 50
    $dir = $script:ProjectDir
    $src = $script:HasSources
    $script:Job = Start-Job -ScriptBlock {
        param($d, $s); Set-Location $d
        if ($s) { & cmd /c "docker compose up --build -d 2>&1" | ForEach-Object { Write-Output $_ } }
        else { & cmd /c "docker compose up -d 2>&1" | ForEach-Object { Write-Output $_ } }
        if ($LASTEXITCODE -ne 0) { throw "Container-Start fehlgeschlagen (Exit $LASTEXITCODE)" }
    } -ArgumentList $dir, $src
    $timer.Start()
}

function Phase-Health {
    Set-StepStatus 3 "active"
    Log "Warte auf Backend..."
    $script:progressBar.Value = 70
    $script:Job = Start-Job -ScriptBlock {
        $elapsed = 0
        while ($elapsed -lt 120) {
            try {
                $null = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
                Write-Output "Backend ist bereit."; return
            } catch {}
            Start-Sleep -Seconds 3; $elapsed += 3
            Write-Output "  Warte... ($elapsed s)"
        }
        Write-Output "Timeout - Backend antwortet noch nicht"
    }
    $timer.Start()
}

function Phase-Model {
    Set-StepStatus 4 "active"
    $m = $script:Config.Model
    Log "Lade LLM-Modell '$m' (kann 2-5 Minuten dauern)..."
    $script:progressBar.Value = 85
    $dir = $script:ProjectDir
    $script:Job = Start-Job -ScriptBlock {
        param($d, $mod); Set-Location $d
        & cmd /c "docker compose exec ollama ollama pull $mod 2>&1" | ForEach-Object { Write-Output $_ }
    } -ArgumentList $dir, $m
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

    switch ($script:Phase) {
        1 { $script:progressBar.Value = 45 }
        2 { $script:progressBar.Value = 65 }
        3 { $script:progressBar.Value = 80 }
        4 { $script:progressBar.Value = 95 }
    }

    if ($failed -and $script:Phase -le 2) {
        $idx = $script:Phase
        Set-StepStatus $idx "error"
        Log ""; Log "FEHLER: $errMsg"; Log "Pruefe: docker compose logs"
        $btnCancel.Text = "Schliessen"
        return
    }

    # Map phase to step label index
    $idx = switch ($script:Phase) { 1 {1} 2 {2} 3 {3} 4 {4} default {-1} }
    if ($idx -ge 0) { Set-StepStatus $idx "done" }
    Next-Phase
})

# ============================================================
# Step 4: Complete
# ============================================================
function Show-Complete {
    $lblStep.Text = "Installation abgeschlossen"
    $pnlContent.Controls.Clear()
    $btnCancel.Visible = $false; $btnNext.Visible = $true; $btnNext.Text = "Schliessen"

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Installation erfolgreich!"
    $lbl.Location = [System.Drawing.Point]::new(30, 20); $lbl.Size = [System.Drawing.Size]::new(560, 35)
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 15); $lbl.ForeColor = $cOK
    $pnlContent.Controls.Add($lbl)

    $url = "http://localhost:$($script:Config.Port)"
    $link = New-Object System.Windows.Forms.LinkLabel
    $link.Text = "Zettelwirtschaft oeffnen: $url"
    $link.Location = [System.Drawing.Point]::new(30, 65); $link.Size = [System.Drawing.Size]::new(560, 25)
    $link.Font = New-Object System.Drawing.Font("Segoe UI", 11)
    $link.add_LinkClicked({ Start-Process $url })
    $pnlContent.Controls.Add($link)

    $info = New-Object System.Windows.Forms.Label
    $info.Location = [System.Drawing.Point]::new(30, 110); $info.Size = [System.Drawing.Size]::new(560, 160)
    $info.Font = New-Object System.Drawing.Font("Consolas", 9)
    $info.Text = "Nuetzliche Befehle:`r`n`r`n  start.bat        System starten + Browser oeffnen`r`n  stop.bat         System stoppen`r`n  update.bat       System aktualisieren`r`n  uninstall.bat    System deinstallieren`r`n`r`nLLM-Modell: $($script:Config.Model)"
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
        0 { $script:Step = 1; $btnBack.Enabled = $true; Show-Prerequisites }
        1 {
            if (-not $script:Checks.DockerRun) {
                try { $null = docker info 2>&1; $script:Checks.DockerRun = ($LASTEXITCODE -eq 0) } catch {}
                if (-not $script:Checks.DockerRun) {
                    [System.Windows.Forms.MessageBox]::Show("Docker Desktop laeuft nicht.", "Fehler", "OK", "Warning"); return
                }
            }
            $script:Step = 2; $btnNext.Text = "Installieren"; Show-Configuration
        }
        2 {
            # Validate
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
            $script:Config.WatchDir = $script:txtWatch.Text
            $script:Config.Model = $script:cmbModel.SelectedItem
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
        1 { $script:Step = 0; $btnBack.Enabled = $false; $btnNext.Text = "Weiter"; Show-Welcome }
        2 { $script:Step = 1; $btnNext.Text = "Weiter"; Show-Prerequisites }
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
