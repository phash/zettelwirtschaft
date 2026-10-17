# fetch-build-tools.ps1 - Laed alle Native-Build-Dependencies in tools/.
#
# Idempotent: vorhandene Tools werden uebersprungen (aber gegen die Pins geprueft).
# Aufruf:
#   pwsh scripts/fetch-build-tools.ps1
#   pwsh scripts/fetch-build-tools.ps1 -Force                 # alles neu laden
#   pwsh scripts/fetch-build-tools.ps1 -Components nssm,poppler  # selektiv
#   pwsh scripts/fetch-build-tools.ps1 -VerifyOnly            # nur tools/ pruefen (build-native.ps1)
#
# Erfasste Komponenten:
#   - poppler-windows  (PDF-Konvertierung fuer pdf2image)
#   - NSSM             (Windows-Service-Wrapper)
#   - Tesseract OCR    (deu+eng Sprachpakete)
#
# INFRA-04 (Supply-Chain): Diese Programme laufen beim Kunden als LocalSystem
# (Dienst, OCR jedes Dokuments). Deshalb KEINE "latest"-Releases und keine
# dynamische Versionsaufloesung: Version, URL und SHA-256 jedes Downloads sind
# unten fest gepinnt, zusaetzlich die SHA-256 der ausgelieferten Kern-Binaries
# in tools/ (erkennt einen manipulierten bzw. von Hand befuellten tools/-Ordner).
# Ein Versions-Update = $BuildToolPins anpassen (neue Hashes aus einem
# vertrauenswuerdigen Download bestimmen und im Commit begruenden).
#
# Lizenz-Hinweise: die heruntergeladenen Tools haben jeweils eigene Lizenzen
# (Apache-2.0 fuer Tesseract, GPL-2.0 fuer poppler, Public Domain fuer NSSM).
# build-native.ps1 packt sie in das Setup.exe - Lizenztexte werden im
# Installer-Verzeichnis mit ausgeliefert.

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$VerifyOnly,
    [string[]]$Components = @("poppler", "nssm", "tesseract")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest sonst extrem langsam

$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$ToolsDir = if ($env:ZW_TOOLS_DIR) { $env:ZW_TOOLS_DIR } else { Join-Path $RepoRoot "tools" }
$TmpDir = Join-Path $ToolsDir ".tmp"

# Gepinnte Downloads (Archiv-Hash) und ausgelieferte Binaries (Datei-Hash,
# relativ zu tools/). Die Datei-Hashes stammen aus genau diesen Archiven.
$BuildToolPins = @{
    poppler = @{
        Version = 'v26.02.0-0'
        Urls    = @('https://github.com/oschwartz10612/poppler-windows/releases/download/v26.02.0-0/Release-26.02.0-0.zip')
        File    = 'Release-26.02.0-0.zip'
        Sha256  = '993e4a94376ed712fafc7058d724ea0b943d118bbd2305cd9ed55174eb85cda5'
        Installed = @{ 'poppler\Library\bin\pdftoppm.exe' = '575a2f66073b256f69084c4f12f34427e6076b7854b2505cb09ecbdc9957fd7f' }
    }
    nssm = @{
        Version = '2.24'
        Urls    = @('https://nssm.cc/release/nssm-2.24.zip')
        File    = 'nssm-2.24.zip'
        Sha256  = '727d1e42275c605e0f04aba98095c38a8e1e46def453cdffce42869428aa6743'
        Installed = @{ 'nssm\win64\nssm.exe' = 'f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97' }
    }
    tesseract = @{
        Version = '5.4.0.20240606'
        # GitHub-Release zuerst (bleibt dauerhaft), der UB-Mannheim-Mirror loescht alte Builds.
        Urls    = @(
            'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe',
            'https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe'
        )
        File    = 'tesseract-ocr-w64-setup-5.4.0.20240606.exe'
        Sha256  = 'c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9'
        Installed = @{
            'tesseract\tesseract.exe'            = 'babb405f4366b480d02cd8ff2bac8d497170f6c1711ce6f3d5d8bf0fb7fa6ed9'
            'tesseract\tessdata\deu.traineddata' = '896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4'
        }
    }
    # Deutsche Sprachdaten: getaggter Stand statt "main".
    tessdataDeu = @{
        Version = '4.1.0'
        Urls    = @('https://github.com/tesseract-ocr/tessdata/raw/4.1.0/deu.traineddata')
        File    = 'deu.traineddata'
        Sha256  = '896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4'
    }
}

