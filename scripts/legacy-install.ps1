# legacy-install.ps1 - Manuell eingerichtete Native-Installation uebernehmen.
#
# Hintergrund: Vor dem Native-Setup (und beim Umstieg Docker -> Native nach
# CLAUDE.md / planung/native-windows-konzept.md) wurde der Dienst
# ZettelwirtschaftBackend von Hand per service-install.bat registriert. Solche
# Installationen haben KEINEN Eintrag unter HKLM\SOFTWARE\Zettelwirtschaft;
# setup-native.nsi erkennt sie ueber den Dienst und uebernimmt sie ("Adoption").
# Die alte Programmversion (ggf. 1.2.x/1.3.x) wird dabei NICHT ausgefuehrt -
# alte Exes kennen --backup/--out-dir teils nicht.
#
# Aktionen (setup-native.nsi ruft das Skript aus $PLUGINSDIR auf):
#   Detect          Dienst-Konfiguration lesen (Registry, Dienst muss NICHT laufen)
#                   und Pfade ableiten. Ergebnis als INI in -ResultFile.
#   StopService     Dienst beenden und warten, bis er steht (bereits gestoppt = ok).
#                   WasRunning=1/0 sagt dem Rollback, ob er wieder zu starten ist.
#   Backup          SQLite-DB (+ -wal/-shm) und config.toml als ZIP sichern, ohne
#                   die alte Exe; ZIP wird nach dem Schreiben geprueft.
#   SetFrontendDir  Nur die Zeile FRONTEND_DIST_DIR der config.toml ersetzen, alle
#                   anderen Bytes bleiben unveraendert (Kodierung, BOM, CRLF).
#                   Atomar (Temp-Datei + ReplaceFile, ACL bleibt): ein Stromausfall
#                   hinterlaesst nie eine halbe config.toml (PIN, Fernet-Key).
#   WaitHealth      Warten, bis der Dienst laeuft und /api/health antwortet.
#   RemoveOldDir    Alten Programmordner aufraeumen: NUR bekannte Programmdateien
#                   (backend\_internal + Exe, frontend, bin, nssm.exe, Skripte ...);
#                   alles andere bleibt liegen, Verknuepfungen werden nie verfolgt.
#
# Ergebnisdatei: INI mit Abschnitt [result], UTF-16LE mit BOM. NSIS liest sie mit
# ReadINIStr (GetPrivateProfileStringW) verlustfrei - auch Pfade mit Umlauten,
# die ueber nsExec::ExecToStack (OEM-/ANSI-Codepage) verstuemmelt ankaemen.
# Warum die Registry statt "sc qc"/"nssm get": sc-Ausgaben sind lokalisiert
# ("ERFOLG"), nssm get gibt UTF-16 aus und braucht die alte nssm.exe. NSSM legt
# seine Einstellungen unter <Dienst>\Parameters ab (Application, AppDirectory,
# AppEnvironmentExtra, AppStdout, ...); das liest jeder Benutzer.
# Tests: -ServicesRoot zeigt auf einen Stub-Schluessel in HKCU (test-scripts.ps1).
#
# Exit 0 = Aktion erfolgreich, 1 = Fehler (Grund als Reason=... in -ResultFile).
# Windows PowerShell 5.1 und PowerShell 7 kompatibel; reines ASCII (keine BOM noetig).

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Detect', 'StopService', 'Backup', 'SetFrontendDir', 'WaitHealth', 'RemoveOldDir')]
    [string]$Action,
    [string]$ResultFile = '',
    [string]$ServiceName = 'ZettelwirtschaftBackend',
    [string]$ServicesRoot = 'HKLM:\SYSTEM\CurrentControlSet\Services',
    [string]$ConfigPath = '',
    [string]$DbPath = '',
    [string]$OutDir = '',
    [string]$Version = '',
    # SetFrontendDir: neuer Wert; leer = FRONTEND_DIST_DIR = "" (Rollback).
    [string]$FrontendDir = '',
    # RemoveOldDir: bisheriger Programmordner.
    [string]$OldDir = '',
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'

