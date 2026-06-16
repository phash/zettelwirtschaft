# Native-Windows Update-Wizard + Backup + Restore — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GUI update wizard, manual backup, guided restore, and backup-before-uninstall to Zettelwirtschaft's existing native-Windows installer (`dist/native` + `setup-native.nsi`), modeled on the PraxisZeit (zeiterfassung) installer.

**Architecture:** A new offline `--backup` subcommand on the frozen backend exe (reuses the tested `backup_service`, bypasses PIN-protected HTTP). Three PowerShell/batch artifacts (`update-wizard`, `backup`, `restore-backup`) that drive the NSSM service `ZettelwirtschaftBackend` and read install/data/config paths from `HKLM\SOFTWARE\Zettelwirtschaft`. The build script ships them into `dist/native`; the NSIS installer places them in the install dir, adds Start-Menu entries, and runs a safety backup before uninstalling.

**Tech Stack:** Python 3.12 / argparse / pytest (backend), Windows PowerShell 5.1 + WinForms + batch (scripts), NSIS (installer), NSSM (service), robocopy (file replacement).

**Branch:** `feat/native-update-backup-restore` (already created, based on `feat/1.4.1-update-check-and-https`).

**Important environment notes for the implementer:**
- The script launchers call `powershell.exe` (Windows PowerShell **5.1**), so the `.ps1` files MUST stay 5.1-compatible: no `?.` null-conditional, no `??`, no `ForEach-Object -Parallel`. Use explicit `if ($x) { ... }`.
- The backend is a PyInstaller onedir bundle: an update is pure file-replacement; migrations run automatically at service start (`entrypoint.py:_run_migrations`). There is **no** pip / vc_redist step (unlike PraxisZeit).
- `config.toml` and all user data live in a **separate data dir** (e.g. `Documents\Zettelwirtschaft`), NOT in the install dir — so `robocopy` of program files never touches config/data.

---

## File Structure

**Create:**
- `backend/tests/test_entrypoint_backup.py` — pytest for the `--backup` CLI mode
- `scripts/update-wizard.ps1` — WinForms GUI + headless update wizard
- `scripts/update-wizard.bat` — admin-elevating launcher for the wizard
- `scripts/backup.bat` — manual backup launcher (calls backend exe `--backup`)
- `scripts/restore-backup.ps1` — offline guided restore
- `scripts/restore-backup.bat` — admin-elevating launcher for restore

**Modify:**
- `backend/app/entrypoint.py` — add `--backup` / `--full` modes
- `scripts/build-native.ps1` — copy the 5 new scripts into `dist/native`
- `setup-native.nsi` — install scripts, Start-Menu shortcuts, backup-before-uninstall, delete-list
- `CLAUDE.md` — document native update/backup/restore ops
- `memory/release-deployment.md` — same, for the memory index (done by the human/assistant, not in-repo)

---

## Task 1: Backend `--backup` subcommand (TDD)

**Files:**
- Modify: `backend/app/entrypoint.py` (argparse in `main()`, lines ~158-184)
- Test: `backend/tests/test_entrypoint_backup.py`

**Context:** `create_backup(settings, include_documents=False) -> str` lives in
`app/services/backup_service.py`; it writes `backup_<db|full>_<ts>.zip` into
`Path(settings.ARCHIVE_DIR).parent / "backups"`, storing the DB at zip path
`database/zettelwirtschaft.db` and documents under `documents/`. `get_settings()` is
`@lru_cache`d and binds the TOML file at import — the test therefore monkeypatches
`app.config.get_settings` instead of going through `--config`/TOML.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_entrypoint_backup.py`:

```python
"""Tests fuer den --backup CLI-Modus des Native-Entrypoints (app/entrypoint.py)."""

import sqlite3
import zipfile
from pathlib import Path

from app.config import Settings


