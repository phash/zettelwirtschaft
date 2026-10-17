# scripts/test-scripts.ps1 - Smoke-Tests fuer Installer- und Betriebs-Skripte.
#
# Faengt die Fehlerklasse, die frueher unbemerkt ausgeliefert wurde: ungueltige
# config.toml aus dem Native-Setup, Batch-Syntaxfehler (backup.bat), UTF-8-BOM
# unter Windows PowerShell 5.1, YAML-Backslashes in generate-mounts.ps1 usw.
#
# Verwendung (Windows):
#   pwsh scripts/test-scripts.ps1
# Optionale Werkzeuge werden genutzt, wenn vorhanden (sonst SKIP):
#   makensis (NSIS), docker (nur CLI, "docker compose config" braucht keinen
#   Daemon), PSScriptAnalyzer.
#
# Sicherheit: alles laeuft in einem Temp-Ordner. Es wird KEIN Dienst, keine
# echte Registry-Installation (nur HKCU\Software\ZwScriptTest) und kein
# laufender Server angefasst; der Health-Test nutzt einen freien Zufallsport.

param(
    [switch]$KeepTemp
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("zw-script-tests-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $Temp | Out-Null

$script:Failures = @()
$script:Skips = @()

function Invoke-Test([string]$name, [scriptblock]$body) {
    try {
        & $body
        Write-Host "[PASS] $name" -ForegroundColor Green
    } catch {
        if ($_.Exception.Message -like 'SKIP:*') {
            Write-Host "[SKIP] $name - $($_.Exception.Message.Substring(5).Trim())" -ForegroundColor Yellow
            $script:Skips += $name
        } else {
            Write-Host "[FAIL] $name" -ForegroundColor Red
            Write-Host "       $($_.Exception.Message)" -ForegroundColor Red
            $script:Failures += $name
        }
    }
}

function Assert-True([bool]$cond, [string]$msg) { if (-not $cond) { throw $msg } }
function Skip([string]$why) { throw "SKIP: $why" }

$WinPS = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Python = Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
$MakeNsis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
if (-not $MakeNsis -and (Test-Path "${env:ProgramFiles(x86)}\NSIS\makensis.exe")) { $MakeNsis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
$Docker = (Get-Command docker -ErrorAction SilentlyContinue).Source

# Windows PowerShell 5.1 ausfuehren (das einzige PowerShell auf Kundenrechnern).
function Invoke-WinPS([string]$file, [string[]]$arguments) {
    $out = & $WinPS -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $file @arguments 2>&1
    return @{ Code = $LASTEXITCODE; Output = (@($out) -join "`n") }
}

# TOML mit Pythons tomllib parsen (wie das Backend). Gibt das Dict als JSON-Objekt zurueck.
function Read-Toml([string]$path) {
    if (-not $Python) { Skip 'python nicht gefunden' }
    $json = & $Python -c "import json,sys,tomllib; print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))))" $path 2>&1
    if ($LASTEXITCODE -ne 0) { throw "tomllib konnte $path nicht lesen: $json" }
    return ($json | ConvertFrom-Json)
}

function Test-HasBom([string]$path) {
    $b = [System.IO.File]::ReadAllBytes($path)
    return ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
}

$umlautDir = "J" + [char]0x00FC + "rgen M" + [char]0x00FC + "ller"   # Pfad mit Umlauten

# ---------------------------------------------------------------- PowerShell
$psFiles = @(Get-ChildItem -Path $RepoRoot -Filter *.ps1 -File) + @(Get-ChildItem -Path (Join-Path $RepoRoot 'scripts') -Filter *.ps1 -File)

Invoke-Test 'PowerShell: alle Skripte parsen unter Windows PowerShell 5.1 und PowerShell 7' {
    $list = ($psFiles | ForEach-Object { $_.FullName }) -join '|'
    $cmd = "`$bad = 0; foreach (`$f in '$list'.Split('|')) { `$t = `$null; `$e = `$null; [void][System.Management.Automation.Language.Parser]::ParseFile(`$f, [ref]`$t, [ref]`$e); foreach (`$x in `$e) { Write-Output (`$f + ': ' + `$x.Message); `$bad++ } }; exit `$bad"
    $out = & $WinPS -NoProfile -NonInteractive -Command $cmd
    Assert-True ($LASTEXITCODE -eq 0) "Parse-Fehler unter 5.1:`n$($out -join "`n")"
    foreach ($f in $psFiles) {
        $t = $null; $e = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$t, [ref]$e)
        Assert-True ($e.Count -eq 0) "Parse-Fehler in $($f.Name): $($e[0].Message)"
    }
}

Invoke-Test 'PowerShell: Skripte mit Nicht-ASCII-Zeichen haben eine UTF-8-BOM (sonst liest 5.1 sie als ANSI)' {
    foreach ($f in $psFiles) {
        $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
        $nonAscii = $false
        foreach ($b in $bytes) { if ($b -gt 0x7F) { $nonAscii = $true; break } }
        if (-not $nonAscii) { continue }
        # Kommentare sind unkritisch; nur Zeichen ausserhalb von Kommentaren zaehlen.
        $t = $null; $e = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$t, [ref]$e)
        $codeNonAscii = @($t | Where-Object { $_.Kind -ne 'Comment' -and $_.Text -match '[^\x00-\x7F]' })
        if ($codeNonAscii.Count -gt 0) {
            Assert-True (Test-HasBom $f.FullName) "$($f.Name) enthaelt Nicht-ASCII-Text ausserhalb von Kommentaren, aber keine UTF-8-BOM"
        }
    }
}

Invoke-Test 'PowerShell: PSScriptAnalyzer ohne Fehler' {
    if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { Skip 'PSScriptAnalyzer nicht installiert (Install-Module PSScriptAnalyzer)' }
    $res = @($psFiles | ForEach-Object { Invoke-ScriptAnalyzer -Path $_.FullName -Severity Error })
    Assert-True ($res.Count -eq 0) (($res | ForEach-Object { "$($_.ScriptName):$($_.Line) $($_.RuleName) $($_.Message)" }) -join "`n")
}

# ---------------------------------------------------------------- Batch
$batFiles = @(Get-ChildItem -Path $RepoRoot -Filter *.bat -File) + @(Get-ChildItem -Path (Join-Path $RepoRoot 'scripts') -Filter *.bat -File)

Invoke-Test 'Batch: keine unescapten Klammern in echo-Texten innerhalb von ( )-Bloecken' {
    $problems = @()
    foreach ($f in $batFiles) {
        $depth = 0
        $n = 0
        foreach ($line in [System.IO.File]::ReadAllLines($f.FullName)) {
            $n++
            $code = $line.Trim()
            if ($code -match '^(::|rem\b|@?echo\s+off)' -or $code -eq '') { continue }
            # Escapte Klammern und gequotete Teile ignorieren
            $plain = ($code -replace '\^[()]', '') -replace '"[^"]*"', '""'
            if ($code -match '^echo[\s.]' -and $depth -gt 0) {
                if ($plain -match '\)') { $problems += "$($f.Name):$n  $code" }
            }
            $depth += ([regex]::Matches($plain, '\(')).Count - ([regex]::Matches($plain, '\)')).Count
            if ($depth -lt 0) { $depth = 0 }
        }
    }
    Assert-True ($problems.Count -eq 0) ("Unescapte ')' in Block-echo (beendet den Block -> Syntaxfehler):`n" + ($problems -join "`n"))
}

Invoke-Test 'Batch: kein "!" in echo-Texten bei enabledelayedexpansion' {
    $problems = @()
    foreach ($f in $batFiles) {
        $lines = [System.IO.File]::ReadAllLines($f.FullName)
        if (-not ($lines -match '(?i)enabledelayedexpansion')) { continue }
        $n = 0
        foreach ($line in $lines) {
            $n++
            if ($line.Trim() -match '^echo\s' -and ($line -replace '![A-Za-z0-9_]+!', '') -match '!') { $problems += "$($f.Name):$n" }
        }
    }
    Assert-True ($problems.Count -eq 0) ("'!' wird bei delayed expansion verschluckt:`n" + ($problems -join "`n"))
}

# Stub fuer zettelwirtschaft-backend.exe: protokolliert Argumente, Exit-Code per ENV.
$stubDir = Join-Path $Temp 'stub\inst\backend'
New-Item -ItemType Directory -Force -Path $stubDir | Out-Null
$stubExe = Join-Path $stubDir 'zettelwirtschaft-backend.exe'
$stubSrc = @'
using System; using System.IO;
public static class ZwStub { public static int Main(string[] a) {
  string log = Environment.GetEnvironmentVariable("ZW_STUB_LOG");
  if (!String.IsNullOrEmpty(log)) File.AppendAllText(log, String.Join(" ", a) + Environment.NewLine);
  string rc = Environment.GetEnvironmentVariable("ZW_STUB_RC");
  return String.IsNullOrEmpty(rc) ? 0 : Int32.Parse(rc); } }
'@
$stubCs = Join-Path $Temp 'stub.cs'
[System.IO.File]::WriteAllText($stubCs, $stubSrc)
$null = & $WinPS -NoProfile -NonInteractive -Command "Add-Type -Path '$stubCs' -OutputAssembly '$stubExe' -OutputType ConsoleApplication"

Invoke-Test 'backup.bat: laeuft mit Stub-Exe (Registry-Werte, /full, Exit-Code, Log)' {
    Assert-True (Test-Path $stubExe) 'Stub-Exe konnte nicht kompiliert werden'
    $regKey = 'HKCU:\Software\ZwScriptTest'
    $dataDir = Join-Path $Temp 'stub\data'
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    New-Item -Path $regKey -Force | Out-Null
    try {
        Set-ItemProperty -Path $regKey -Name InstallDir -Value (Join-Path $Temp 'stub\inst')
        Set-ItemProperty -Path $regKey -Name ConfigPath -Value (Join-Path $dataDir 'config.toml')
        Set-ItemProperty -Path $regKey -Name DataDir -Value $dataDir
        $argLog = Join-Path $Temp 'stub\args.txt'
        $env:ZW_REGKEY = 'HKCU\Software\ZwScriptTest'
        $env:ZW_STUB_LOG = $argLog
        $out = cmd /c "`"$RepoRoot\scripts\backup.bat`" /full < NUL" 2>&1
        $rc = $LASTEXITCODE
        Assert-True ($rc -eq 0) "backup.bat /full Exit $rc (255 = Syntaxfehler):`n$($out -join "`n")"
        $args1 = (Get-Content $argLog -Raw).Trim()
        Assert-True ($args1 -match '--backup --full$' -and $args1 -match [regex]::Escape("--config $dataDir\config.toml")) "Falsche Argumente: $args1"
        Assert-True ((Get-Content (Join-Path $dataDir 'logs\backup.log') -Raw) -match 'Exit 0') 'backup.log fehlt oder ohne Exit-Code'

        $env:ZW_STUB_RC = '3'
        $out = cmd /c "`"$RepoRoot\scripts\backup.bat`" < NUL" 2>&1
        Assert-True ($LASTEXITCODE -eq 3) "Fehler-Exit-Code nicht durchgereicht (Exit $LASTEXITCODE)"
        Assert-True (($out -join "`n") -match 'Backup fehlgeschlagen \(Exit 3\)') "Fehlermeldung fehlt:`n$($out -join "`n")"

        Remove-Item Env:\ZW_STUB_RC
        Set-ItemProperty -Path $regKey -Name ConfigPath -Value ''
        $out = cmd /c "`"$RepoRoot\scripts\backup.bat`" < NUL" 2>&1
        Assert-True ($LASTEXITCODE -eq 1) "Fehlender ConfigPath: Exit $LASTEXITCODE statt 1"
    } finally {
        Remove-Item Env:\ZW_REGKEY, Env:\ZW_STUB_LOG, Env:\ZW_STUB_RC -ErrorAction SilentlyContinue
        Remove-Item -Path $regKey -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- Native-Konfiguration
Invoke-Test 'new-secret.ps1 (5.1): PIN 6 Ziffern, Fernet-Key 44 Zeichen URL-safe' {
    $pin = Invoke-WinPS (Join-Path $RepoRoot 'scripts\new-secret.ps1') @('-Kind', 'pin')
    Assert-True ($pin.Code -eq 0 -and $pin.Output.Trim() -match '^\d{6}$') "PIN ungueltig: $($pin.Output)"
    $key = Invoke-WinPS (Join-Path $RepoRoot 'scripts\new-secret.ps1') @('-Kind', 'fernet')
    Assert-True ($key.Code -eq 0 -and $key.Output.Trim() -match '^[A-Za-z0-9_-]{43}=$') "Key ungueltig: $($key.Output)"
}

Invoke-Test 'New-NativeConfig.ps1 (5.1): gueltige TOML ohne BOM, Umlaut-Pfad, Fernet-Key' {
    $dataRoot = Join-Path $Temp "native\$umlautDir"
    $cfg = Join-Path $dataRoot 'config.toml'
    $r = Invoke-WinPS (Join-Path $RepoRoot 'scripts\New-NativeConfig.ps1') @('-OutPath', $cfg, '-DataDir', "$dataRoot\", '-FrontendDistDir', 'C:\Program Files\Zettelwirtschaft\frontend', '-Port', '8090', '-PinCode', '123456', '-Version', '9.9.9')
    Assert-True ($r.Code -eq 0) "Exit $($r.Code): $($r.Output)"
    Assert-True (-not (Test-HasBom $cfg)) 'config.toml hat eine BOM'
    $d = Read-Toml $cfg
    $expectedData = ($dataRoot -replace '\\', '/') + '/data'
    Assert-True ($d.DATABASE_URL -eq "sqlite+aiosqlite:///$expectedData/zettelwirtschaft.db") "DATABASE_URL: $($d.DATABASE_URL)"
    Assert-True ($d.FRONTEND_DIST_DIR -eq 'C:/Program Files/Zettelwirtschaft/frontend') "FRONTEND_DIST_DIR: $($d.FRONTEND_DIST_DIR)"
    Assert-True ($d.SERVER_PORT -eq 8090 -and $d.PIN_ENABLED -eq $true -and $d.PIN_CODE -eq '123456') 'Port/PIN falsch'
    Assert-True ($d.EMAIL_ENCRYPTION_KEY -match '^[A-Za-z0-9_-]{43}=$') "EMAIL_ENCRYPTION_KEY: $($d.EMAIL_ENCRYPTION_KEY)"

    $cfg2 = Join-Path $Temp 'native\noui.toml'
    $r = Invoke-WinPS (Join-Path $RepoRoot 'scripts\New-NativeConfig.ps1') @('-OutPath', $cfg2, '-DataDir', 'D:\Daten', '-DisablePin')
    Assert-True ($r.Code -eq 0) "ohne Frontend/PIN: Exit $($r.Code): $($r.Output)"
    $d2 = Read-Toml $cfg2
    Assert-True ($d2.FRONTEND_DIST_DIR -eq '' -and $d2.PIN_ENABLED -eq $false) 'leeres FRONTEND_DIST_DIR / PIN_ENABLED=false falsch'

    $r = Invoke-WinPS (Join-Path $RepoRoot 'scripts\New-NativeConfig.ps1') @('-OutPath', (Join-Path $Temp 'native\bad.toml'), '-DataDir', 'D:\x')
    Assert-True ($r.Code -ne 0 -and -not (Test-Path (Join-Path $Temp 'native\bad.toml'))) 'PIN-Schutz ohne PIN wurde nicht abgelehnt'
}

Invoke-Test 'Convert-Env-To-Config.ps1 (5.1): .env -> gueltige TOML, Werte uebernommen' {
    $dir = Join-Path $Temp "convert\$umlautDir"
    New-Item -ItemType Directory -Force -Path (Join-Path $dir 'data') | Out-Null
    $key = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa='
    $envPath = Join-Path $dir '.env'
    [System.IO.File]::WriteAllText($envPath, "FRONTEND_PORT=8081`r`nPIN_ENABLED=true`r`nPIN_CODE=4711`r`nOLLAMA_MODEL=llama3.2`r`nEMAIL_ENCRYPTION_KEY=$key`r`n")
    $out = Join-Path $dir 'config.toml'
    $r = Invoke-WinPS (Join-Path $RepoRoot 'scripts\Convert-Env-To-Config.ps1') @('-EnvPath', $envPath, '-OutPath', $out, '-DataDir', (Join-Path $dir 'data'), '-FrontendDistDir', 'C:\ZW\frontend')
    Assert-True ($r.Code -eq 0) "Exit $($r.Code): $($r.Output)"
    Assert-True (-not (Test-HasBom $out)) 'config.toml hat eine BOM (Out-File -Encoding utf8 unter 5.1)'
    $d = Read-Toml $out
    Assert-True ($d.SERVER_PORT -eq 8081 -and $d.PIN_CODE -eq '4711' -and $d.OLLAMA_MODEL -eq 'llama3.2' -and $d.EMAIL_ENCRYPTION_KEY -eq $key) 'Werte nicht uebernommen'
    Assert-True ($d.ARCHIVE_DIR -eq (($dir -replace '\\', '/') + '/data/archive')) "ARCHIVE_DIR: $($d.ARCHIVE_DIR)"

    [System.IO.File]::WriteAllText($envPath, "PIN_ENABLED=true`r`nPIN_CODE=`r`n")
    $r = Invoke-WinPS (Join-Path $RepoRoot 'scripts\Convert-Env-To-Config.ps1') @('-EnvPath', $envPath, '-OutPath', (Join-Path $dir 'bad.toml'), '-DataDir', (Join-Path $dir 'data'))
    Assert-True ($r.Code -ne 0) 'PIN_ENABLED mit leerem PIN wurde nicht abgelehnt'
}

# ---------------------------------------------------------------- NSIS
Invoke-Test 'setup-native.nsi: kompiliert mit /WX (Warnungen = Fehler)' {
    if (-not $MakeNsis) { Skip 'makensis nicht gefunden' }
    $dist = Join-Path $Temp 'nsis\dist'
    foreach ($d in 'backend', 'frontend', 'bin') { New-Item -ItemType Directory -Force -Path (Join-Path $dist $d) | Out-Null; Set-Content (Join-Path $dist "$d\x.txt") 'x' }
    foreach ($f in 'nssm.exe', 'service-install.bat', 'service-uninstall.bat', 'config.toml.example', 'VERSION', 'update-wizard.bat', 'update-wizard.ps1', 'backup.bat', 'restore-backup.bat', 'restore-backup.ps1') {
        Set-Content (Join-Path $dist $f) 'x'
    }
    foreach ($f in 'new-secret.ps1', 'New-NativeConfig.ps1', 'Convert-Env-To-Config.ps1') { Copy-Item (Join-Path $RepoRoot "scripts\$f") $dist }
    $outExe = Join-Path $Temp 'nsis\zw-native-test.exe'
    Push-Location $RepoRoot
    try { $o = & $MakeNsis /WX /V2 /DVERSION=0.0.0-test "/DDIST=$dist" "/DOUTFILE=$outExe" setup-native.nsi 2>&1 } finally { Pop-Location }
    Assert-True ($LASTEXITCODE -eq 0 -and (Test-Path $outExe)) "makensis Exit $($LASTEXITCODE):`n$($o -join "`n")"
}

Invoke-Test 'setup-native-helpers.nsh: GenerateSecret liefert gueltigen PIN + Fernet-Key (echter NSIS-Lauf)' {
    if (-not $MakeNsis) { Skip 'makensis nicht gefunden' }
    $dir = Join-Path $Temp 'nsis-harness'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $result = Join-Path $dir 'result.txt'
    $nsi = @"
Unicode True
!include "LogicLib.nsh"
!include "$RepoRoot\setup-native-helpers.nsh"
Name "ZwSecretTest"
OutFile "$dir\harness.exe"
RequestExecutionLevel user
SilentInstall silent
Section
    InitPluginsDir
    File "/oname=`$PLUGINSDIR\new-secret.ps1" "$RepoRoot\scripts\new-secret.ps1"
    Push "pin"
    Call GenerateSecret
    Pop `$1
    Push "fernet"
    Call GenerateSecret
    Pop `$2
    FileOpen `$0 "$result" w
    FileWrite `$0 "PIN=`$1`$\r`$\nKEY=`$2`$\r`$\n"
    FileClose `$0
SectionEnd
"@
    [System.IO.File]::WriteAllText((Join-Path $dir 'harness.nsi'), $nsi)
    $o = & $MakeNsis /WX /V2 (Join-Path $dir 'harness.nsi') 2>&1
    Assert-True ($LASTEXITCODE -eq 0) "makensis Harness:`n$($o -join "`n")"
    $p = Start-Process -FilePath (Join-Path $dir 'harness.exe') -Wait -PassThru
    Assert-True ($p.ExitCode -eq 0 -and (Test-Path $result)) 'Harness lief nicht'
    $txt = Get-Content $result -Raw
    Assert-True ($txt -match '(?m)^PIN=\d{6}\r?$') "PIN: $txt"
    Assert-True ($txt -match '(?m)^KEY=[A-Za-z0-9_-]{43}=\r?$') "KEY: $txt"
}

Invoke-Test 'Release: Manifest vollstaendig, Setup.exe (Docker) kompiliert aus dem gestagten Ordner' {
    if (-not $Python) { Skip 'python nicht gefunden' }
    $stage = Join-Path $Temp 'release'
    $o = & $Python (Join-Path $RepoRoot 'scripts\release_manifest.py') stage $stage 2>&1
    Assert-True ($LASTEXITCODE -eq 0) "stage: $o"
    # Dateien, die start.bat/update.bat/HTTPS zur Laufzeit brauchen, muessen dabei sein.
    foreach ($f in 'generate-mounts.ps1', 'wait-health.ps1', 'docker-compose.ssl.yml', 'ssl\nginx-ssl.conf', 'ssl\generate-self-signed-cert.py') {
        Assert-True (Test-Path (Join-Path $stage $f)) "$f fehlt im Release-Paket"
    }
    foreach ($bat in 'start.bat', 'update.bat') {
        foreach ($m in [regex]::Matches((Get-Content (Join-Path $RepoRoot $bat) -Raw), '-File\s+([\w.-]+\.ps1)')) {
            Assert-True (Test-Path (Join-Path $stage $m.Groups[1].Value)) "$bat ruft $($m.Groups[1].Value) auf, das nicht im Release-Paket ist"
        }
    }
    if (-not $MakeNsis) { Skip 'makensis nicht gefunden (Manifest ok)' }
    Copy-Item (Join-Path $RepoRoot 'setup.nsi') $stage
    Push-Location $stage
    try { $o = & $MakeNsis /WX /V2 /DVERSION=0.0.0-test setup.nsi 2>&1 } finally { Pop-Location }
    Assert-True ($LASTEXITCODE -eq 0) "makensis setup.nsi:`n$($o -join "`n")"
}

# ---------------------------------------------------------------- Docker-Skripte
function New-ComposeProject([string]$name) {
    $p = Join-Path $Temp $name
    New-Item -ItemType Directory -Force -Path (Join-Path $p 'data'), (Join-Path $p 'ssl') | Out-Null
    foreach ($f in 'docker-compose.yml', 'docker-compose.ssl.yml', 'generate-mounts.ps1', 'wait-health.ps1') { Copy-Item (Join-Path $RepoRoot $f) $p }
    Copy-Item (Join-Path $RepoRoot 'ssl\nginx-ssl.conf') (Join-Path $p 'ssl')
    [System.IO.File]::WriteAllText((Join-Path $p '.env'), "FRONTEND_PORT=8080`r`nPIN_ENABLED=false`r`n")
    return $p
}

function Get-ComposeJson([string]$dir) {
    Push-Location $dir
    $enc = [Console]::OutputEncoding
    # docker gibt UTF-8 aus; sonst dekodiert PowerShell mit der OEM-Codepage.
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    try { $j = & $Docker compose config --format json 2>&1 } finally { [Console]::OutputEncoding = $enc; Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "docker compose config: $j" }
    return (($j -join "`n") | ConvertFrom-Json)
}

Invoke-Test 'generate-mounts.ps1 (5.1): Backslash-/Apostroph-Pfade, GPU-Block bleibt, compose config gueltig' {
    if (-not $Docker) { Skip 'docker-CLI nicht gefunden' }
    $p = New-ComposeProject 'compose1'
    $gpu = "services:`n  ollama:`n    deploy:`n      resources:`n        reservations:`n          devices:`n            - driver: nvidia`n              count: all`n              capabilities: [gpu]`n"
    [System.IO.File]::WriteAllText((Join-Path $p 'docker-compose.override.yml'), $gpu)
    $mounts = @{ watch = 'V:\Zettelwirtschaft\eingang'; export = "D:\Docs\O'Brien\$umlautDir" } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $p 'data\.host-mounts.json'), $mounts, (New-Object System.Text.UTF8Encoding($false)))
    $r = Invoke-WinPS (Join-Path $p 'generate-mounts.ps1') @()
    Assert-True ($r.Code -eq 0) "Exit $($r.Code): $($r.Output)"
    $cfg = Get-ComposeJson $p
    $vols = @($cfg.services.backend.volumes | Where-Object { $_.target -like '/app/external/*' })
    Assert-True ($vols.Count -eq 2) "Erwartet 2 Host-Mounts, gefunden $($vols.Count)"
    Assert-True (($vols | Where-Object { $_.target -eq '/app/external/export' }).source -eq "D:\Docs\O'Brien\$umlautDir") 'Export-Pfad verfaelscht'
    Assert-True ($null -ne $cfg.services.ollama.deploy) 'GPU-Block ging verloren'

    # Ordner geleert (Backend loescht .host-mounts.json): Mounts muessen verschwinden.
    Remove-Item (Join-Path $p 'data\.host-mounts.json')
    $r = Invoke-WinPS (Join-Path $p 'generate-mounts.ps1') @()
    Assert-True ($r.Code -eq 0) "Exit $($r.Code): $($r.Output)"
    $cfg = Get-ComposeJson $p
    Assert-True (@($cfg.services.backend.volumes | Where-Object { $_.target -like '/app/external/*' }).Count -eq 0) 'Alte Host-Mounts blieben stehen'
    Assert-True ($null -ne $cfg.services.ollama.deploy) 'GPU-Block ging beim Leeren verloren'

    # Ohne GPU-Block und ohne Mounts: Override wird entfernt.
    Remove-Item (Join-Path $p 'docker-compose.override.yml')
    $r = Invoke-WinPS (Join-Path $p 'generate-mounts.ps1') @()
    Assert-True ($r.Code -eq 0 -and -not (Test-Path (Join-Path $p 'docker-compose.override.yml'))) 'Leeres Override wurde angelegt'
}

Invoke-Test 'generate-mounts.ps1: HTTPS-Overlay ueber COMPOSE_FILE, 8080 nicht mehr veroeffentlicht' {
    if (-not $Docker) { Skip 'docker-CLI nicht gefunden' }
    $p = New-ComposeProject 'compose2'
    Set-Content (Join-Path $p 'ssl\cert.pem') 'x'
    Set-Content (Join-Path $p 'ssl\key.pem') 'x'
    $r = Invoke-WinPS (Join-Path $p 'generate-mounts.ps1') @()
    Assert-True ($r.Code -eq 0) "Exit $($r.Code): $($r.Output)"
    $envText = [System.IO.File]::ReadAllText((Join-Path $p '.env'))
    Assert-True ($envText -match '(?m)^COMPOSE_FILE=docker-compose\.yml;docker-compose\.ssl\.yml\r?$') ".env: $envText"
    $cfg = Get-ComposeJson $p
    $published = @($cfg.services.frontend.ports | ForEach-Object { [string]$_.published })
    Assert-True (($published -join ',') -eq '80,443') "Ports: $($published -join ',')"
    Assert-True ($cfg.services.backend.environment.TRUSTED_PROXIES -eq $cfg.networks.'proxy-net'.ipam.config[0].subnet) 'TRUSTED_PROXIES != proxy-net-Subnetz'

    # Zertifikat entfernt: zurueck auf HTTP.
    Remove-Item (Join-Path $p 'ssl\cert.pem')
    $r = Invoke-WinPS (Join-Path $p 'generate-mounts.ps1') @()
    Assert-True ($r.Code -eq 0 -and -not ([System.IO.File]::ReadAllText((Join-Path $p '.env')) -match 'COMPOSE_FILE')) 'COMPOSE_FILE blieb nach Entfernen des Zertifikats'
    $cfg = Get-ComposeJson $p
    Assert-True ((@($cfg.services.frontend.ports | ForEach-Object { [string]$_.published }) -join ',') -eq '8080') 'HTTP-Port 8080 fehlt'
}

Invoke-Test 'wait-health.ps1 (5.1): Erfolg ueber FRONTEND_PORT, Timeout mit Exit 1, -UrlOnly HTTP/HTTPS' {
    if (-not $Python) { Skip 'python nicht gefunden' }
    $p = Join-Path $Temp 'health'
    New-Item -ItemType Directory -Force -Path (Join-Path $p 'api') | Out-Null
    Set-Content (Join-Path $p 'api\health') '{"status":"ok"}'
    Copy-Item (Join-Path $RepoRoot 'wait-health.ps1') $p
    # Freien Port waehlen (nie 8080/11434/11435 - dort laufen echte Dienste).
    $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0); $l.Start(); $port = $l.LocalEndpoint.Port; $l.Stop()
    [System.IO.File]::WriteAllText((Join-Path $p '.env'), "FRONTEND_PORT=$port`r`n")
    $srv = Start-Process -FilePath $Python -ArgumentList '-m', 'http.server', $port, '--bind', '127.0.0.1', '--directory', $p -PassThru -WindowStyle Hidden
    try {
        $r = Invoke-WinPS (Join-Path $p 'wait-health.ps1') @('-TimeoutSeconds', '20')
        Assert-True ($r.Code -eq 0 -and $r.Output -match "http://localhost:$port") "Exit $($r.Code): $($r.Output)"
    } finally {
        Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
    }
    $r = Invoke-WinPS (Join-Path $p 'wait-health.ps1') @('-TimeoutSeconds', '3')
    Assert-True ($r.Code -eq 1) "Timeout: Exit $($r.Code) statt 1"

    # -UrlOnly (install-gui.ps1): dieselbe HTTP/HTTPS-Erkennung ohne Wartezeit.
    $r = Invoke-WinPS (Join-Path $p 'wait-health.ps1') @('-UrlOnly')
    Assert-True ($r.Code -eq 0 -and $r.Output.Trim() -eq "http://localhost:$port") "UrlOnly (HTTP): $($r.Output)"
    [System.IO.File]::WriteAllText((Join-Path $p '.env'), "FRONTEND_PORT=$port`r`nCOMPOSE_PATH_SEPARATOR=;`r`nCOMPOSE_FILE=docker-compose.yml;docker-compose.ssl.yml`r`n")
    $r = Invoke-WinPS (Join-Path $p 'wait-health.ps1') @('-UrlOnly')
    Assert-True ($r.Code -eq 0 -and $r.Output.Trim() -eq 'https://localhost') "UrlOnly (HTTPS): $($r.Output)"
}

# ---------------------------------------------------------------- Update-Wizard
Invoke-Test 'update-wizard.ps1: Step-CopyFiles spiegelt backend/frontend/bin, behaelt Uninstall.exe' {
    # Nur die Kopier-Funktion aus dem Skript laden (kein Dienst-Zugriff).
    $wizardPath = Join-Path $RepoRoot 'scripts\update-wizard.ps1'
    $t = $null; $e = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($wizardPath, [ref]$t, [ref]$e)
    $need = 'Step-CopyFiles'
    $fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $need }, $true) | Select-Object -First 1
    $assigns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -in '$VersionOwnedDirs', '$RobocopyQuiet' }, $false)
    Assert-True ($null -ne $fn -and @($assigns).Count -eq 2) 'Step-CopyFiles / $VersionOwnedDirs / $RobocopyQuiet nicht gefunden'

    $src = Join-Path $Temp 'wizard\src'; $inst = Join-Path $Temp 'wizard\inst'
    foreach ($d in 'backend', 'frontend\assets', 'bin') { New-Item -ItemType Directory -Force -Path (Join-Path $src $d), (Join-Path $inst $d) | Out-Null }
    Set-Content (Join-Path $src 'backend\new.pyd') 'neu'
    Set-Content (Join-Path $src 'frontend\assets\app-NEW.js') 'neu'
    Set-Content (Join-Path $src 'VERSION') '2.0.0'
    Set-Content (Join-Path $inst 'backend\stale.pyd') 'alt'
    Set-Content (Join-Path $inst 'frontend\assets\app-OLD.js') 'alt'
    Set-Content (Join-Path $inst 'Uninstall.exe') 'uninst'
    Set-Content (Join-Path $inst 'eigene-notiz.txt') 'bleibt'

    # Seiteneffekte ausserhalb der Testordner abklemmen (Registry-Version, GUI-Log).
    $prelude = 'function Set-StepStatus { }; function Write-Log { }; function Set-ItemProperty { }; $DryRun = $false; $NewVersion = ''2.0.0'''
    $defs = (@($assigns) | ForEach-Object { $_.Extent.Text }) -join "`n"
    $code = "$prelude`n$defs`n$($fn.Extent.Text)`n`$WizardDirResolved = '$src'; `$InstallDir = '$inst'; Step-CopyFiles"
    $ok = & ([scriptblock]::Create($code))
    Assert-True ($ok -eq $true) 'Step-CopyFiles meldete Fehler'
    Assert-True (Test-Path (Join-Path $inst 'backend\new.pyd')) 'neue Datei fehlt'
    Assert-True (-not (Test-Path (Join-Path $inst 'backend\stale.pyd'))) 'veraltete .pyd blieb liegen'
    Assert-True (-not (Test-Path (Join-Path $inst 'frontend\assets\app-OLD.js'))) 'veraltetes Asset blieb liegen'
    Assert-True ((Test-Path (Join-Path $inst 'Uninstall.exe')) -and (Test-Path (Join-Path $inst 'eigene-notiz.txt'))) 'Dateien im Hauptordner wurden entfernt'

    # Unvollstaendiges Paket (bin fehlt) darf nichts spiegeln.
    Remove-Item -Recurse -Force (Join-Path $src 'bin')
    Set-Content (Join-Path $inst 'backend\keep.pyd') 'x'
    $ok = & ([scriptblock]::Create($code))
    Assert-True ($ok -eq $false -and (Test-Path (Join-Path $inst 'backend\keep.pyd'))) 'Unvollstaendiges Paket wurde trotzdem gespiegelt'
}

# ---------------------------------------------------------------- Ergebnis
if (-not $KeepTemp) { Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue } else { Write-Host "Temp: $Temp" }
Write-Host ''
if ($script:Failures.Count -gt 0) {
    Write-Host "$($script:Failures.Count) Test(s) fehlgeschlagen: $($script:Failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "Alle Skript-Tests bestanden ($($script:Skips.Count) uebersprungen)." -ForegroundColor Green
exit 0
