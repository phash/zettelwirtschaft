# secure-install-dir.ps1 - Zugriffsrechte des Programmordners festlegen (INFRA-03).
#
# Problem: Der Dienst ZettelwirtschaftBackend laeuft als LocalSystem und startet
# <install>\backend\zettelwirtschaft-backend.exe, nssm.exe sowie die gebundelten
# Tesseract/poppler-Binaries (<install>\bin, vorne im PATH). Setup, Uninstaller
# und Update-Assistent fuehren die Exe zusaetzlich mit Administratorrechten aus
# (--backup). Unter "C:\Program Files" ist das sicher. Ein frei gewaehlter
# Programmordner (C:\Zettelwirtschaft, D:\Apps\...) erbt dagegen von der
# Laufwerkswurzel "Authentifizierte Benutzer: Aendern" - jedes lokale Konto
# koennte dann Exe/DLLs austauschen und Code als SYSTEM ausfuehren.
#
# Loesung (unabhaengig vom Ort, auch unter Program Files):
#   - Programmordner: Vererbung aus, nur SYSTEM + Administratoren Vollzugriff,
#     Benutzer Lesen/Ausfuehren. Atomar in EINEM Schreibvorgang (kein Fenster,
#     in dem die alten Rechte schon weg, die neuen aber noch nicht gesetzt sind).
#   - Alle Unterobjekte: explizite Eintraege entfernen (icacls /reset), damit sie
#     nur noch die engen Rechte erben.
#   - Als Administrator: Besitzer aller Objekte = Administratoren (ein Besitzer
#     kann die Rechte "seiner" Datei sonst jederzeit wieder oeffnen).
# Anders als secure-acl.ps1 (Datenordner) NICHT idempotent-zurueckhaltend: der
# Programmordner hat keine bewusst vom Benutzer gesetzten Rechte, er wird immer
# auf den Sollzustand gesetzt.
#
# Verwendung:
#   Direkt (NSIS):  powershell -File secure-install-dir.ps1 "<Programmordner>"
#                   Exit 0 = alles gesetzt, 2 = Ordner geschuetzt, aber einzelne
#                   Unterobjekte/Besitzer nicht (Warnung), 1 = Ordner NICHT geschuetzt.
#   Dot-Sourcing:   . (Join-Path $dir 'secure-install-dir.ps1'); Protect-ZwInstallDir -LiteralPath $p
#                   (liefert 0/1/2 wie oben). Der Parameter heisst bewusst nicht
#                   $InstallDir - beim Dot-Sourcen ueberschriebe er sonst die
#                   gleichnamige Variable des Aufrufers.
# Windows PowerShell 5.1 und PowerShell 7 kompatibel; reines ASCII.

param([Parameter(Position = 0)] [string]$ProtectInstallDir = '')

function Protect-ZwInstallDir {
    param([Parameter(Mandatory = $true)] [string]$LiteralPath)

    $full = [System.IO.Path]::GetFullPath($LiteralPath.Trim().Trim('"')).TrimEnd('\')
    if ($full.StartsWith('\\')) {
        Write-Warning "Programmordner $full ist ein Netzwerkpfad - nicht unterstuetzt."
        return 1
    }
    if (-not $full -or [System.IO.Path]::GetPathRoot($full + '\').TrimEnd('\') -eq $full) {
        Write-Warning "Programmordner $full ist eine Laufwerkswurzel - nicht unterstuetzt."
        return 1
    }
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    $principal = New-Object System.Security.Principal.WindowsPrincipal([System.Security.Principal.WindowsIdentity]::GetCurrent())
    $elevated = $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    $result = 0
    try {
        if (-not (Test-Path -LiteralPath $full)) { New-Item -ItemType Directory -Force -Path $full | Out-Null }
        $item = Get-Item -LiteralPath $full -Force
        if (-not $item.PSIsContainer) { Write-Warning "$full ist kein Ordner."; return 1 }

        if ($elevated) {
            # Besitz uebernehmen, bevor die DACL geschrieben wird: ein fremder
            # Besitzer (vorab angelegter Ordner) koennte sie sonst blockieren.
            $null = & (Join-Path $env:SystemRoot 'System32\takeown.exe') /F $full /A 2>&1
            if ($LASTEXITCODE -ne 0) { Write-Warning "takeown $full fehlgeschlagen (Exit $LASTEXITCODE)"; $result = 2 }
        }

        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        $none = [System.Security.AccessControl.PropagationFlags]::None
        foreach ($entry in @(
                @('S-1-5-18', 'FullControl'),        # SYSTEM (Dienst)
                @('S-1-5-32-544', 'FullControl'),    # Administratoren
                @('S-1-5-32-545', 'ReadAndExecute')  # Benutzer
            )) {
            $sid = New-Object System.Security.Principal.SecurityIdentifier($entry[0])
            $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                        $sid, [System.Security.AccessControl.FileSystemRights]$entry[1], $inherit, $none, 'Allow')))
        }
        # Nur die DACL schreiben (Besitzer unveraendert, kein SeRestorePrivilege noetig).
        if ($PSVersionTable.PSVersion.Major -ge 6) {
            [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.DirectoryInfo]$item, $acl)
        } else {
            $item.SetAccessControl($acl)
        }
    } catch {
        Write-Warning "Zugriffsrechte fuer $full nicht gesetzt: $($_.Exception.Message)"
        return 1
    }

    # Unterobjekte: explizite Rechte entfernen -> erben nur noch die engen Rechte.
    if (Get-ChildItem -LiteralPath $full -Force -ErrorAction SilentlyContinue | Select-Object -First 1) {
        $out = & $icacls (Join-Path $full '*') /reset /T /C /Q 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Warning "icacls /reset (Unterordner) Exit ${LASTEXITCODE}: $($out -join ' ')"; $result = 2 }
    }
    if ($elevated) {
        $out = & $icacls $full /setowner '*S-1-5-32-544' /T /C /Q 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Warning "icacls /setowner Exit ${LASTEXITCODE}: $($out -join ' ')"; $result = 2 }
    } else {
        Write-Warning 'Nicht als Administrator gestartet - Besitzer der Programmdateien unveraendert.'
    }
    return $result
}

if ($ProtectInstallDir) {
    $code = Protect-ZwInstallDir -LiteralPath $ProtectInstallDir
    if ($code -eq 0) { Write-Output "[OK] Programmordner geschuetzt (SYSTEM/Administratoren Vollzugriff, Benutzer Lesen): $ProtectInstallDir" }
    exit $code
}
