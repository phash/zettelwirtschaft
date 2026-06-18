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
    # M4: ZIP-Eintraege vor dem Entpacken auf Path-Traversal pruefen (zip-slip).
    # -BackupZip ist beliebig vom Nutzer waehlbar; ein praepariertes Archiv
    # koennte sonst Dateien ausserhalb von $tmp schreiben.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipRead = [System.IO.Compression.ZipFile]::OpenRead($BackupZip)
    try {
        foreach ($entry in $zipRead.Entries) {
            $en = $entry.FullName
            if ($en -match '\.\.[\\/]' -or $en -match '^([A-Za-z]:|[\\/])') {
                throw "Unsicherer Pfad im Backup-ZIP (abgebrochen): $en"
            }
        }
    } finally { $zipRead.Dispose() }

    Expand-Archive -LiteralPath $BackupZip -DestinationPath $tmp -Force
    $srcDb = Join-Path $tmp 'database\zettelwirtschaft.db'
    if (-not (Test-Path $srcDb)) {
        Write-Host 'FEHLER: Kein database/zettelwirtschaft.db im Backup-ZIP.'
        exit 1
    }

    Write-Host 'Stoppe Dienst...'
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        try { $svc.WaitForStatus('Stopped', (New-TimeSpan -Seconds 60)) }
        catch { throw "Dienst '$ServiceName' konnte nicht gestoppt werden - Restore abgebrochen (Datenbank unveraendert)." }
    }

    foreach ($sfx in '-wal', '-shm') {
        $side = "$dbPath$sfx"
        if (Test-Path $side) { Remove-Item -LiteralPath $side -Force }
    }
    $dbParent = Split-Path -Parent $dbPath
    if ($dbParent) { New-Item -ItemType Directory -Force -Path $dbParent | Out-Null }
    Copy-Item -LiteralPath $srcDb -Destination $dbPath -Force
    Write-Host 'Datenbank wiederhergestellt.'

    $srcDocs = Join-Path $tmp 'documents'
    if (Test-Path $srcDocs) {
        if ($archiveDir) {
            $archiveWin = $archiveDir -replace '/', '\'
            Write-Host 'Stelle Dokumente wieder her...'
            # H1: Das aktuelle Archiv NICHT mit dem Backup mergen (sonst weichen
            # Archiv und wiederhergestellte DB voneinander ab - Dateien ohne
            # DB-Eintrag bleiben als Waisen liegen). Vorhandenes Archiv zur Seite
            # legen (nicht loeschen - bei unvollstaendigem ZIP waere das
            # Datenverlust) und frisch aus dem Backup befuellen.
            if ((Test-Path $archiveWin) -and (Get-ChildItem -LiteralPath $archiveWin -Force -ErrorAction SilentlyContinue)) {
                $aside = "${archiveWin}.pre-restore-$(Get-Date -Format yyyyMMdd_HHmmss)"
                Rename-Item -LiteralPath $archiveWin -NewName (Split-Path -Leaf $aside)
                Write-Host "Bisheriges Archiv gesichert nach: $aside"
            }
            New-Item -ItemType Directory -Force -Path $archiveWin | Out-Null
            Copy-Item -Path (Join-Path $srcDocs '*') -Destination $archiveWin -Recurse -Force
        } else {
            Write-Host 'WARNUNG: documents/ im Backup, aber ARCHIVE_DIR fehlt in config.toml - Dokumente NICHT wiederhergestellt.'
        }
    } else {
        Write-Host 'Hinweis: DB-only-Backup (kein documents/) - nur die Datenbank wurde wiederhergestellt.'
    }

    Write-Host 'Starte Dienst...'
    $svcStart = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svcStart) {
        try {
            Start-Service -Name $ServiceName -ErrorAction Stop
            $svcStart.WaitForStatus('Running', (New-TimeSpan -Seconds 120))
            Write-Host 'Dienst laeuft.'
        } catch {
            Write-Host "WARNUNG: Dienst konnte nicht gestartet werden: $_"
            Write-Host 'Pruefe logs\backend.log im Datenordner.'
        }
    } else {
        Write-Host "WARNUNG: Dienst $ServiceName nicht gefunden - bitte manuell starten."
    }

    Write-Host ''
    Write-Host 'Restore abgeschlossen.'
    Write-Host 'Hinweis: Falls die Vektor-Suche Treffer vermissen laesst, in den'
    Write-Host "Einstellungen -> Wartung 'Vektor-Index neu aufbauen' ausfuehren."
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
