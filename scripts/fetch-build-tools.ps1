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
        # Tesseract wird als InnoSetup-Installer ausgeliefert. Drei Optionen:
        # 1. winget                — schnell + offiziell, aber Endkunden-Verzeichnis
        # 2. innounp.exe            — extrahiert ohne Installation (~ 1 MB Tool)
        # 3. Silent install nach %TEMP%, kopieren, deinstallieren
        #
        # Wir nehmen Option 3: das ist robust und braucht nichts Externes.
        # Wer schon eine Tesseract-Installation hat, kann das umgehen indem er
        # `tools/tesseract/` einfach manuell ausfuellt.

        $tessSetupUrl = "https://github.com/UB-Mannheim/tesseract/wiki"
        Write-Host "  Quelle: $tessSetupUrl" -ForegroundColor DarkGray
        Write-Host "  Pruefe Setup-EXE (Pinned-Version)..." -ForegroundColor DarkGray

        # Pinned auf eine bekannte gute Version. Bei Bedarf updaten.
        # UB-Mannheim haengt das Setup-EXE als GitHub-Release-Asset an.
        $tessVersion = "5.5.0.20241111"
        $tessSetupName = "tesseract-ocr-w64-setup-$tessVersion.exe"
        $tessSetupCandidates = @(
            "https://digi.bib.uni-mannheim.de/tesseract/$tessSetupName"
        )

        $tessSetup = Join-Path $TmpDir $tessSetupName
        $downloaded = $false
        foreach ($u in $tessSetupCandidates) {
            try {
                Get-FileIfMissing -Url $u -OutFile $tessSetup
                $downloaded = $true
                break
            } catch {
                Write-Warning "  Download fehlgeschlagen: $u`n  $($_.Exception.Message)"
            }
        }
        if (-not $downloaded) {
            Write-Host ""
            Write-Host "  [MANUELL] Tesseract-Download fehlgeschlagen." -ForegroundColor Red
            Write-Host "  Bitte runterladen: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Red
            Write-Host "  Setup-EXE installieren mit 'Additional language data (download)':" -ForegroundColor Red
            Write-Host "    - German" -ForegroundColor Red
            Write-Host "    - English (Standard)" -ForegroundColor Red
            Write-Host "  Danach kopieren:" -ForegroundColor Red
            Write-Host "    Copy-Item -Recurse 'C:\Program Files\Tesseract-OCR\*' '$tessTarget'" -ForegroundColor Red
            throw "Tesseract-Download benoetigt manuelle Aktion"
        }

        # Silent-Install nach Temp-Verzeichnis
        $tessInstall = Join-Path $TmpDir "tesseract-install"
        if (Test-Path $tessInstall) { Remove-Item -Recurse -Force $tessInstall }
        New-Item -ItemType Directory -Path $tessInstall | Out-Null

        Write-Host "  Silent-Install nach $tessInstall ..." -ForegroundColor DarkGray
        # /TASKS="!japanese" als Beispiel — wir wollen aber Sprachpakete deu+eng.
        # /COMPONENTS verlangt eine genaue Liste — wir nehmen Default + spr_deu.
        # Tesseract-InnoSetup unterstuetzt:
        #   /COMPONENTS="main,langdata/deu,langdata/eng"
        # /VERYSILENT laesst keinen Fortschritt sehen, /SUPPRESSMSGBOXES blockiert nichts
        $args = @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/DIR=`"$tessInstall`""
        )
        $p = Start-Process -FilePath $tessSetup -ArgumentList $args -Wait -PassThru -NoNewWindow
        if ($p.ExitCode -ne 0) {
            throw "Tesseract-Installer schlug fehl (ExitCode $($p.ExitCode))"
        }

        # Kopieren in unser tools/ — nur das Noetigste, deu+eng+osd.
        if (Test-Path $tessTarget) { Remove-Item -Recurse -Force $tessTarget }
        New-Item -ItemType Directory -Path $tessTarget | Out-Null

        Copy-Item -Path (Join-Path $tessInstall "tesseract.exe") -Destination $tessTarget
        # Alle .dll
        Get-ChildItem -Path $tessInstall -Filter "*.dll" | Copy-Item -Destination $tessTarget

        # tessdata: deu + eng + osd
        $srcData = Join-Path $tessInstall "tessdata"
        if (-not (Test-Path $srcData)) {
            throw "tessdata-Verzeichnis nicht gefunden: $srcData"
        }
        New-Item -ItemType Directory -Path $tessData -Force | Out-Null
        $needed = @("deu.traineddata", "eng.traineddata", "osd.traineddata")
        foreach ($n in $needed) {
            $src = Join-Path $srcData $n
            if (Test-Path $src) {
                Copy-Item -Path $src -Destination $tessData
            } else {
                Write-Warning "  Sprachpaket fehlt: $n (im Tesseract-Installer nicht ausgewaehlt?)"
            }
        }
        # Configs/tessconfig-Dateien werden vom OCR-Engine benoetigt
        $srcConfigs = Join-Path $srcData "configs"
        if (Test-Path $srcConfigs) {
            Copy-Item -Recurse -Path $srcConfigs -Destination $tessData
        }

        # Tesseract-Installer raeumt nicht automatisch auf — die Test-Install-Dir
        # ist halt unter %TEMP%, wird vom OS irgendwann geloescht.
        Write-Host "  installiert: $tessExe ($tessVersion)" -ForegroundColor DarkGreen
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