# ---------- Helper ----------
function Get-Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-PinnedFile {
    # Laedt eine gepinnte Datei (oder nimmt die gecachte) und prueft IMMER den
    # SHA-256 - auch im Cache. Bei Abweichung wird die Datei verworfen.
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Pin,
        [Parameter(Mandatory = $true)] [string]$OutFile
    )
    if (-not $Pin.Sha256) { throw "Kein SHA-256-Pin fuer $($Pin.File) - Download verweigert." }
    if ((Test-Path -LiteralPath $OutFile) -and -not $Force) {
        if ((Get-Sha256 $OutFile) -eq $Pin.Sha256) {
            Write-Host "  Cached (SHA256 OK): $(Split-Path -Leaf $OutFile)" -ForegroundColor DarkGray
            return
        }
        Write-Warning "  Cache $(Split-Path -Leaf $OutFile) hat einen falschen SHA-256 - lade neu"
        Remove-Item -LiteralPath $OutFile -Force
    }
    $lastError = $null
    foreach ($url in $Pin.Urls) {
        Write-Host "  Download: $url" -ForegroundColor Yellow
        try {
            Invoke-WebRequest -Uri $url -OutFile $OutFile -UseBasicParsing
        } catch {
            $lastError = $_
            Write-Warning "  nicht ladbar: $url ($_)"
            continue
        }
        $actual = Get-Sha256 $OutFile
        if ($actual -eq $Pin.Sha256) {
            Write-Host "  SHA256 OK" -ForegroundColor DarkGreen
            return
        }
        Remove-Item -LiteralPath $OutFile -Force
        throw "SHA256-Mismatch fuer $url`n  erwartet: $($Pin.Sha256)`n  erhalten: $actual`nDownload verworfen (manipulierte oder geaenderte Quelle?)."
    }
    throw "Download fehlgeschlagen fuer $($Pin.File): $lastError"
}

function Test-InstalledPins {
    # Prueft die ausgelieferten Binaries einer Komponente gegen die Pins.
    # Rueckgabe: Liste der Abweichungen ("<pfad>: fehlt" / "<pfad>: SHA-256 ...").
    param([Parameter(Mandatory = $true)] [string]$Component)
    $problems = @()
    foreach ($rel in $BuildToolPins[$Component].Installed.Keys) {
        $path = Join-Path $ToolsDir $rel
        if (-not (Test-Path -LiteralPath $path)) { $problems += "${rel}: fehlt"; continue }
        $actual = Get-Sha256 $path
        $expected = $BuildToolPins[$Component].Installed[$rel]
        if ($actual -ne $expected) { $problems += "${rel}: SHA-256 $actual, erwartet $expected" }
    }
    return , $problems
}

function Assert-InstalledPins([string]$Component) {
    $problems = Test-InstalledPins -Component $Component
    if ($problems.Count -gt 0) {
        throw "tools/ ($Component) entspricht nicht den Pins:`n  $($problems -join "`n  ")`nNeu laden: pwsh scripts/fetch-build-tools.ps1 -Force -Components $Component"
    }
}

function Expand-Strict {
    param([string]$ArchivePath, [string]$Destination)
    if (Test-Path $Destination) {
        Remove-Item -Recurse -Force $Destination
    }
    New-Item -ItemType Directory -Path $Destination | Out-Null
    Expand-Archive -Path $ArchivePath -DestinationPath $Destination -Force
}

# ---------- Nur pruefen (build-native.ps1) ----------
if ($VerifyOnly) {
    $failed = @()
    foreach ($c in $Components) {
        $problems = Test-InstalledPins -Component $c
        if ($problems.Count -gt 0) {
            $failed += $c
            foreach ($p in $problems) { Write-Host "[FEHLER] ${c}: $p" -ForegroundColor Red }
        } else {
            Write-Host "[OK] $c $($BuildToolPins[$c].Version) entspricht den Pins" -ForegroundColor Green
        }
    }
    if ($failed.Count -gt 0) {
        Write-Host "tools/ ungeprueft/abweichend: $($failed -join ', ') - neu laden mit: pwsh scripts/fetch-build-tools.ps1 -Force -Components $($failed -join ',')" -ForegroundColor Red
        exit 1
    }
    exit 0
}

