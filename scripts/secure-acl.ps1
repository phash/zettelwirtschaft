# secure-acl.ps1 - Zugriffsrechte fuer Geheimnisse und Daten einschraenken (SEC-DATA-03).
#
# Problem: config.toml/.env (PIN_CODE, EMAIL_ENCRYPTION_KEY), Datenbank (Fernet-
# verschluesselte IMAP-Passwoerter), Backups, Logs und das Archiv erben die
# Rechte des Elternordners. Liegt der Datenordner ausserhalb des Profils
# (C:\Zettelwirtschaft, D:\...), erbt er von der Laufwerkswurzel
# "Authentifizierte Benutzer: Aendern" - jedes andere lokale Konto koennte die
# Geheimnisse lesen und die config.toml des (als LocalSystem laufenden) Dienstes
# aendern (PIN aus, OLLAMA_BASE_URL auf einen eigenen Listener).
#
# Loesung: Vererbung abschalten, nur SYSTEM (Dienst), Administratoren und das
# installierende Konto erhalten Vollzugriff. Bei Ordnern erben alle Kinder diese
# Rechte (Windows propagiert beim Setzen). Fuer den Standard-Datenordner im
# Profil aendert sich praktisch nichts (dort gelten schon nur diese drei).
#
# Idempotent: ist die Vererbung schon abgeschaltet (frueherer Lauf oder vom
# Benutzer bewusst angepasst, z.B. ein Scanner-Konto fuer den watch-Ordner),
# bleibt die ACL unveraendert. Neben dem ausfuehrenden Konto behaelt auch der
# Besitzer des Pfads Zugriff (Setup-Upgrade/Update-Wizard unter einem anderen
# Admin-Konto sperren das urspruenglich installierende Konto nicht aus).
#
# Nicht angefasst: UNC-Pfade (\\NAS\...) - dort entscheidet der Server - und
# Laufwerkswurzeln.
#
# Verwendung:
#   Dot-Sourcing:  . (Join-Path $PSScriptRoot 'secure-acl.ps1'); Protect-ZwPath -LiteralPath $p
#   Direkt (NSIS): powershell -File secure-acl.ps1 "<Ordner>" ["<Datei>" ...]
#                  Exit 0 = alles gesetzt, 2 = mind. ein Pfad uebersprungen
#                  (Warnung, die Installation laeuft weiter).
# Windows PowerShell 5.1 und PowerShell 7 kompatibel; reines ASCII (keine BOM noetig).

param([Parameter(ValueFromRemainingArguments = $true)] [string[]]$ProtectPath = @())

$ZwSidSystem = 'S-1-5-18'
$ZwSidAdministrators = 'S-1-5-32-544'

function Protect-ZwPath {
    param(
        [Parameter(Mandatory = $true)] [string]$LiteralPath,
        # Weitere Konten (z.B. ein spaeteres Dienstkonto NT SERVICE\ZettelwirtschaftBackend).
        [string[]]$ExtraSid = @()
    )
    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        Write-Warning "Zugriffsrechte: $LiteralPath existiert nicht - uebersprungen."
        return $false
    }
    $full = [System.IO.Path]::GetFullPath($LiteralPath)
    if ($full.StartsWith('\\')) {
        Write-Warning "Zugriffsrechte: $full ist ein Netzwerkpfad - Rechte bitte auf dem NAS/Server einschraenken."
        return $false
    }
    try {
        $item = Get-Item -LiteralPath $full -Force
        $isDir = $item.PSIsContainer
        if ($isDir -and [System.IO.Path]::GetPathRoot($full).TrimEnd('\') -eq $full.TrimEnd('\')) {
            Write-Warning "Zugriffsrechte: $full ist eine Laufwerkswurzel - uebersprungen."
            return $false
        }
        $acl = Get-Acl -LiteralPath $full
        if ($acl.AreAccessRulesProtected) {
            return $true   # schon eingeschraenkt bzw. bewusst angepasst
        }
        $owner = $null
        try { $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value } catch { $owner = $null }
        # Explizite Eintraege entfernen, Vererbung aus (ohne die geerbten zu kopieren).
        foreach ($rule in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
            [void]$acl.RemoveAccessRuleSpecific($rule)
        }
        $acl.SetAccessRuleProtection($true, $false)

        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $sids = @($ZwSidSystem, $ZwSidAdministrators, $me, $owner) +@($ExtraSid) | Where-Object { $_ } | Select-Object -Unique
        foreach ($sid in $sids) {
            $id = New-Object System.Security.Principal.SecurityIdentifier($sid)
            if ($isDir) {
                $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                    $id, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
            } else {
                $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($id, 'FullControl', 'Allow')
            }
            $acl.AddAccessRule($rule)
        }
        # Nur den DACL-Abschnitt schreiben (Besitzer bleibt) - Set-Acl unter 5.1
        # versucht sonst den Besitzer mitzusetzen und scheitert ohne SeRestorePrivilege.
        if ($PSVersionTable.PSVersion.Major -ge 6) {
            if ($isDir) {
                [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.DirectoryInfo]$item, [System.Security.AccessControl.DirectorySecurity]$acl)
            } else {
                [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.FileInfo]$item, [System.Security.AccessControl.FileSecurity]$acl)
            }
        } else {
            $item.SetAccessControl($acl)
        }
        return $true
    } catch {
        Write-Warning "Zugriffsrechte fuer $full nicht gesetzt: $($_.Exception.Message)"
        return $false
    }
}

if ($ProtectPath.Count -gt 0) {
    $allOk = $true
    foreach ($p in $ProtectPath) {
        if (-not $p) { continue }
        $p = $p.Trim().Trim('"')
        if (Protect-ZwPath -LiteralPath $p) {
            Write-Output "[OK] Zugriff eingeschraenkt (SYSTEM, Administratoren, $env:USERNAME): $p"
        } else {
            $allOk = $false
        }
    }
    exit $(if ($allOk) { 0 } else { 2 })
}