function Write-ZwResult([System.Collections.IDictionary]$values) {
    if (-not $ResultFile) { return }
    $lines = @('[result]')
    foreach ($k in $values.Keys) {
        $v = [string]$values[$k]
        $lines += ($k + '=' + ($v -replace '[\r\n]+', ' '))
    }
    $dir = Split-Path -Parent $ResultFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    # UTF-16LE mit BOM: GetPrivateProfileStringW liest dann Unicode.
    [System.IO.File]::WriteAllText($ResultFile, (($lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::Unicode)
}

function Get-ZwFullPath([string]$path) {
    if (-not $path) { return '' }
    return [System.IO.Path]::GetFullPath($path.Trim().Trim('"')).TrimEnd('\')
}

# $child gleich $root oder darunter (ohne Gross-/Kleinschreibung).
function Test-ZwWithin([string]$child, [string]$root) {
    if (-not $child -or -not $root) { return $false }
    $c = (Get-ZwFullPath $child) + '\'
    $r = (Get-ZwFullPath $root) + '\'
    return $c.StartsWith($r, [System.StringComparison]::OrdinalIgnoreCase)
}

# Exe-Pfad aus einer Dienst-ImagePath-Zeile (mit/ohne Anfuehrungszeichen, Argumente).
function Get-ZwImageExe([string]$imagePath) {
    $p = [Environment]::ExpandEnvironmentVariables(([string]$imagePath).Trim())
    if (-not $p) { return '' }
    if ($p.StartsWith('"')) {
        $end = $p.IndexOf('"', 1)
        if ($end -gt 1) { return $p.Substring(1, $end - 1) }
        return $p.Trim('"')
    }
    $i = $p.ToLowerInvariant().IndexOf('.exe')
    if ($i -gt 0) { return $p.Substring(0, $i + 4) }
    return $p
}

# Wert eines TOML-Strings (Basic "..." oder Literal '...'); $null = Schluessel fehlt.
function Get-ZwTomlString([string]$text, [string]$key) {
    $rx = '(?m)^[ \t]*' + [regex]::Escape($key) + '[ \t]*=[ \t]*(?:"((?:[^"\\\r\n]|\\.)*)"|''([^''\r\n]*)'')'
    $m = [regex]::Match($text, $rx)
    if (-not $m.Success) { return $null }
    if ($m.Groups[2].Success) { return $m.Groups[2].Value }
    $nul = [string][char]0
    return $m.Groups[1].Value.Replace('\\', $nul).Replace('\"', '"').Replace($nul, '\')
}

function Read-ZwConfigText([string]$path) {
    # UTF-8 (mit oder ohne BOM); config.toml enthaelt PIN + Fernet-Key -> nie ausgeben.
    return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
}

function Get-ZwLegacyInstall {
    $r = [ordered]@{ Status = 'none'; Reason = '' }
    $svcKey = Join-Path $ServicesRoot $ServiceName
    if (-not (Test-Path -LiteralPath $svcKey)) { return $r }
    $r.Status = 'foreign'
    $svc = Get-ItemProperty -LiteralPath $svcKey
    $nssm = Get-ZwImageExe ([string]$svc.ImagePath)
    $r.Nssm = $nssm
    $parKey = Join-Path $svcKey 'Parameters'
    if (-not $nssm -or ([System.IO.Path]::GetFileName($nssm) -notlike 'nssm*.exe') -or -not (Test-Path -LiteralPath $parKey)) {
        $r.Reason = 'not-nssm'
        return $r
    }
    $par = Get-ItemProperty -LiteralPath $parKey
    $app = [string]$par.Application
    $appDir = [string]$par.AppDirectory
    $r.Application = $app
    $r.AppDirectory = $appDir
    $r.AppStdout = [string]$par.AppStdout
    $r.AppStderr = [string]$par.AppStderr
    $r.OllamaDependency = if (@($svc.DependOnService) -contains 'Ollama') { '1' } else { '0' }
    if (-not $app) { $r.Reason = 'no-application'; return $r }

    # Programmordner: <alt>\backend\zettelwirtschaft-backend.exe -> <alt>
    $appParent = Split-Path -Parent $app
    $oldDir = if ((Split-Path -Leaf $appParent) -ieq 'backend') { Split-Path -Parent $appParent } else { $appParent }
    $oldDir = Get-ZwFullPath $oldDir
    $r.OldInstallDir = $oldDir
    if (-not $appDir) { $appDir = $appParent }

    $config = ''
    foreach ($entry in @($par.AppEnvironmentExtra)) {
        if ([string]$entry -match '^\s*ZETTELWIRTSCHAFT_CONFIG=(.+)$') { $config = $Matches[1].Trim().Trim('"') }
    }
    if (-not $config) { $r.Reason = 'no-config-env'; return $r }
    $config = Get-ZwFullPath $config
    $r.ConfigPath = $config
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { $r.Reason = 'config-missing'; return $r }

    $text = Read-ZwConfigText $config
    $dbUrl = Get-ZwTomlString $text 'DATABASE_URL'
    # Fehlende Schluessel = Backend-Default ./data/..., relativ zum Arbeitsordner
    # des Dienstes (AppDirectory).
    if (-not $dbUrl) { $dbUrl = 'sqlite+aiosqlite:///./data/zettelwirtschaft.db' }
    $m = [regex]::Match($dbUrl, '^sqlite(?:\+aiosqlite)?:///(.+)$')
    if (-not $m.Success) { $r.Reason = 'db-url'; return $r }
    $db = $m.Groups[1].Value.Replace('/', '\')
    $relative = -not [System.IO.Path]::IsPathRooted($db)
    if ($relative) { $db = Join-Path $appDir $db }
    $db = Get-ZwFullPath $db
    $r.DbPath = $db
    # Relative Datenpfade zeigen nach der Uebernahme in den NEUEN Programmordner
    # (AppDirectory wechselt) -> Datenbank/Archiv waeren scheinbar leer. Nicht
    # uebernehmen; die config.toml muss vorher auf absolute Pfade umgestellt werden.
    foreach ($k in 'UPLOAD_DIR', 'ARCHIVE_DIR', 'THUMBNAIL_DIR', 'WATCH_DIR') {
        $v = Get-ZwTomlString $text $k
        if (-not $v -or -not [System.IO.Path]::IsPathRooted($v.Replace('/', '\'))) { $relative = $true }
    }
    $chroma = Get-ZwTomlString $text 'CHROMADB_PATH'
    if ($chroma -and -not [System.IO.Path]::IsPathRooted($chroma.Replace('/', '\'))) { $relative = $true }
    if ($relative) { $r.Reason = 'relative-paths'; return $r }

    # Datenordner wie beim Setup: <Datenordner>\data\zettelwirtschaft.db,
    # sonst der Ordner der config.toml.
    $dbDir = Split-Path -Parent $db
    $dataDir = if ((Split-Path -Leaf $dbDir) -ieq 'data') { Split-Path -Parent $dbDir } else { Split-Path -Parent $config }
    $r.DataDir = Get-ZwFullPath $dataDir
    $logDir = if ($r.AppStdout) { Split-Path -Parent (Get-ZwFullPath $r.AppStdout) } else { Join-Path $r.DataDir 'logs' }
    $r.LogDir = Get-ZwFullPath $logDir

    $fe = Get-ZwTomlString $text 'FRONTEND_DIST_DIR'
    $r.FrontendKey = if ($null -eq $fe) { '0' } else { '1' }
    $r.FrontendDistDir = [string]$fe

    $ver = 'unbekannt'
    $verFile = Join-Path $oldDir 'VERSION'
    if (Test-Path -LiteralPath $verFile -PathType Leaf) {
        $first = @(Get-Content -LiteralPath $verFile -TotalCount 1)
        if ($first.Count -gt 0 -and ([string]$first[0]).Trim() -match '^[0-9A-Za-z.+-]{1,40}$') { $ver = ([string]$first[0]).Trim() }
    }
    $r.OldVersion = $ver

    # Liegt irgendetwas, das bleiben muss, im alten Programmordner? Dann darf er
    # nach der Uebernahme nicht geloescht werden.
    $keep = @($r.DataDir, $config, $db, $r.LogDir, $r.AppStdout, $r.AppStderr)
    foreach ($k in 'UPLOAD_DIR', 'ARCHIVE_DIR', 'THUMBNAIL_DIR', 'WATCH_DIR', 'EXPORT_DIR', 'CHROMADB_PATH', 'SERVER_SSL_CERTFILE', 'SERVER_SSL_KEYFILE') {
        $v = Get-ZwTomlString $text $k
        if ($v) {
            $v = $v.Replace('/', '\')
            if (-not [System.IO.Path]::IsPathRooted($v)) { $v = Join-Path $appDir $v }
            $keep += $v
        }
    }
    $inside = @($keep | Where-Object { $_ -and (Test-ZwWithin $_ $oldDir) }).Count -gt 0
    $r.DataInsideOldDir = if ($inside) { '1' } else { '0' }

    # Loeschen nur anbieten, wenn der Ordner eindeutig ein alter Programmordner ist:
    # keine Daten darin, keine Laufwerkswurzel / kein Profil- oder Systemordner,
    # und er enthaelt die alte Backend-Exe oder nssm.exe.
    # ProgramW6432: das 32-Bit-Setup startet die 32-Bit-PowerShell (SysWOW64),
    # dort ist $env:ProgramFiles = "C:\Program Files (x86)".
    $protected = @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)},
        $env:ProgramW6432, $env:ProgramData, $env:SystemRoot, $env:PUBLIC, [Environment]::GetFolderPath('MyDocuments')) |
        Where-Object { $_ } | ForEach-Object { Get-ZwFullPath $_ }
    $isRoot = ([System.IO.Path]::GetPathRoot($oldDir + '\').TrimEnd('\') -eq $oldDir)
    $looksLikeProgram = (Test-Path -LiteralPath (Join-Path $oldDir 'backend\zettelwirtschaft-backend.exe')) -or (Test-Path -LiteralPath (Join-Path $oldDir 'nssm.exe'))
    $deletable = (-not $inside) -and (-not $isRoot) -and $looksLikeProgram -and -not ($protected -contains $oldDir)
    $r.OldDirDeletable = if ($deletable) { '1' } else { '0' }

    $r.Status = 'adoptable'
    return $r
}

function Invoke-ZwStopService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { return [ordered]@{ Status = 'ok'; Reason = 'not-installed'; WasRunning = '0' } }
    # War der Dienst schon gestoppt (vom Nutzer angehalten), startet ihn ein
    # Rollback auch nicht wieder.
    $wasRunning = if ($svc.Status -ne 'Stopped') { '1' } else { '0' }
    if ($svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds($TimeoutSeconds)) }
        catch { return [ordered]@{ Status = 'error'; Reason = 'timeout'; WasRunning = $wasRunning } }
    }
    return [ordered]@{ Status = 'ok'; Reason = ''; WasRunning = $wasRunning }
}

function Add-ZwZipFile($zip, [string]$source, [string]$entryName) {
    # FileShare.Read: schlaegt fehl, solange noch ein Prozess die Datei zum
    # Schreiben offen hat (Dienst nicht wirklich beendet) -> kein halbes Backup.
    $in = New-Object System.IO.FileStream($source, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $entry = $zip.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
        $out = $entry.Open()
        try { $in.CopyTo($out) } finally { $out.Dispose() }
    } finally { $in.Dispose() }
}

function Invoke-ZwBackup {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (-not $DbPath -or -not (Test-Path -LiteralPath $DbPath -PathType Leaf)) { return [ordered]@{ Status = 'error'; Reason = 'db-missing' } }
    if (-not $ConfigPath -or -not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { return [ordered]@{ Status = 'error'; Reason = 'config-missing' } }
    if (-not $OutDir) { return [ordered]@{ Status = 'error'; Reason = 'no-out-dir' } }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $tag = if ($Version) { $Version -replace '[^0-9A-Za-z.+-]', '_' } else { 'update' }
    $zipPath = Join-Path $OutDir ('pre-{0}_{1}.zip' -f $tag, (Get-Date -Format 'yyyyMMdd_HHmmss'))
    if (Test-Path -LiteralPath $zipPath) { $zipPath = $zipPath -replace '\.zip$', ('_' + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.zip') }

    # Gleiche Struktur wie die Backups des Backends (database/zettelwirtschaft.db),
    # damit restore-backup.bat das ZIP direkt zurueckspielen kann.
    $files = [ordered]@{ 'database/zettelwirtschaft.db' = $DbPath; 'config.toml' = $ConfigPath }
    foreach ($sfx in '-wal', '-shm') {
        if (Test-Path -LiteralPath ($DbPath + $sfx) -PathType Leaf) { $files["database/zettelwirtschaft.db$sfx"] = $DbPath + $sfx }
    }
    try {
        $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($name in $files.Keys) { Add-ZwZipFile $zip $files[$name] $name }
        } finally { $zip.Dispose() }

        # Pruefen: ZIP oeffnet, enthaelt alle Dateien in voller Groesse, DB beginnt
        # mit dem SQLite-Header.
        $check = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            foreach ($name in $files.Keys) {
                $e = $check.GetEntry($name)
                if (-not $e) { throw "Eintrag $name fehlt" }
                if ($e.Length -ne (Get-Item -LiteralPath $files[$name]).Length) { throw "Eintrag $name unvollstaendig" }
            }
            $s = $check.GetEntry('database/zettelwirtschaft.db').Open()
            try {
                $head = New-Object byte[] 16
                $n = $s.Read($head, 0, 16)
            } finally { $s.Dispose() }
            if ($n -ne 16 -or [System.Text.Encoding]::ASCII.GetString($head, 0, 15) -ne 'SQLite format 3') { throw 'Datenbank im ZIP ist keine SQLite-Datei' }
        } finally { $check.Dispose() }
    } catch {
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
        # New-Object/Methodenaufrufe verpacken die IOException (MethodInvocationException).
        $reason = 'verify'
        for ($ex = $_.Exception; $ex; $ex = $ex.InnerException) { if ($ex -is [System.IO.IOException]) { $reason = 'locked-or-io'; break } }
        [Console]::Error.WriteLine("[FEHLER] Backup: $($_.Exception.Message)")
        return [ordered]@{ Status = 'error'; Reason = $reason }
    }
    # Das ZIP enthaelt PIN + Fernet-Key (config.toml): Rechte wie die config.toml
    # selbst (SYSTEM, Administratoren, dieses Konto). Best effort.
    $acl = Join-Path $PSScriptRoot 'secure-acl.ps1'
    if (Test-Path -LiteralPath $acl) {
        try { . $acl; $null = Protect-ZwPath -LiteralPath $zipPath } catch { Write-Warning "Zugriffsrechte des Backups nicht eingeschraenkt: $($_.Exception.Message)" }
    }
    return [ordered]@{ Status = 'ok'; Reason = ''; Path = $zipPath }
}

function Invoke-ZwSetFrontendDir {
    if (-not $ConfigPath -or -not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { return [ordered]@{ Status = 'error'; Reason = 'config-missing' } }
    $bytes = [System.IO.File]::ReadAllBytes($ConfigPath)
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $offset = if ($bom) { 3 } else { 0 }
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $text = $utf8.GetString($bytes, $offset, $bytes.Length - $offset)

    # Nur der Wert wird ersetzt; Einrueckung, Leerzeichen, ein Kommentar hinter
    # dem Wert und das Zeilenende bleiben erhalten.
    $rx = [regex]'(?m)^([ \t]*FRONTEND_DIST_DIR[ \t]*=[ \t]*)("(?:[^"\\\r\n]|\\.)*"|''[^''\r\n]*''|[^\r\n#]*?)([ \t]*(?:#[^\r\n]*)?)(?=\r?$)'
    $m = $rx.Match($text)
    if (-not $m.Success) { return [ordered]@{ Status = 'missing'; Reason = '' } }
    $path = $FrontendDir.Trim().Trim('"').Replace('\', '/').TrimEnd('/')
    if ($path -match '[\x00-\x1F\x7F]') { return [ordered]@{ Status = 'error'; Reason = 'invalid-path' } }
    $value = '"' + $path.Replace('\', '\\').Replace('"', '\"') + '"'
    if ($m.Groups[2].Value -ceq $value) { return [ordered]@{ Status = 'unchanged'; Reason = '' } }
    $new = $text.Substring(0, $m.Groups[2].Index) + $value + $text.Substring($m.Groups[2].Index + $m.Groups[2].Length)

    $body = $utf8.GetBytes($new)
    if ($bom) { $body = [byte[]](0xEF, 0xBB, 0xBF) + $body }
    # Atomar ersetzen statt in-place ueberschreiben: WriteAllBytes kuerzt die Datei
    # erst auf 0 Byte - ein Stromausfall/Absturz mittendrin liesse eine leere
    # config.toml zurueck (PIN, EMAIL_ENCRYPTION_KEY verloren). ReplaceFile tauscht
    # die Datei in einem Schritt und uebernimmt die ACL der bisherigen config.toml
    # (SEC-DATA-03). Temp-Datei im selben Ordner (ReplaceFile braucht dasselbe
    # Volume). Schlaegt das fehl, bleibt die config.toml unveraendert.
    $tmp = $ConfigPath + '.zw-tmp-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    try {
        [System.IO.File]::WriteAllBytes($tmp, [byte[]]$body)
        # [NullString]::Value statt $null: PowerShell wandelt $null bei einem
        # string-Parameter in "" um -> "Der Pfad hat ein ungueltiges Format".
        [System.IO.File]::Replace($tmp, $ConfigPath, [NullString]::Value)
    } catch {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        [Console]::Error.WriteLine("[FEHLER] SetFrontendDir: $($_.Exception.Message)")
        return [ordered]@{ Status = 'error'; Reason = 'replace' }
    }
    return [ordered]@{ Status = 'updated'; Reason = '' }
}

function Invoke-ZwWaitHealth {
    $port = 8080
    $scheme = 'http'
    if ($ConfigPath -and (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        $text = Read-ZwConfigText $ConfigPath
        $pm = [regex]::Match($text, '(?m)^[ \t]*SERVER_PORT[ \t]*=[ \t]*(\d+)')
        if ($pm.Success) { $port = [int]$pm.Groups[1].Value }
        if (Get-ZwTomlString $text 'SERVER_SSL_CERTFILE') { $scheme = 'https' }
    }
    if ($scheme -eq 'https') {
        # Selbstsigniertes Zertifikat auf localhost - nur fuer diese Pruefung.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    }
    $url = "${scheme}://127.0.0.1:$port/api/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $checkService = [bool](Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)
    do {
        if ($checkService) {
            $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
            if (-not $svc -or $svc.Status -eq 'Stopped') { return [ordered]@{ Status = 'error'; Reason = 'service-stopped'; Url = $url } }
        }
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { return [ordered]@{ Status = 'ok'; Reason = ''; Url = $url } }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return [ordered]@{ Status = 'error'; Reason = 'timeout'; Url = $url }
}

# Programmdateien, die ein Native-Bundle (dist\native bzw. eine manuelle
# Installation) im Programmordner ablegt. Nur diese loescht RemoveOldDir.
$ZwProgramFiles = @('nssm.exe', 'VERSION', 'service-install.bat', 'service-uninstall.bat', 'config.toml.example',
    'Convert-Env-To-Config.ps1', 'New-NativeConfig.ps1', 'model-tiers.ps1', 'new-secret.ps1', 'secure-acl.ps1',
    'secure-install-dir.ps1', 'legacy-install.ps1', 'update-wizard.bat', 'update-wizard.ps1', 'backup.bat',
    'restore-backup.bat', 'restore-backup.ps1', 'backend\zettelwirtschaft-backend.exe')

# Rekursiv loeschen, ohne Verknuepfungen (Junction/Symlink) zu folgen oder zu
# entfernen: sie und ein Ziel ausserhalb bleiben unangetastet. Gibt die Anzahl
# nicht loeschbarer Eintraege zurueck.
function Remove-ZwTree([string]$path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return 0 }
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return 1 }
    $failed = 0
    if ($item.PSIsContainer) {
        try { $children = @(Get-ChildItem -LiteralPath $path -Force) } catch { return 1 }
        foreach ($c in $children) { $failed += Remove-ZwTree $c.FullName }
        if ($failed -eq 0) {
            try { Remove-Item -LiteralPath $path -Force } catch { $failed++ }
        }
    } else {
        try { Remove-Item -LiteralPath $path -Force } catch { $failed++ }
    }
    return $failed
}

function Invoke-ZwRemoveOldDir {
    if (-not $OldDir -or -not (Test-Path -LiteralPath $OldDir -PathType Container)) { return [ordered]@{ Status = 'error'; Reason = 'no-old-dir' } }
    $old = Get-ZwFullPath $OldDir
    if ([System.IO.Path]::GetPathRoot($old + '\').TrimEnd('\') -eq $old) { return [ordered]@{ Status = 'error'; Reason = 'root' } }
    # Zweite Sicherung neben DataInsideOldDir aus Detect: aktive Config/DB nie hier.
    foreach ($p in $ConfigPath, $DbPath) {
        if ($p -and (Test-ZwWithin $p $old)) { return [ordered]@{ Status = 'error'; Reason = 'data-inside' } }
    }
    foreach ($name in $ZwProgramFiles) {
        $p = Join-Path $old $name
        if (Test-Path -LiteralPath $p -PathType Leaf) { $null = Remove-ZwTree $p }
    }
    # backend: nur das PyInstaller-Bundle (_internal); z.B. ein altes backend\data
    # aus Zeiten relativer Pfade bleibt liegen.
    $null = Remove-ZwTree (Join-Path $old 'backend\_internal')
    foreach ($d in 'frontend', 'bin') { $null = Remove-ZwTree (Join-Path $old $d) }
    foreach ($d in (Join-Path $old 'backend'), $old) {
        if ((Test-Path -LiteralPath $d -PathType Container) -and -not @(Get-ChildItem -LiteralPath $d -Force)) {
            try { Remove-Item -LiteralPath $d -Force } catch { }
        }
    }
    if (-not (Test-Path -LiteralPath $old)) { return [ordered]@{ Status = 'removed'; Reason = '' } }
    $left = @(Get-ChildItem -LiteralPath $old -Force | Select-Object -First 8 | ForEach-Object { $_.Name })
    return [ordered]@{ Status = 'partial'; Reason = ''; Remaining = ($left -join ', ') }
}

try {
    switch ($Action) {
        'Detect' { $res = Get-ZwLegacyInstall }
        'StopService' { $res = Invoke-ZwStopService }
        'Backup' { $res = Invoke-ZwBackup }
        'SetFrontendDir' { $res = Invoke-ZwSetFrontendDir }
        'WaitHealth' { $res = Invoke-ZwWaitHealth }
        'RemoveOldDir' { $res = Invoke-ZwRemoveOldDir }
    }
} catch {
    [Console]::Error.WriteLine("[FEHLER] ${Action}: $($_.Exception.Message)")
    $res = [ordered]@{ Status = 'error'; Reason = 'exception' }
}
Write-ZwResult $res
Write-Output "[$Action] Status=$($res.Status) $($res.Reason)"
if ($res.Status -eq 'error') { exit 1 }
exit 0
