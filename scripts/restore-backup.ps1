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