if (-not (Test-Path $ToolsDir)) { New-Item -ItemType Directory -Path $ToolsDir | Out-Null }
if (-not (Test-Path $TmpDir))   { New-Item -ItemType Directory -Path $TmpDir   | Out-Null }

Write-Host "==> Build-Tools nach $ToolsDir" -ForegroundColor Cyan
Write-Host ""

# ---------- poppler ----------
if ($Components -contains "poppler") {
    $pin = $BuildToolPins.poppler
    Write-Host "[1/3] poppler-windows $($pin.Version)" -ForegroundColor Green
    $popplerTarget = Join-Path $ToolsDir "poppler"
    $popplerLib    = Join-Path $popplerTarget "Library\bin"
    $popplerStamp  = Join-Path $popplerTarget ".version"

    if ((Test-Path $popplerLib) -and -not $Force) {
        Assert-InstalledPins 'poppler'
        Write-Host "  bereits installiert (SHA256 OK): $popplerLib" -ForegroundColor DarkGreen
    } else {
        $popplerZip = Join-Path $TmpDir $pin.File
        Get-PinnedFile -Pin $pin -OutFile $popplerZip
        Expand-Strict -ArchivePath $popplerZip -Destination $popplerTarget

        # Der ZIP enthaelt einen Wurzel-Ordner "poppler-XX.YY.Z/Library/bin/..."
        # Aufloesen, sodass tools/poppler/Library/bin direkt klappt.
        $inner = Get-ChildItem $popplerTarget -Directory | Select-Object -First 1
        if ($inner -and (Test-Path (Join-Path $inner.FullName "Library"))) {
            Get-ChildItem -Path $inner.FullName -Force | Move-Item -Destination $popplerTarget -Force
            Remove-Item -Recurse -Force $inner.FullName
        }

        if (-not (Test-Path $popplerLib)) {
            throw "poppler-Layout unerwartet: Library\bin fehlt unter $popplerTarget"
        }
        Assert-InstalledPins 'poppler'
        $pin.Version | Out-File $popplerStamp -Encoding ascii
        Write-Host "  installiert: $popplerLib ($($pin.Version))" -ForegroundColor DarkGreen
    }
    Write-Host ""
}

# ---------- NSSM ----------
if ($Components -contains "nssm") {
    $pin = $BuildToolPins.nssm
    Write-Host "[2/3] NSSM $($pin.Version)" -ForegroundColor Green
    $nssmTarget = Join-Path $ToolsDir "nssm\win64"
    $nssmExe    = Join-Path $nssmTarget "nssm.exe"

    if ((Test-Path $nssmExe) -and -not $Force) {
        Assert-InstalledPins 'nssm'
        Write-Host "  bereits installiert (SHA256 OK): $nssmExe" -ForegroundColor DarkGreen
    } else {
        $nssmZip    = Join-Path $TmpDir $pin.File
        $nssmRaw    = Join-Path $TmpDir "nssm-extract"

        Get-PinnedFile -Pin $pin -OutFile $nssmZip
        Expand-Strict -ArchivePath $nssmZip -Destination $nssmRaw

        $src = Join-Path $nssmRaw "nssm-$($pin.Version)\win64"
        if (-not (Test-Path $src)) {
            throw "NSSM-Layout unerwartet: $src fehlt"
        }
        if (-not (Test-Path $nssmTarget)) { New-Item -ItemType Directory -Path $nssmTarget -Force | Out-Null }
        Copy-Item -Path "$src\*" -Destination $nssmTarget -Force

        if (-not (Test-Path $nssmExe)) { throw "NSSM-Install fehlgeschlagen: $nssmExe nicht da" }
        Assert-InstalledPins 'nssm'
        Write-Host "  installiert: $nssmExe ($($pin.Version))" -ForegroundColor DarkGreen
    }
    Write-Host ""
}

