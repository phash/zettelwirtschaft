# Zettelwirtschaft - Restore aus Backup (Offline). Windows PowerShell 5.1.
# Stoppt den Dienst, ersetzt die DB (+ optional Dokumente) aus einem Backup-ZIP,
# startet den Dienst. Destruktiv -> explizite Tippbestaetigung.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupZip,
    [string]$ConfigPath = "",
    # Datenordner, in dem DB und Archiv liegen muessen (INFRA-01). Standard:
    # DataDir aus HKLM\SOFTWARE\Zettelwirtschaft bzw. der Ordner der config.toml.
    [string]$DataDir = ""
)

$ErrorActionPreference = 'Stop'
$ServiceName = 'ZettelwirtschaftBackend'

function Get-RegValue([string]$name) {
    # Fallback WOW6432Node: aeltere Setups schrieben als 32-Bit-Prozess dorthin.
    foreach ($key in @('HKLM:\SOFTWARE\Zettelwirtschaft', 'HKLM:\SOFTWARE\WOW6432Node\Zettelwirtschaft')) {
        try {
            $val = (Get-ItemProperty $key -Name $name -ErrorAction Stop).$name
            if ($val) { return $val }
        } catch { }
    }
    return $null
}

if (-not (Test-Path $BackupZip)) { Write-Host "FEHLER: Backup nicht gefunden: $BackupZip"; exit 1 }
if (-not $ConfigPath) { $ConfigPath = Get-RegValue 'ConfigPath' }
if (-not $ConfigPath -or -not (Test-Path $ConfigPath)) {
    Write-Host "FEHLER: config.toml nicht gefunden ($ConfigPath). Bitte -ConfigPath angeben."
    exit 1
}

# --- config.toml parsen ---
# config.toml ist UTF-8 ohne BOM - ohne -Encoding liest 5.1 sie als ANSI und
# macht aus Umlauten im Pfad (C:\Users\J<ue>rgen) Mojibake (Restore in einen falschen Pfad).
$cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
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

# INFRA-01: Der Restore laeuft mit Administratorrechten und verschiebt/ueberschreibt
# die Ziele aus der config.toml. Liegt die (z.B. bei aelteren Installationen in
# C:\Zettelwirtschaft) fuer andere Konten beschreibbar, liesse sich ARCHIVE_DIR auf
# den Programmordner richten -> das Backup-ZIP ersetzt die Dienst-Exe (SYSTEM).
# Deshalb: DB und Archiv nur innerhalb des Datenordners, nie im Programmordner.
function Test-IsWithin([string]$child, [string]$root) {
    $c = [System.IO.Path]::GetFullPath($child).TrimEnd('\') + '\'
    $r = [System.IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    return $c.StartsWith($r, [System.StringComparison]::OrdinalIgnoreCase)
}
$allowedRoots = @()
if ($DataDir) {
    $allowedRoots += $DataDir
} else {
    $regDataDir = Get-RegValue 'DataDir'
    if ($regDataDir) { $allowedRoots += $regDataDir }
    $allowedRoots += (Split-Path -Parent ([System.IO.Path]::GetFullPath($ConfigPath)))
}
$installDir = Get-RegValue 'InstallDir'
$targets = @(@{ Label = 'DATABASE_URL'; Path = $dbPath })
if ($archiveDir) { $targets += @{ Label = 'ARCHIVE_DIR'; Path = ($archiveDir -replace '/', '\') } }
foreach ($t in $targets) {
    $p = $t.Path
    $bad = $null
    if (-not $p -or -not [System.IO.Path]::IsPathRooted($p) -or $p.StartsWith('\\')) {
        $bad = 'kein absoluter lokaler Pfad'
    } elseif (-not @($allowedRoots | Where-Object { (Test-IsWithin $p $_) -and ([System.IO.Path]::GetFullPath($p).TrimEnd('\') -ne [System.IO.Path]::GetFullPath($_).TrimEnd('\')) })) {
        $bad = "liegt nicht innerhalb des Datenordners ($($allowedRoots -join ', '))"
    } elseif ($installDir -and (Test-IsWithin $p $installDir)) {
        $bad = "liegt im Programmordner ($installDir)"
    }
    if ($bad) {
        Write-Host "FEHLER: Restore-Ziel $($t.Label) = $p $bad - abgebrochen (config.toml pruefen, ggf. -DataDir angeben)."
        exit 1
    }
}

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