def _make_sqlite_db(path: Path) -> None:
    """Legt eine minimale, gueltige SQLite-DB am Zielpfad an."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
    finally:
        conn.close()


def test_backup_cli_creates_db_zip(test_settings: Settings, monkeypatch, capsys):
    db_path = Path(test_settings.DATABASE_URL.split("///")[-1])
    _make_sqlite_db(db_path)

    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: test_settings)

    from app.entrypoint import main
    rc = main(["--backup"])

    out = capsys.readouterr().out.strip()
    assert rc == 0, "Exit-Code muss 0 sein"
    zip_path = Path(out)
    assert zip_path.exists(), f"gedruckter Pfad existiert nicht: {out}"
    assert "backup_db_" in zip_path.name
    with zipfile.ZipFile(zip_path) as zf:
        assert "database/zettelwirtschaft.db" in zf.namelist()


def test_backup_cli_full_includes_documents(test_settings: Settings, monkeypatch, capsys):
    db_path = Path(test_settings.DATABASE_URL.split("///")[-1])
    _make_sqlite_db(db_path)
    doc = Path(test_settings.ARCHIVE_DIR) / "2024" / "rechnung.pdf"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b"%PDF-1.4 fake")

    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: test_settings)

    from app.entrypoint import main
    rc = main(["--backup", "--full"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    zip_path = Path(out)
    assert "backup_full_" in zip_path.name
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n.startswith("documents/") for n in names), names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entrypoint_backup.py -v`
Expected: FAIL — `main(["--backup"])` exits via argparse `SystemExit: 2`
("unrecognized arguments: --backup") because the flag doesn't exist yet.

- [ ] **Step 3: Add the `--backup` / `--full` flags and handler in `entrypoint.py`**

In `backend/app/entrypoint.py`, inside `main()`, add the two arguments right after the
`--version` argument:

```python
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Backup erstellen (DB + Config) und beenden",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Mit --backup: Dokumente einschliessen",
    )
```

Then add the handler immediately after the `if args.version:` block (before
`_ensure_data_dirs()`):

```python
    if args.version:
        from app.main import _read_version
        print(_read_version())
        return 0

    if args.backup:
        # Offline-Backup: nutzt den getesteten backup_service direkt — kein HTTP,
        # also auch keine PIN-Huerde, und funktioniert bei laufendem wie gestopptem
        # Service (sqlite3 .backup() ist multi-prozess-sicher).
        from app.config import get_settings
        from app.services.backup_service import create_backup

        settings = get_settings()
        path = create_backup(settings, include_documents=args.full)
        print(path)
        return 0
```

Also update the module docstring usage line (top of file) to mention `--backup`:

```python
Aufrufpfad:
    zettelwirtschaft-backend.exe [--config <path>] [--migrate-only]
                                 [--backup [--full]] [--version]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entrypoint_backup.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/entrypoint.py backend/tests/test_entrypoint_backup.py
git commit -m "feat(native): --backup subcommand auf der Backend-Exe (offline, reuse backup_service)"
```

---

## Task 2: `scripts/backup.bat` (manual backup launcher)

**Files:**
- Create: `scripts/backup.bat`

**Context:** Reads `InstallDir`/`ConfigPath`/`DataDir` from `HKLM\SOFTWARE\Zettelwirtschaft`
(written by `setup-native.nsi`), calls `backend\zettelwirtschaft-backend.exe --config <cfg>
--backup`, logs to `<DataDir>\logs\backup.log`. No admin needed (backup writes into the
user-owned data dir). An optional first argument overrides the config path.

- [ ] **Step 1: Create `scripts/backup.bat`**

```bat
@echo off
setlocal
REM ============================================================
REM Zettelwirtschaft - Manuelles Backup (DB + Config)
REM Liest InstallDir/ConfigPath/DataDir aus der Registry und ruft
REM die Backend-Exe mit --backup. Optionales Argument: ConfigPath.
REM ============================================================

for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Zettelwirtschaft" /v InstallDir 2^>nul ^| findstr /i "InstallDir"') do set "INSTALL_DIR=%%b"
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Zettelwirtschaft" /v ConfigPath 2^>nul ^| findstr /i "ConfigPath"') do set "CONFIG_PATH=%%b"
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Zettelwirtschaft" /v DataDir 2^>nul ^| findstr /i "DataDir"') do set "DATA_DIR=%%b"

if not "%~1"=="" set "CONFIG_PATH=%~1"

set "BACKEND_EXE=%INSTALL_DIR%\backend\zettelwirtschaft-backend.exe"

if not exist "%BACKEND_EXE%" (
    echo FEHLER: Backend-Exe nicht gefunden: %BACKEND_EXE%
    echo Ist Zettelwirtschaft Native installiert?
    pause
    exit /b 1
)
if "%CONFIG_PATH%"=="" (
    echo FEHLER: ConfigPath nicht gefunden (Registry leer und kein Argument uebergeben).
    pause
    exit /b 1
)

if not "%DATA_DIR%"=="" if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
set "LOGFILE=%DATA_DIR%\logs\backup.log"

echo.
echo Erstelle Backup...
echo [%date% %time%] Starte manuelles Backup >> "%LOGFILE%"
"%BACKEND_EXE%" --config "%CONFIG_PATH%" --backup
set "RC=%errorlevel%"
echo [%date% %time%] Backup beendet, Exit %RC% >> "%LOGFILE%"

echo.
if "%RC%"=="0" (
    echo Backup erfolgreich erstellt.
    if not "%DATA_DIR%"=="" echo Ablage: %DATA_DIR%\data\backups
) else (
    echo FEHLER: Backup fehlgeschlagen (Exit %RC%).
    if not "%LOGFILE%"=="" echo Details: %LOGFILE%
)
pause
exit /b %RC%
```

- [ ] **Step 2: Validate (graceful failure without an install)**

Run (in repo root, on Windows):
`cmd /c scripts\backup.bat`
Expected: prints `FEHLER: Backend-Exe nicht gefunden: ...\backend\zettelwirtschaft-backend.exe`
and waits at `pause` (press a key). This confirms registry-read + guard logic works even
when nothing is installed. (If a real install exists, it will instead create a backup.)

- [ ] **Step 3: Commit**

```bash
git add scripts/backup.bat
git commit -m "feat(native): backup.bat - manuelles Backup ueber die Backend-Exe"
```

---

## Task 3: `scripts/update-wizard.ps1` + `scripts/update-wizard.bat`

**Files:**
- Create: `scripts/update-wizard.ps1`
- Create: `scripts/update-wizard.bat`

**Context:** WinForms GUI wizard (Welcome → Progress → Done) plus a `-Headless` mode that
emits `[STEP]`/`[LOG]`/`[PROGRESS]`/`[DONE]` markers, and a `-DryRun` mode that simulates
every step. Detects InstallDir/DataDir/ConfigPath from the registry (or `-InstallDir`).
Steps: backup (via backend exe) → stop service → robocopy new files → start service. Service
name is `ZettelwirtschaftBackend`. Marker file proving a valid install dir:
`backend\zettelwirtschaft-backend.exe`.

- [ ] **Step 1: Create `scripts/update-wizard.ps1`**

```powershell
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
    if (Test-Path $vf) { return (Get-Content -LiteralPath $vf -Raw).Trim() }
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
        Start-Service -Name $ServiceName -ErrorAction Stop
        $svc = Get-Service -Name $ServiceName
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
```

- [ ] **Step 2: Create `scripts/update-wizard.bat`**

```bat
@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft Update-Wizard Launcher (Admin + STA)
REM Aufruf: update-wizard.bat [InstallDir]
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Fordere Administrator-Rechte an...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "DIR=%~dp0"
set "WIZARD=%DIR%update-wizard.ps1"
if not exist "%WIZARD%" (
    echo FEHLER: %WIZARD% nicht gefunden.
    pause
    exit /b 1
)

chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%WIZARD%" %*
exit /b %errorlevel%
```

- [ ] **Step 3: Validate the PS1 parses (no syntax errors)**

Run: `powershell -NoProfile -Command "$e=$null; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw scripts\update-wizard.ps1),[ref]$e) | Out-Null; if($e){$e; exit 1} else {'PARSE OK'}"`
Expected: `PARSE OK` (no parse errors).

- [ ] **Step 4: Headless dry-run smoke test against a fake install dir**

Run:
```powershell
$fake = Join-Path $env:TEMP 'zw-fake-install'
New-Item -ItemType Directory -Force -Path (Join-Path $fake 'backend') | Out-Null
'1.4.1' | Set-Content (Join-Path $fake 'VERSION')
New-Item -ItemType File -Force -Path (Join-Path $fake 'backend\zettelwirtschaft-backend.exe') | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update-wizard.ps1 -Headless -DryRun -InstallDir $fake
```
Expected: stdout contains `[STEP] backup ok`, `[STEP] stop ok`, `[STEP] copy ok`,
`[STEP] start ok`, and ends with `[DONE] success`. (DryRun skips the real exe/service/robocopy,
so no install is needed.) Clean up: `Remove-Item -Recurse -Force $fake`.

- [ ] **Step 5: Commit**

```bash
git add scripts/update-wizard.ps1 scripts/update-wizard.bat
git commit -m "feat(native): GUI-Update-Wizard (WinForms + Headless/DryRun) fuer Native-Pfad"
```

---

## Task 4: `scripts/restore-backup.ps1` + `scripts/restore-backup.bat`

**Files:**
- Create: `scripts/restore-backup.ps1`
- Create: `scripts/restore-backup.bat`

**Context:** Offline restore. Parses `config.toml` for `DATABASE_URL` (→ db file path) and
`ARCHIVE_DIR`. Stops the service, deletes `-wal`/`-shm` sidecars, replaces the DB from the
backup zip's `database/zettelwirtschaft.db`, optionally restores `documents/` into
`ARCHIVE_DIR`, restarts the service. Destructive → requires typing `WIEDERHERSTELLEN`.
Windows PowerShell 5.1 compatible (no `?.`).

- [ ] **Step 1: Create `scripts/restore-backup.ps1`**

```powershell
# Zettelwirtschaft - Restore aus Backup (Offline). Windows PowerShell 5.1.
# Stoppt den Dienst, ersetzt die DB (+ optional Dokumente) aus einem Backup-ZIP,
# startet den Dienst. Destruktiv -> explizite Tippbestaetigung.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupZip,
    [string]$ConfigPath = ""
)

$ErrorActionPreference = 'Stop'
$ServiceName = 'ZettelwirtschaftBackend'

function Get-RegValue([string]$name) {
    try { return (Get-ItemProperty 'HKLM:\SOFTWARE\Zettelwirtschaft' -Name $name -ErrorAction Stop).$name }
    catch { return $null }
}

if (-not (Test-Path $BackupZip)) { Write-Host "FEHLER: Backup nicht gefunden: $BackupZip"; exit 1 }
if (-not $ConfigPath) { $ConfigPath = Get-RegValue 'ConfigPath' }
if (-not $ConfigPath -or -not (Test-Path $ConfigPath)) {
    Write-Host "FEHLER: config.toml nicht gefunden ($ConfigPath). Bitte -ConfigPath angeben."
    exit 1
}

# --- config.toml parsen ---
$cfg = Get-Content -LiteralPath $ConfigPath -Raw
function Get-TomlString([string]$key) {
    $m = [regex]::Match($cfg, "(?m)^\s*$key\s*=\s*`"([^`"]*)`"")
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}
$dbUrl = Get-TomlString 'DATABASE_URL'
$archiveDir = Get-TomlString 'ARCHIVE_DIR'
if (-not $dbUrl) { Write-Host 'FEHLER: DATABASE_URL fehlt in config.toml.'; exit 1 }
$dbPath = $dbUrl -replace '^sqlite\+aiosqlite:///', '' -replace '^sqlite:///', ''
$dbPath = $dbPath -replace '/', '\'

Write-Host ''
Write-Host '=== Zettelwirtschaft Restore ==='
Write-Host "Backup:  $BackupZip"
Write-Host "Ziel-DB: $dbPath"
if ($archiveDir) { Write-Host "Archiv:  $archiveDir" }
Write-Host ''
Write-Host 'WARNUNG: Die aktuelle Datenbank wird ueberschrieben. Dies kann NICHT rueckgaengig gemacht werden.'
$confirm = Read-Host "Zum Bestaetigen 'WIEDERHERSTELLEN' eingeben"
if ($confirm -ne 'WIEDERHERSTELLEN') { Write-Host 'Abgebrochen.'; exit 0 }

$tmp = Join-Path $env:TEMP ('zw-restore-' + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
    Expand-Archive -LiteralPath $BackupZip -DestinationPath $tmp -Force
    $srcDb = Join-Path $tmp 'database\zettelwirtschaft.db'
    if (-not (Test-Path $srcDb)) {
        Write-Host 'FEHLER: Kein database/zettelwirtschaft.db im Backup-ZIP.'
        exit 1
    }

    Write-Host 'Stoppe Dienst...'
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) { $svc.WaitForStatus('Stopped', (New-TimeSpan -Seconds 60)) }

    foreach ($sfx in '-wal', '-shm') {
        $side = "$dbPath$sfx"
        if (Test-Path $side) { Remove-Item -LiteralPath $side -Force }
    }
    $dbParent = Split-Path -Parent $dbPath
    if ($dbParent) { New-Item -ItemType Directory -Force -Path $dbParent | Out-Null }
    Copy-Item -LiteralPath $srcDb -Destination $dbPath -Force
    Write-Host 'Datenbank wiederhergestellt.'

    $srcDocs = Join-Path $tmp 'documents'
    if ((Test-Path $srcDocs) -and $archiveDir) {
        $archiveWin = $archiveDir -replace '/', '\'
        Write-Host 'Stelle Dokumente wieder her...'
        New-Item -ItemType Directory -Force -Path $archiveWin | Out-Null
        Copy-Item -Path (Join-Path $srcDocs '*') -Destination $archiveWin -Recurse -Force
    }

    Write-Host 'Starte Dienst...'
    Start-Service -Name $ServiceName -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host 'Restore abgeschlossen.'
    Write-Host 'Hinweis: Falls die Vektor-Suche Treffer vermissen laesst, in den'
    Write-Host "Einstellungen -> Wartung 'Vektor-Index neu aufbauen' ausfuehren."
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
```

- [ ] **Step 2: Create `scripts/restore-backup.bat`**

```bat
@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM Zettelwirtschaft Restore Launcher (Admin)
REM Aufruf: restore-backup.bat <backup-file.zip>
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Fordere Administrator-Rechte an...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
    exit /b
)

if "%~1"=="" (
    echo Usage: restore-backup.bat ^<backup-file.zip^>
    echo.
    echo Beispiel: restore-backup.bat "%%USERPROFILE%%\Documents\Zettelwirtschaft\data\backups\backup_db_20260616_120000.zip"
    pause
    exit /b 1
)

set "DIR=%~dp0"
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DIR%restore-backup.ps1" -BackupZip "%~1"
exit /b %errorlevel%
```

- [ ] **Step 3: Validate the PS1 parses**

Run: `powershell -NoProfile -Command "$e=$null; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw scripts\restore-backup.ps1),[ref]$e) | Out-Null; if($e){$e; exit 1} else {'PARSE OK'}"`
Expected: `PARSE OK`.

- [ ] **Step 4: Validate the config-parse + abort path (no service touched)**

Run:
```powershell
$cfg = Join-Path $env:TEMP 'zw-test-config.toml'
@'
DATABASE_URL = "sqlite+aiosqlite:///C:/Temp/zwdata/data/zettelwirtschaft.db"
ARCHIVE_DIR = "C:/Temp/zwdata/data/archive"
'@ | Set-Content $cfg
$fakeZip = Join-Path $env:TEMP 'zw-fake-backup.zip'
Compress-Archive -Path $cfg -DestinationPath $fakeZip -Force   # zip ohne database/ -> spaeter, hier nur Parse
'n' | powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restore-backup.ps1 -BackupZip $fakeZip -ConfigPath $cfg
```
Expected: prints the parsed `Ziel-DB: C:\Temp\zwdata\data\zettelwirtschaft.db` and
`Archiv:  C:/Temp/zwdata/data/archive`, then — because the typed confirmation is `n`, not
`WIEDERHERSTELLEN` — prints `Abgebrochen.` and exits **without** stopping any service.
Clean up: `Remove-Item $cfg, $fakeZip -Force`.

- [ ] **Step 5: Commit**

```bash
git add scripts/restore-backup.ps1 scripts/restore-backup.bat
git commit -m "feat(native): restore-backup - gefuehrter Offline-Restore aus Backup-ZIP"
```

---

## Task 5: Ship the scripts in `build-native.ps1`

**Files:**
- Modify: `scripts/build-native.ps1` (step 4 block, after the `Copy-Item` of VERSION at line ~105)

**Context:** The update package = the `dist/native` folder (zipped). The wizard must therefore
be inside `dist/native`, and the NSIS `File "${DIST}\..."` lines (Task 6) reference it from there.

- [ ] **Step 1: Add the copy loop**

In `scripts/build-native.ps1`, immediately after the line
`Copy-Item -Force (Join-Path $RepoRoot "VERSION") (Join-Path $DistRoot "VERSION")`
add:

```powershell

# Update/Backup/Restore-Skripte ins Bundle (landen in Setup.exe UND im Update-ZIP)
foreach ($s in @("update-wizard.bat", "update-wizard.ps1", "backup.bat", "restore-backup.bat", "restore-backup.ps1")) {
    Copy-Item -Force (Join-Path $RepoRoot "scripts\$s") (Join-Path $DistRoot $s)
}
```

- [ ] **Step 2: Validate the script still parses**

Run: `powershell -NoProfile -Command "$e=$null; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw scripts\build-native.ps1),[ref]$e) | Out-Null; if($e){$e; exit 1} else {'PARSE OK'}"`
Expected: `PARSE OK`.

- [ ] **Step 3: Commit**

```bash
git add scripts/build-native.ps1
git commit -m "build(native): Update/Backup/Restore-Skripte in dist/native buendeln"
```

---

## Task 6: Wire into `setup-native.nsi` (install + Start-Menu + uninstall backup)

**Files:**
- Modify: `setup-native.nsi` (install section, Start-Menu, uninstall section)

**Context:** `${DIST}` resolves to the built `dist/native` (now containing the 5 scripts).
The install dir already has `backend\zettelwirtschaft-backend.exe`. The uninstall section
already reads `DataDir` into `$0`; we use `$1` for `ConfigPath`.

- [ ] **Step 1: Install the new scripts**

In `setup-native.nsi`, after the line `File "scripts\Convert-Env-To-Config.ps1"` (in Section
`SecMain`), add:

```nsis
    File "${DIST}\update-wizard.bat"
    File "${DIST}\update-wizard.ps1"
    File "${DIST}\backup.bat"
    File "${DIST}\restore-backup.bat"
    File "${DIST}\restore-backup.ps1"
```

- [ ] **Step 2: Add Start-Menu shortcuts**

After the existing `CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk" ...` block
(end of Section `SecMain`), add:

```nsis
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Update.lnk" \
        "$INSTDIR\update-wizard.bat" "" "$SYSDIR\shell32.dll" 46
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Backup jetzt.lnk" \
        "$INSTDIR\backup.bat" "" "$SYSDIR\shell32.dll" 45
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Backup wiederherstellen.lnk" \
        "$INSTDIR\restore-backup.bat" "" "$SYSDIR\shell32.dll" 238
```

- [ ] **Step 3: Safety backup before uninstall**

In `Section "Uninstall"`, as the FIRST statements (before
`nsExec::ExecToLog '"$INSTDIR\service-uninstall.bat" "$INSTDIR"'`), add:

```nsis
    ; Sicherungs-Backup vor dem Entfernen (best-effort, Dienst laeuft ggf. noch)
    ReadRegStr $1 HKLM "Software\Zettelwirtschaft" "ConfigPath"
    ${If} ${FileExists} "$INSTDIR\backend\zettelwirtschaft-backend.exe"
    ${AndIf} $1 != ""
        DetailPrint "Erstelle Sicherungs-Backup vor Deinstallation..."
        nsExec::ExecToLog '"$INSTDIR\backend\zettelwirtschaft-backend.exe" --config "$1" --backup'
    ${EndIf}
```

- [ ] **Step 4: Delete the new scripts on uninstall**

In `Section "Uninstall"`, after `Delete "$INSTDIR\Convert-Env-To-Config.ps1"`, add:

```nsis
    Delete "$INSTDIR\update-wizard.bat"
    Delete "$INSTDIR\update-wizard.ps1"
    Delete "$INSTDIR\backup.bat"
    Delete "$INSTDIR\restore-backup.bat"
    Delete "$INSTDIR\restore-backup.ps1"
```

And after `Delete "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk"`, add:

```nsis
    Delete "$SMPROGRAMS\Zettelwirtschaft\Update.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Backup jetzt.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Backup wiederherstellen.lnk"
```

- [ ] **Step 5: Validate NSIS compiles (if `makensis` is available)**

The NSIS `File` directives need the referenced files to exist, so a real compile requires a
built `dist/native`. If a build is available:
Run: `makensis /DVERSION=test "/DDIST=dist\native" setup-native.nsi`
Expected: compiles to `Zettelwirtschaft-test-Native-Setup.exe` with exit 0 (then delete the
test exe).
If no `dist/native` exists yet, instead do a structural review: confirm the 3 `${If}`/`${AndIf}`/
`${EndIf}` are balanced and every new `File`/`Delete`/`CreateShortcut` line is inside the correct
Section. (A full compile happens as part of `build-native.ps1` in Task 8's manual smoke.)

- [ ] **Step 6: Commit**

```bash
git add setup-native.nsi
git commit -m "feat(native): Installer bindet Update/Backup/Restore ein + Backup-before-uninstall"
```

---

## Task 7: Documentation

**Files:**
- Modify: `CLAUDE.md` (section `## Native-Service-Ops`)

- [ ] **Step 1: Add a native update/backup ops subsection**

In `CLAUDE.md`, at the end of the `## Native-Service-Ops` section (after the
"Migration Docker → Native" block), add:

```markdown
**Update / Backup / Restore** (Native, ab v1.4):

- **Update**: neues `dist/native`-ZIP in einen TEMP-Ordner entpacken, dort
  `update-wizard.bat` als Admin starten (GUI-Wizard). Schritte: Backup → Dienst
  stoppen → robocopy neuer Dateien in den Install-Ordner → Dienst starten
  (Alembic-Migrationen laufen automatisch). Headless: `update-wizard.ps1 -Headless`.
- **Backup manuell**: Startmenü „Backup jetzt" oder `backup.bat` → ruft
  `zettelwirtschaft-backend.exe --config <config.toml> --backup` (DB + Config als
  ZIP in `<DataDir>\data\backups`). Zusätzlich läuft der in-process Auto-Backup
  täglich (kein Scheduled-Task nötig).
- **Restore**: `restore-backup.bat <backup.zip>` (Admin) → Dienst stoppen, DB (+ optional
  Dokumente bei `--full`-Backups) aus dem ZIP zurückspielen, Dienst starten. Danach ggf.
  Vektor-Index neu aufbauen (Einstellungen → Wartung).
- **Backup vor Deinstallation**: der Uninstaller erstellt automatisch ein Sicherungs-Backup
  im Datenordner, bevor er den Dienst und die Programmdateien entfernt.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(native): Update/Backup/Restore-Ops im CLAUDE.md dokumentieren"
```

---

## Task 8: Final verification

- [ ] **Step 1: Full backend test suite stays green**

Run: `cd backend && python -m pytest -q`
Expected: all pass (previously 374 passed / 1 skipped, now +2 from Task 1 → 376 / 1 skipped).

- [ ] **Step 2: Confirm all new scripts parse**

Run:
```powershell
foreach ($f in 'update-wizard.ps1','restore-backup.ps1','build-native.ps1') {
    $e=$null
    [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw "scripts\$f"),[ref]$e) | Out-Null
    if ($e) { Write-Host "PARSE FAIL: $f"; $e } else { Write-Host "OK: $f" }
}
```
Expected: `OK:` for all three.

- [ ] **Step 3: (Manual, documented) End-to-end smoke on a real install**

Not CI-able (needs a real NSSM service). Documented procedure for the maintainer:
1. `pwsh scripts/build-native.ps1` → builds `dist/native` (incl. the new scripts) and the
   Setup.exe.
2. Install via the Setup.exe into a throwaway VM/dir; verify Start-Menu has „Update",
   „Backup jetzt", „Backup wiederherstellen".
3. Run „Backup jetzt" → a `backup_db_*.zip` appears in `<DataDir>\data\backups`.
4. Bump `VERSION`, rebuild `dist/native`, copy it to a TEMP folder, run `update-wizard.bat` →
   wizard completes, service is `RUNNING`, web UI reachable, version updated.
5. Run `restore-backup.bat <the backup zip>` → DB restored, service back up.
6. Uninstall → a safety backup is written before removal; confirm it exists.

- [ ] **Step 4: Push the branch**

```bash
git push -u origin feat/native-update-backup-restore
```

---

## Self-Review Notes (filled by plan author)

**Spec coverage:**
- Spec §4.1 (`--backup`) → Task 1. §4.2 (update-wizard) → Task 3. §4.3 (backup.bat) → Task 2.
  §4.4 (restore) → Task 4. §4.5 (build-native) → Task 5. §4.6 (NSIS install/startmenu/
  uninstall-backup) → Task 6. §7 testing → Tasks 1, 8. Docs (§8 deliverables) → Task 7.
- Decisions E1 (offline backup) → Task 1 handler. E2 (no scheduled task) → nothing to build,
  documented in Task 7. E3 (db-only default, `--full` opt-in) → Task 1 `--full` flag. E4
  (no pip/vcredist) → Task 3 has only 4 steps. E5 (registry detection) → Task 3 `Get-RegValue`.
  E6 (additive robocopy, keep Uninstall.exe) → Task 3 `Step-CopyFiles` `/XF Uninstall.exe`.

**Type/name consistency:** Service name `ZettelwirtschaftBackend` and registry path
`HKLM\SOFTWARE\Zettelwirtschaft` with keys `InstallDir`/`DataDir`/`ConfigPath` are used
identically across Tasks 2/3/4/6. Backend exe path `backend\zettelwirtschaft-backend.exe`
consistent. Backup zip internal layout `database/zettelwirtschaft.db` + `documents/` matches
`backup_service.create_backup` (verified in source) and is mirrored by the Task 4 restore.
```