# ---------- Tesseract ----------
if ($Components -contains "tesseract") {
    $pin = $BuildToolPins.tesseract
    Write-Host "[3/3] Tesseract OCR $($pin.Version) (deu+eng)" -ForegroundColor Green
    $tessTarget = Join-Path $ToolsDir "tesseract"
    $tessExe    = Join-Path $tessTarget "tesseract.exe"
    $tessData   = Join-Path $tessTarget "tessdata"

    if ((Test-Path $tessExe) -and (Test-Path (Join-Path $tessData "deu.traineddata")) -and -not $Force) {
        Assert-InstalledPins 'tesseract'
        Write-Host "  bereits installiert (SHA256 OK): $tessExe + deu.traineddata" -ForegroundColor DarkGreen
    } else {
        # Das UB-Mannheim-Setup ist ein NSIS-Installer. Wir laden die gepinnte
        # Version und ENTPACKEN sie mit 7-Zip (kein Admin noetig, kein Raten von
        # Silent-Flags) und ergaenzen die deutschen Sprachdaten.
        $tessSetup = Join-Path $TmpDir $pin.File
        Get-PinnedFile -Pin $pin -OutFile $tessSetup

        $sevenZip = @(
            (Get-Command 7z -ErrorAction SilentlyContinue).Source,
            "$env:ProgramFiles\7-Zip\7z.exe",
            "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $sevenZip) {
            throw "7-Zip nicht gefunden (zum Entpacken des Tesseract-Setups). 7-Zip installieren."
        }

        $tessExtract = Join-Path $TmpDir "tesseract-extract"
        if (Test-Path $tessExtract) { Remove-Item -Recurse -Force $tessExtract }
        Write-Host "  Entpacke mit 7-Zip ..." -ForegroundColor DarkGray
        & $sevenZip x $tessSetup "-o$tessExtract" -y *> $null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $tessExtract "tesseract.exe"))) {
            throw "Tesseract-Extraktion fehlgeschlagen (7z ExitCode $LASTEXITCODE)"
        }

        # tools/tesseract neu aufbauen: tesseract.exe + alle DLLs + tessdata.
        if (Test-Path $tessTarget) { Remove-Item -Recurse -Force $tessTarget }
        New-Item -ItemType Directory -Path $tessData -Force | Out-Null
        Copy-Item -Path (Join-Path $tessExtract "tesseract.exe") -Destination $tessTarget
        Get-ChildItem -Path $tessExtract -Filter "*.dll" | Copy-Item -Destination $tessTarget
        Copy-Item -Path (Join-Path $tessExtract "tessdata\*") -Destination $tessData -Recurse -Force

        # deu nachladen, falls das Setup es nicht mitbringt: UB-Mannheim bundlet nur
        # eng+osd, German ist ein optionaler On-Demand-Download. Ohne diesen Schritt
        # waere die OCR-Pipeline (deu+eng) unvollstaendig.
        $deuFile = Join-Path $tessData "deu.traineddata"
        if (-not (Test-Path $deuFile)) {
            Write-Host "  Lade deu.traineddata ($($BuildToolPins.tessdataDeu.Version)) ..." -ForegroundColor DarkGray
            Get-PinnedFile -Pin $BuildToolPins.tessdataDeu -OutFile $deuFile
        }
        Assert-InstalledPins 'tesseract'
        Write-Host "  installiert: $tessExe ($($pin.Version), deu+eng+osd)" -ForegroundColor DarkGreen
    }
    Write-Host ""
}

# ---------- Aufraeumen ----------
if (Test-Path $TmpDir) {
    # ZIPs/Setup-EXEs behalten fuers Caching bei Re-Runs - explizit -Force noetig
    if ($Force) { Remove-Item -Recurse -Force $TmpDir }
}

Write-Host "==> Fertig. tools/ ist fuer build-native.ps1 vorbereitet." -ForegroundColor Cyan
Get-ChildItem -Path $ToolsDir -Directory | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    Write-Host ("    {0,-12} {1,7:N1} MB" -f $_.Name, ($size / 1MB))
}
