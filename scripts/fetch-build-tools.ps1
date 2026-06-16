# fetch-build-tools.ps1 - Laed alle Native-Build-Dependencies in tools/.
#
# Idempotent: vorhandene Tools werden uebersprungen.
# Aufruf:
#   pwsh scripts/fetch-build-tools.ps1
#   pwsh scripts/fetch-build-tools.ps1 -Force                 # alles neu laden
#   pwsh scripts/fetch-build-tools.ps1 -Components nssm,poppler  # selektiv
#
# Erfasste Komponenten:
#   - poppler-windows  (PDF-Konvertierung fuer pdf2image)
#   - NSSM             (Windows-Service-Wrapper)
#   - Tesseract OCR    (deu+eng Sprachpakete)
#
# Lizenz-Hinweise: die heruntergeladenen Tools haben jeweils eigene Lizenzen
# (Apache-2.0 fuer Tesseract, GPL-2.0 fuer poppler, Public Domain fuer NSSM).
# build-native.ps1 packt sie in das Setup.exe — Lizenztexte werden im
# Installer-Verzeichnis mit ausgeliefert.

[CmdletBinding()]
param(
    [switch]$Force,
    [string[]]$Components = @("poppler", "nssm", "tesseract")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest sonst extrem langsam

$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$ToolsDir = Join-Path $RepoRoot "tools"
$TmpDir = Join-Path $ToolsDir ".tmp"

if (-not (Test-Path $ToolsDir)) { New-Item -ItemType Directory -Path $ToolsDir | Out-Null }
if (-not (Test-Path $TmpDir))   { New-Item -ItemType Directory -Path $TmpDir   | Out-Null }

Write-Host "==> Build-Tools nach $ToolsDir" -ForegroundColor Cyan
Write-Host ""

# ---------- Helper ----------
function Get-FileIfMissing {
    param(
        [string]$Url,
        [string]$OutFile,
        [string]$ExpectedSha256 = ""
    )
    if ((Test-Path $OutFile) -and -not $Force) {
        Write-Host "  Cached: $(Split-Path -Leaf $OutFile)" -ForegroundColor DarkGray
        return
    }
    Write-Host "  Download: $Url" -ForegroundColor Yellow
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
    if ($ExpectedSha256) {
        $actual = (Get-FileHash $OutFile -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $ExpectedSha256.ToLower()) {
            throw "SHA256-Mismatch fuer $OutFile`n  expected: $ExpectedSha256`n  actual:   $actual"
        }
        Write-Host "  SHA256 OK" -ForegroundColor DarkGreen
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

# ---------- poppler ----------
if ($Components -contains "poppler") {
    Write-Host "[1/3] poppler-windows" -ForegroundColor Green
    $popplerTarget = Join-Path $ToolsDir "poppler"
    $popplerLib    = Join-Path $popplerTarget "Library\bin"
    $popplerStamp  = Join-Path $popplerTarget ".version"

    if ((Test-Path $popplerLib) -and -not $Force) {
        Write-Host "  bereits installiert: $popplerLib" -ForegroundColor DarkGreen
    } else {
        # Letzter stabiler Release per GitHub-API
        Write-Host "  pruefe GitHub-Release..." -ForegroundColor DarkGray
        $api = Invoke-RestMethod "https://api.github.com/repos/oschwartz10612/poppler-windows/releases/latest"
        $asset = $api.assets | Where-Object { $_.name -match "Release-[\d\.\-]+\.zip$" } | Select-Object -First 1
        if (-not $asset) { throw "Kein passender poppler-Release gefunden" }

        $popplerZip = Join-Path $TmpDir $asset.name
        Get-FileIfMissing -Url $asset.browser_download_url -OutFile $popplerZip
        Expand-Strict -ArchivePath $popplerZip -Destination $popplerTarget

        # Der ZIP enthaelt einen Wurzel-Ordner "poppler-XX.YY.Z/Library/bin/..."
        # Auflösen, sodass tools/poppler/Library/bin direkt klappt.
        $inner = Get-ChildItem $popplerTarget -Directory | Select-Object -First 1
        if ($inner -and (Test-Path (Join-Path $inner.FullName "Library"))) {
            Get-ChildItem -Path $inner.FullName -Force | Move-Item -Destination $popplerTarget -Force
            Remove-Item -Recurse -Force $inner.FullName
        }

        if (-not (Test-Path $popplerLib)) {
            throw "poppler-Layout unerwartet: Library\bin fehlt unter $popplerTarget"
        }
        $api.tag_name | Out-File $popplerStamp -Encoding ascii
        Write-Host "  installiert: $popplerLib ($($api.tag_name))" -ForegroundColor DarkGreen
    }
    Write-Host ""
}

# ---------- NSSM ----------
if ($Components -contains "nssm") {
    Write-Host "[2/3] NSSM" -ForegroundColor Green
    $nssmTarget = Join-Path $ToolsDir "nssm\win64"
    $nssmExe    = Join-Path $nssmTarget "nssm.exe"

    if ((Test-Path $nssmExe) -and -not $Force) {
        Write-Host "  bereits installiert: $nssmExe" -ForegroundColor DarkGreen
    } else {
        # nssm.cc liefert ein klar versioniertes ZIP mit win32/win64-Unterordnern
        $nssmZipUrl = "https://nssm.cc/release/nssm-2.24.zip"
        $nssmZip    = Join-Path $TmpDir "nssm-2.24.zip"
        $nssmRaw    = Join-Path $TmpDir "nssm-extract"

        Get-FileIfMissing -Url $nssmZipUrl -OutFile $nssmZip
        Expand-Strict -ArchivePath $nssmZip -Destination $nssmRaw

        $src = Join-Path $nssmRaw "nssm-2.24\win64"
        if (-not (Test-Path $src)) {
            throw "NSSM-Layout unerwartet: $src fehlt"
        }
        if (-not (Test-Path $nssmTarget)) { New-Item -ItemType Directory -Path $nssmTarget -Force | Out-Null }
        Copy-Item -Path "$src\*" -Destination $nssmTarget -Force

        if (-not (Test-Path $nssmExe)) { throw "NSSM-Install fehlgeschlagen: $nssmExe nicht da" }
        Write-Host "  installiert: $nssmExe (2.24)" -ForegroundColor DarkGreen
    }
    Write-Host ""
}

# ---------- Tesseract ----------
if ($Components -contains "tesseract") {
    Write-Host "[3/3] Tesseract OCR (deu+eng)" -ForegroundColor Green
    $tessTarget = Join-Path $ToolsDir "tesseract"
    $tessExe    = Join-Path $tessTarget "tesseract.exe"
    $tessData   = Join-Path $tessTarget "tessdata"

    if ((Test-Path $tessExe) -and (Test-Path (Join-Path $tessData "deu.traineddata")) -and -not $Force) {
        Write-Host "  bereits installiert: $tessExe + deu.traineddata" -ForegroundColor DarkGreen
    } else {
        # Das UB-Mannheim-Setup ist ein NSIS-Installer. Wir laden es vom Mirror und
        # ENTPACKEN es mit 7-Zip (kein Admin noetig, kein Raten von Silent-Flags) und
        # ergaenzen die deutschen Sprachdaten. Wer schon eine Tesseract-Installation
        # hat, kann das umgehen indem er `tools/tesseract/` einfach manuell ausfuellt.

        Write-Host "  Quelle: https://github.com/UB-Mannheim/tesseract/wiki (Mirror digi.bib.uni-mannheim.de)" -ForegroundColor DarkGray

        # Version dynamisch aus dem Mirror-Listing aufloesen statt hart zu pinnen:
        # eine feste Pin rottet, sobald der Mirror sie entfernt (digi.bib haelt nur
        # neuere Builds vor; die alte Pin 5.5.0.20241111 liefert mittlerweile 404).
        # Fallback auf eine bekannte gute Version, wenn das Listing nicht erreichbar ist.
        $tessMirror   = "https://digi.bib.uni-mannheim.de/tesseract/"
        $tessFallback = "5.4.0.20240606"
        $tessVersion  = $tessFallback
        try {
            $listing = Invoke-WebRequest -Uri $tessMirror -UseBasicParsing -TimeoutSec 30
            # Nur saubere X.Y.Z.YYYYMMDD-Builds (keine alpha/-gHASH-Snapshots).
            $vers = [regex]::Matches($listing.Content, 'tesseract-ocr-w64-setup-(\d+\.\d+\.\d+\.\d{8})\.exe') |
                ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
            if ($vers) {
                # Neueste = hoechste [version] (Datums-Suffix als Tiebreaker).
                $tessVersion = ($vers | Sort-Object { [version]($_ -replace '\.\d{8}$', '') }, { $_ })[-1]
            }
        } catch {
            Write-Warning "  Mirror-Listing nicht erreichbar - nutze Fallback $tessFallback"
        }
        Write-Host "  Version: $tessVersion" -ForegroundColor DarkGray

        $tessSetupName = "tesseract-ocr-w64-setup-$tessVersion.exe"
        $tessSetup     = Join-Path $TmpDir $tessSetupName
        try {
            Get-FileIfMissing -Url "$tessMirror$tessSetupName" -OutFile $tessSetup
        } catch {
            if ($tessVersion -ne $tessFallback) {
                Write-Warning "  $tessVersion nicht ladbar - versuche Fallback $tessFallback"
                $tessVersion   = $tessFallback
                $tessSetupName = "tesseract-ocr-w64-setup-$tessVersion.exe"
                $tessSetup     = Join-Path $TmpDir $tessSetupName
                Get-FileIfMissing -Url "$tessMirror$tessSetupName" -OutFile $tessSetup
            } else {
                throw "Tesseract-Download fehlgeschlagen. Manuell: https://github.com/UB-Mannheim/tesseract/wiki -> tools/tesseract fuellen."
            }
        }

        # Das UB-Mannheim-Setup ist ein NSIS-Installer und braucht zum Ausfuehren
        # Admin. Wir EXTRAHIEREN es stattdessen mit 7-Zip (kein Admin, und robuster
        # als Silent-Install-Flags zu raten). 7-Zip ist auf Build-Hosts ueblich.
        $sevenZip = @(
            (Get-Command 7z -ErrorAction SilentlyContinue).Source,
            "$env:ProgramFiles\7-Zip\7z.exe",
            "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $sevenZip) {
            throw "7-Zip nicht gefunden (zum Entpacken des Tesseract-Setups). 7-Zip installieren oder tools/tesseract manuell fuellen."
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
            Write-Host "  Lade deu.traineddata (nicht im Setup gebundelt) ..." -ForegroundColor DarkGray
            Get-FileIfMissing -Url "https://github.com/tesseract-ocr/tessdata/raw/main/deu.traineddata" -OutFile $deuFile
        }

        Write-Host "  installiert: $tessExe ($tessVersion, deu+eng+osd)" -ForegroundColor DarkGreen
    }
    Write-Host ""
}

# ---------- Aufraeumen ----------
if (Test-Path $TmpDir) {
    # ZIPs/Setup-EXEs behalten fuers Caching bei Re-Runs — explizit -Force noetig
    if ($Force) { Remove-Item -Recurse -Force $TmpDir }
}

Write-Host "==> Fertig. tools/ ist fuer build-native.ps1 vorbereitet." -ForegroundColor Cyan
Get-ChildItem -Path $ToolsDir -Directory | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    Write-Host ("    {0,-12} {1,7:N1} MB" -f $_.Name, ($size / 1MB))
}
