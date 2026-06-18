# Zettelwirtschaft Update-Wizard (Native-Windows)
# GUI-Wizard zum Aktualisieren einer bestehenden Native-Installation.
# Aufruf via update-wizard.bat (erzwingt Admin + STA). Windows PowerShell 5.1.
#
# Flow:
#   1. Backup (Service laeuft noch) via Backend-Exe --backup
#   2. Service ZettelwirtschaftBackend stoppen
#   3. Dateien aktualisieren (robocopy, additiv, ohne Uninstall.exe)
#   4. Service starten (Alembic-Migrationen laufen beim Start)

[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$DryRun,
    # -Headless: keine GUI, maschinenlesbare Marker auf stdout:
    #   [STEP] <id> <running|ok|fail|warn> / [LOG] <text> / [PROGRESS] <0..100> / [DONE] <success|fail>
    # Exit 0 = success, 1 = failure.
    [switch]$Headless
)

if (-not $Headless) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()
}

$ErrorActionPreference = 'Stop'
$WizardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServiceName = 'ZettelwirtschaftBackend'

# ------------------------------------------------------------ Hilfsfunktionen
function Get-RegValue([string]$name) {
    try {
        return (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Zettelwirtschaft' -Name $name -ErrorAction Stop).$name
    } catch { return $null }
}

function Get-AppVersion([string]$dir) {
    if (-not $dir) { return 'unbekannt' }
    $vf = Join-Path $dir 'VERSION'
    if (Test-Path $vf) {
        # Get-Content -Raw liefert auf einer 0-Byte-Datei $null -> .Trim() wuerde
        # unter Windows PowerShell 5.1 werfen (top-level, kein try/catch).
        $raw = Get-Content -LiteralPath $vf -Raw
        if ($raw) { return $raw.Trim() }
    }
    return 'unbekannt'
}

function Test-InstallDir([string]$dir) {
    if (-not $dir) { return $false }
    return (Test-Path (Join-Path $dir 'backend\zettelwirtschaft-backend.exe'))
}

function Show-Error([string]$msg) {
    if ($Headless) { Write-Host "[ERROR] $msg"; return }
    [System.Windows.Forms.MessageBox]::Show($msg, 'Zettelwirtschaft Update-Wizard',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}

# ------------------------------------------------------------ InstallDir ermitteln
if (-not $InstallDir) { $InstallDir = Get-RegValue 'InstallDir' }

if (-not (Test-InstallDir $InstallDir)) {
    if ($Headless) {
        Show-Error "InstallDir ungueltig (erwartet backend\zettelwirtschaft-backend.exe)."
        exit 1
    }
    $fbd = New-Object System.Windows.Forms.FolderBrowserDialog
    $fbd.Description = 'Zettelwirtschaft-Installationsverzeichnis waehlen'
    $fbd.ShowNewFolderButton = $false
    if ($fbd.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $InstallDir = $fbd.SelectedPath }
    if (-not (Test-InstallDir $InstallDir)) {
        Show-Error "Kein gueltiges Zettelwirtschaft-Verzeichnis ausgewaehlt."
        exit 1
    }
}

$InstallDir = (Resolve-Path $InstallDir).Path.TrimEnd('\')
$WizardDirResolved = (Resolve-Path $WizardDir).Path.TrimEnd('\')

if ($InstallDir -ieq $WizardDirResolved) {
    Show-Error "Der Update-Wizard darf nicht aus dem Installationsverzeichnis laufen.`n`nBitte das Update-Paket in einen TEMP-Ordner entpacken und den Wizard von dort starten."
    exit 1
}

$ConfigPath     = Get-RegValue 'ConfigPath'
$DataDir        = Get-RegValue 'DataDir'
$CurrentVersion = Get-AppVersion $InstallDir
$NewVersion     = Get-AppVersion $WizardDirResolved
$BackendExe     = Join-Path $InstallDir 'backend\zettelwirtschaft-backend.exe'

# ------------------------------------------------------------ GUI (nur nicht-Headless)
if (-not $Headless) {

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Zettelwirtschaft Update-Wizard'
$form.ClientSize = New-Object System.Drawing.Size(700, 520)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Icon = [System.Drawing.SystemIcons]::Application

$header = New-Object System.Windows.Forms.Panel
$header.Dock = 'Top'; $header.Height = 64
$header.BackColor = [System.Drawing.Color]::FromArgb(20, 60, 120)
$form.Controls.Add($header)

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = 'Zettelwirtschaft Update-Wizard'
$lblTitle.ForeColor = [System.Drawing.Color]::White
$lblTitle.Font = New-Object System.Drawing.Font('Segoe UI', 15, [System.Drawing.FontStyle]::Bold)
$lblTitle.Location = New-Object System.Drawing.Point(18, 14)
$lblTitle.AutoSize = $true
$header.Controls.Add($lblTitle)

$footer = New-Object System.Windows.Forms.Panel
$footer.Dock = 'Bottom'; $footer.Height = 50
$footer.BackColor = [System.Drawing.Color]::FromArgb(240, 240, 240)
$form.Controls.Add($footer)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = 'Update starten'
$btnNext.Size = New-Object System.Drawing.Size(140, 32)
$btnNext.Location = New-Object System.Drawing.Point(($form.ClientSize.Width - 140 - 15), 9)
$btnNext.Anchor = [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Right
$btnNext.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
$footer.Controls.Add($btnNext)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = 'Abbrechen'
$btnCancel.Size = New-Object System.Drawing.Size(110, 32)
$btnCancel.Location = New-Object System.Drawing.Point(($btnNext.Location.X - 110 - 10), 9)
$btnCancel.Anchor = [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Right
$btnCancel.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$footer.Controls.Add($btnCancel)

$body = New-Object System.Windows.Forms.Panel
$body.Dock = 'Fill'; $body.BackColor = [System.Drawing.Color]::White
$form.Controls.Add($body)
$body.BringToFront()

$script:stepLabels = @{}
$script:logBox = $null
$script:progressBar = $null
$script:currentPage = 'welcome'

function Show-WelcomePage {
    $script:currentPage = 'welcome'
    $body.Controls.Clear()

    $lbl1 = New-Object System.Windows.Forms.Label
    $lbl1.Text = 'Willkommen'
    $lbl1.Font = New-Object System.Drawing.Font('Segoe UI', 13, [System.Drawing.FontStyle]::Bold)
    $lbl1.Location = New-Object System.Drawing.Point(20, 18); $lbl1.AutoSize = $true
    $body.Controls.Add($lbl1)

    $lbl2 = New-Object System.Windows.Forms.Label
    $lbl2.Text = "Dieser Assistent aktualisiert Zettelwirtschaft auf eine neuere Version.`r`n`r`n" +
        "   1. Sicherungs-Backup (Service laeuft noch)`r`n" +
        "   2. Dienst stoppen`r`n" +
        "   3. Neue Dateien ueber die bestehenden kopieren`r`n" +
        "   4. Dienst starten (Datenbank-Migrationen laufen automatisch)"
    $lbl2.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $lbl2.Location = New-Object System.Drawing.Point(20, 50)
    $lbl2.Size = New-Object System.Drawing.Size(650, 130)
    $body.Controls.Add($lbl2)

    $grp = New-Object System.Windows.Forms.GroupBox
    $grp.Text = ' Erkannte Umgebung '
    $grp.Location = New-Object System.Drawing.Point(20, 190)
    $grp.Size = New-Object System.Drawing.Size(650, 150)
    $grp.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $body.Controls.Add($grp)

    $info = @(
        @{ Key = 'Installationsverzeichnis:'; Val = $InstallDir },
        @{ Key = 'Aktuelle Version:';         Val = $CurrentVersion },
        @{ Key = 'Neue Version:';             Val = $NewVersion },
        @{ Key = 'Update-Quelle:';            Val = $WizardDirResolved }
    )
    $y = 24
    foreach ($pair in $info) {
        $k = New-Object System.Windows.Forms.Label
        $k.Text = $pair.Key; $k.Font = New-Object System.Drawing.Font('Segoe UI', 9)
        $k.Location = New-Object System.Drawing.Point(15, $y); $k.Size = New-Object System.Drawing.Size(170, 20)
        $grp.Controls.Add($k)
        $v = New-Object System.Windows.Forms.Label
        $v.Text = [string]$pair.Val; $v.Font = New-Object System.Drawing.Font('Consolas', 9)
        $v.Location = New-Object System.Drawing.Point(190, $y); $v.Size = New-Object System.Drawing.Size(445, 20)
        $grp.Controls.Add($v)
        $y += 26
    }

    if ($CurrentVersion -eq $NewVersion -and $CurrentVersion -ne 'unbekannt') {
        $warn = New-Object System.Windows.Forms.Label
        $warn.Text = "Hinweis: installierte und neue Version sind identisch ($CurrentVersion). Update trotzdem moeglich."
        $warn.ForeColor = [System.Drawing.Color]::DarkOrange
        $warn.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Italic)
        $warn.Location = New-Object System.Drawing.Point(15, ($y + 2)); $warn.Size = New-Object System.Drawing.Size(620, 20)
        $grp.Controls.Add($warn)
    }

    $btnNext.Text = 'Update starten'; $btnNext.Enabled = $true; $btnCancel.Enabled = $true
}

function Show-ProgressPage {
    $script:currentPage = 'progress'
    $body.Controls.Clear()

    $lbl1 = New-Object System.Windows.Forms.Label
    $lbl1.Text = 'Update-Fortschritt'
    $lbl1.Font = New-Object System.Drawing.Font('Segoe UI', 13, [System.Drawing.FontStyle]::Bold)
    $lbl1.Location = New-Object System.Drawing.Point(20, 14); $lbl1.AutoSize = $true
    $body.Controls.Add($lbl1)

    $steps = @(
        @{ Id='backup'; Text='1. Sicherungs-Backup erstellen' },
        @{ Id='stop';   Text='2. Dienst stoppen' },
        @{ Id='copy';   Text='3. Dateien aktualisieren' },
        @{ Id='start';  Text='4. Dienst starten' }
    )
    $script:stepLabels = @{}
    $y = 50
    foreach ($step in $steps) {
        $l = New-Object System.Windows.Forms.Label
        $l.Text = "   -  $($step.Text)"
        $l.Font = New-Object System.Drawing.Font('Segoe UI', 10)
        $l.Location = New-Object System.Drawing.Point(20, $y); $l.Size = New-Object System.Drawing.Size(650, 22)
        $l.ForeColor = [System.Drawing.Color]::DimGray
        $body.Controls.Add($l)
        $script:stepLabels[$step.Id] = $l
        $y += 26
    }

    $y += 8
    $script:progressBar = New-Object System.Windows.Forms.ProgressBar
    $script:progressBar.Location = New-Object System.Drawing.Point(20, $y)
    $script:progressBar.Size = New-Object System.Drawing.Size(650, 18)
    $script:progressBar.Minimum = 0; $script:progressBar.Maximum = 100
    $body.Controls.Add($script:progressBar)

    $y += 28
    $script:logBox = New-Object System.Windows.Forms.TextBox
    $script:logBox.Multiline = $true; $script:logBox.ReadOnly = $true; $script:logBox.ScrollBars = 'Vertical'
    $script:logBox.Font = New-Object System.Drawing.Font('Consolas', 8)
    $script:logBox.BackColor = [System.Drawing.Color]::FromArgb(250, 250, 250)
    $script:logBox.Location = New-Object System.Drawing.Point(20, $y)
    $script:logBox.Size = New-Object System.Drawing.Size(650, 180)
    $body.Controls.Add($script:logBox)

    $btnNext.Text = 'Bitte warten...'; $btnNext.Enabled = $false; $btnCancel.Enabled = $false
}

}  # end if (-not $Headless)

# ------------------------------------------------------------ Log / Step / Progress
function Write-Log([string]$msg) {
    if ($Headless) { Write-Host "[LOG] $msg"; return }
    if ($script:logBox) {
        $script:logBox.AppendText(('[{0}] {1}{2}' -f (Get-Date -Format 'HH:mm:ss'), $msg, "`r`n"))
        [System.Windows.Forms.Application]::DoEvents()
    }
}

function Set-StepStatus([string]$id, [string]$status) {
    if ($Headless) { Write-Host "[STEP] $id $status"; return }
    $lbl = $script:stepLabels[$id]
    if (-not $lbl) { return }
    $txt = $lbl.Text.Substring(6)
    $marker = switch ($status) { 'running' { '>>' } 'ok' { '[OK]' } 'fail' { '[!!]' } 'warn' { '[??]' } default { '  ' } }
    $color  = switch ($status) {
        'running' { [System.Drawing.Color]::Blue }
        'ok'      { [System.Drawing.Color]::ForestGreen }
        'fail'    { [System.Drawing.Color]::Firebrick }
        'warn'    { [System.Drawing.Color]::DarkOrange }
        default   { [System.Drawing.Color]::DimGray }
    }
    $lbl.Text = " $marker $txt"; $lbl.ForeColor = $color
    [System.Windows.Forms.Application]::DoEvents()
}

function Update-Progress([int]$percent) {
    if ($Headless) { Write-Host "[PROGRESS] $percent"; return }
    $script:progressBar.Value = [Math]::Min(100, [Math]::Max(0, $percent))
    [System.Windows.Forms.Application]::DoEvents()
}

# ------------------------------------------------------------ Schritte
function Step-Backup {
    Set-StepStatus 'backup' 'running'
    Write-Log 'Erstelle Sicherungs-Backup (Service laeuft noch)...'
    if ($DryRun) { Start-Sleep -Seconds 1; Set-StepStatus 'backup' 'ok'; return $true }
    if (-not (Test-Path $BackendExe)) {
        Write-Log "WARNUNG: Backend-Exe fehlt ($BackendExe) - Backup uebersprungen"
        Set-StepStatus 'backup' 'warn'; return $true
    }
    if (-not $ConfigPath -or -not (Test-Path $ConfigPath)) {
        Write-Log 'WARNUNG: ConfigPath unbekannt - Backup uebersprungen'
        Set-StepStatus 'backup' 'warn'; return $true
    }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $BackendExe
        $psi.Arguments = "--config `"$ConfigPath`" --backup"
        $psi.WorkingDirectory = (Join-Path $InstallDir 'backend')
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        if ($proc.ExitCode -eq 0) {
            Write-Log "Backup erstellt: $($stdout.Trim())"
            Set-StepStatus 'backup' 'ok'; return $true
        }
        Write-Log "WARNUNG: Backup-Exit $($proc.ExitCode)"
        foreach ($line in ($stderr -split "`r?`n")) { if ($line.Trim()) { Write-Log "  $line" } }
        Set-StepStatus 'backup' 'warn'; return $true   # nicht-fatal
    } catch {
        Write-Log "WARNUNG: Backup fehlgeschlagen: $_"
        Set-StepStatus 'backup' 'warn'; return $true
    }
}

function Step-StopService {
    Set-StepStatus 'stop' 'running'
    Write-Log "Stoppe Dienst $ServiceName..."
    if ($DryRun) { Start-Sleep -Seconds 1; Set-StepStatus 'stop' 'ok'; return $true }
    try {
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc) { Write-Log "WARNUNG: Dienst nicht gefunden"; Set-StepStatus 'stop' 'warn'; return $true }
        if ($svc.Status -eq 'Stopped') {
            Write-Log 'Dienst war bereits gestoppt'
        } else {
            Stop-Service -Name $ServiceName -Force -ErrorAction Stop
            $svc.WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
            Write-Log 'Dienst gestoppt'
        }
        Set-StepStatus 'stop' 'ok'; return $true
    } catch {
        Write-Log "FEHLER beim Stoppen: $_"
        Set-StepStatus 'stop' 'fail'; return $false
    }
}

function Step-CopyFiles {
    Set-StepStatus 'copy' 'running'
    Write-Log 'Kopiere neue Dateien (robocopy, additiv)...'
    if ($DryRun) { Start-Sleep -Seconds 1; Set-StepStatus 'copy' 'ok'; return $true }
    try {
        $rcArgs = @(
            $WizardDirResolved, $InstallDir, '/E',
            '/NFL', '/NDL', '/NJH', '/NJS', '/NC', '/NS',
            '/R:2', '/W:2',
            '/XF', 'Uninstall.exe'
        )
        Write-Log "  robocopy $WizardDirResolved -> $InstallDir"
        $out = & robocopy.exe @rcArgs 2>&1
        $rc = $LASTEXITCODE
        if ($rc -lt 8) {
            Write-Log "Dateien aktualisiert (robocopy exit=$rc)"
            # M2: Registry-Version nachziehen. Sonst zeigen HKLM und "Programme &
            # Features" weiter die alte Version -> falsche Basis fuer eine spaetere
            # Update-/Uninstall-Entscheidung. Nicht-fatal bei Fehler.
            try {
                Set-ItemProperty -Path 'HKLM:\SOFTWARE\Zettelwirtschaft' -Name 'Version' -Value $NewVersion -ErrorAction Stop
                Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft' -Name 'DisplayVersion' -Value $NewVersion -ErrorAction SilentlyContinue
                Write-Log "Registry-Version aktualisiert: $NewVersion"
            } catch {
                Write-Log "WARNUNG: Registry-Version nicht aktualisiert: $_"
            }
            Set-StepStatus 'copy' 'ok'; return $true
        }
        Write-Log "FEHLER: robocopy exit=$rc"
        foreach ($line in $out) { Write-Log "  $line" }
        Set-StepStatus 'copy' 'fail'; return $false
    } catch {
        Write-Log "FEHLER beim Kopieren: $_"
        Set-StepStatus 'copy' 'fail'; return $false
    }
}

function Step-StartService {
    Set-StepStatus 'start' 'running'
    Write-Log "Starte Dienst $ServiceName (Migrationen laufen beim Start)..."
    if ($DryRun) { Start-Sleep -Seconds 1; Set-StepStatus 'start' 'ok'; return $true }
    try {
        # Symmetrisch zu Step-StopService: ein fehlender Dienst (kaputte Installation)
        # ist eine Warnung, kein Update-Fehlschlag — die Dateien sind ja kopiert.
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc) {
            Write-Log "WARNUNG: Dienst $ServiceName nicht gefunden - Start uebersprungen"
            Set-StepStatus 'start' 'warn'; return $true
        }
        Start-Service -Name $ServiceName -ErrorAction Stop
        $svc.WaitForStatus('Running', (New-TimeSpan -Seconds 120))
        Write-Log 'Dienst laeuft'
        Set-StepStatus 'start' 'ok'; return $true
    } catch {
        Write-Log "FEHLER beim Starten: $_"
        if ($DataDir) { Write-Log "Pruefe $DataDir\logs\backend.log" }
        Set-StepStatus 'start' 'fail'; return $false
    }
}

function Invoke-Update {
    Write-Log "Update: $CurrentVersion -> $NewVersion"
    Write-Log "Install: $InstallDir"
    Write-Log "Quelle:  $WizardDirResolved"
    Write-Log ''
    $null = Step-Backup;        Update-Progress 20
    $okStop = Step-StopService; Update-Progress 45
    if (-not $okStop) { return $false }
    $okCopy = Step-CopyFiles;   Update-Progress 75
    if (-not $okCopy) {
        Write-Log ''
        Write-Log 'Kopieren fehlgeschlagen - versuche Dienst wieder zu starten...'
        Step-StartService | Out-Null
        return $false
    }
    $okStart = Step-StartService; Update-Progress 100
    return $okStart
}

function Show-Done([bool]$success) {
    Write-Log ''
    Write-Log '=================================================='
    if ($success) {
        Write-Log "Update auf Version $NewVersion erfolgreich abgeschlossen."
    } else {
        Write-Log 'Update mit FEHLERN beendet. Siehe Protokoll oben.'
        if ($DataDir) { Write-Log "Backups liegen unter: $DataDir\data\backups" }
    }
    Write-Log '=================================================='
    if ($Headless) { Write-Host "[DONE] $(if ($success) { 'success' } else { 'fail' })"; return }
    $script:currentPage = 'done'
    $btnNext.Text = 'Schliessen'; $btnNext.Enabled = $true; $btnCancel.Enabled = $false
}

# ------------------------------------------------------------ Entry point
if ($Headless) {
    Write-Host "[LOG] Headless-Update: $CurrentVersion -> $NewVersion"
    $success = Invoke-Update
    Show-Done $success
    if ($success) { exit 0 } else { exit 1 }
}

$btnCancel.Add_Click({ if ($script:currentPage -eq 'welcome') { $form.Close() } })
$btnNext.Add_Click({
    switch ($script:currentPage) {
        'welcome' { Show-ProgressPage; $ok = Invoke-Update; Show-Done $ok }
        'done'    { $form.Close() }
    }
})

Show-WelcomePage
[void]$form.ShowDialog()
