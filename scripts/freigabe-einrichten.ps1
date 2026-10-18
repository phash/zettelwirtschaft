# freigabe-einrichten.ps1 - Eingangsordner fuer Netzwerk-Scanner freigeben.
#
# Weg (B) aus planung/13-netzwerk-ordner.md: Der LOKALE Watch-Ordner wird als
# SMB-Freigabe angeboten, damit ein Netzwerk-Scanner (z.B. "Scan to Network
# Folder") direkt hineinschreibt. Der Ordner bleibt lokal: der Dienst braucht
# keine Netzwerkanmeldung, lokale Aenderungs-Ereignisse sind zuverlaessig.
#
# Was eingerichtet wird (idempotent, jeder Schritt meldet sich):
#   1. lokales Konto (Standard "zw-scanner") mit erzeugtem Passwort, in keiner
#      lokalen Gruppe, ohne interaktive/Remote-/Batch-/Dienst-Anmeldung, im
#      Anmeldebildschirm versteckt. ACHTUNG (F4): Bei der Netzwerkanmeldung
#      gehoert es trotzdem implizit zu "Jeder", "Authentifizierte Benutzer" und
#      damit auch zu "Benutzer" - andere Freigaben dieses PCs mit solchen Rechten
#      sind mit den Scanner-Zugangsdaten erreichbar und werden deshalb gemeldet.
#   2. NTFS: auf dem Watch-Ordner selbst nur "Dateien erstellen" + Ordner lesen
#      (keine Unterordner, kein Loeschen/Lesen von Dateien ANDERER Konten);
#      eigene Dateien darf das Konto bearbeiten/umbenennen (ERSTELLER-BESITZER).
#      verarbeitet\ und rejected\ sind fuer das Konto gesperrt. Breite Rechte
#      auf dem Ordner (Jeder/Benutzer/Authentifizierte Benutzer/Interaktiv/
#      Netzwerk, z.B. von C:\ geerbt) werden entfernt - sie gaelten ueber die
#      Freigabe sonst auch fuer das Scanner-Konto (F1).
#      Hinweis (F10): Alle Geraete teilen sich das EINE Konto. Per Netzwerk
#      geschriebene Scans gehoeren ihm - wer die Scanner-Zugangsdaten kennt, kann
#      noch nicht verarbeitete Scans lesen, ersetzen oder loeschen (bis sie nach
#      verarbeitet\ verschoben sind). Die Zugangsdaten deshalb nur im Scanner
#      hinterlegen; mehrere Konten fuer eine Freigabe richtet das Skript nicht ein.
#   3. SMB-Freigabe (Standard "Zettelwirtschaft-Eingang") mit genau EINEM
#      Eintrag: "Aendern" fuer dieses Konto (kein "Jeder"), kein SMB1
#   4. Firewall-Regel nur TCP 445, nur lokales Subnetz (bzw. nur die Adresse
#      -ScannerAddress), nur Profil "Privat". ACHTUNG (F3): Eine Port-Regel laesst
#      sich nicht auf eine Freigabe beschraenken - sie oeffnet den SMB-Dienst des
#      ganzen PCs (alle Freigaben) fuer das lokale Netz.
#   5. HKLM\SOFTWARE\Zettelwirtschaft: ScannerShareName/User/Path (KEIN Passwort)
# Ausgabe: UNC-Pfad, Benutzer und Passwort genau einmal (Konsole, bzw. nur in
# der -ResultFile-INI fuer das Setup). Das Passwort wird nirgends gespeichert.
#
# Aufruf (Administrator; mit -Elevate fordert das Skript die Rechte selbst an,
# so nutzt es freigabe-einrichten.bat):
#   freigabe-einrichten.ps1 [-WatchDir <Ordner>] [-NewPassword] [-Yes] [-ScannerAddress <IPv4>]
#   freigabe-einrichten.ps1 -Remove        (Namen aus der Registry, falls nicht angegeben)
#   freigabe-einrichten.ps1 -WatchDir <Ordner> -DataDir <Daten> -ResultFile <ini>   (Setup)
# Setup-Modus (-ResultFile): bei einem vorhandenen Konto wird immer ein neues
# Passwort gesetzt - die Abschlussseite ist die einzige Stelle, an der es steht.
# Ohne -WatchDir: zettelwirtschaft-backend.exe --print-watch-dir (Einstellung der
# Weboberflaeche, Fallback WATCH_DIR). Ordner ausserhalb des Datenordners nur
# nach Rueckfrage bzw. mit -Yes, nie im Setup-Modus (-ResultFile).
#
# Tests (scripts/test-scripts.ps1): -DryRun protokolliert jeden Systemeingriff
# als JSON-Zeile in -DryRunLog statt ihn auszufuehren; -DryRunState (JSON)
# simuliert den Systemzustand. Es wird dann nichts angelegt.
#
# Exit 0 = ok, 1 = Fehler (Teilschritte werden zurueckgenommen), 3 = keine
# Administratorrechte, 4 = Ordner nicht erlaubt.
# Windows PowerShell 5.1 kompatibel (startet sich bei Bedarf als 64-Bit-Prozess
# neu: LocalAccounts gibt es in 32-Bit-PowerShell nicht). UTF-8 mit BOM.

# PositionalBinding=$false (F8): ein ueberzaehliges Wort (z.B. aus einem Pfad mit
# Leerzeichen ohne Anfuehrungszeichen) bindet sich nicht still an -DataDir.
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$WatchDir = '',
    # Datenordner der Installation (Setup: Registry ist noch nicht geschrieben);
    # leer = HKLM\SOFTWARE\Zettelwirtschaft\DataDir.
    [string]$DataDir = '',
    [switch]$Remove,
    [switch]$NewPassword,
    [string]$ResultFile = '',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,39}$')]
    [string]$ShareName = 'Zettelwirtschaft-Eingang',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,19}$')]
    [string]$UserName = 'zw-scanner',
    [switch]$Yes,
    # F3: Firewall-Regel nur fuer diese Adresse (bzw. dieses Netz, CIDR) statt
    # fuer das ganze lokale Subnetz.
    [ValidatePattern('^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)(/(3[0-2]|[12]?\d))?$')]
    [string]$ScannerAddress = '',
    # Ohne Administratorrechte: mit UAC-Abfrage neu starten (freigabe-einrichten.bat).
    [switch]$Elevate,
    [switch]$PauseOnExit,
    [string]$RegistryKey = 'HKLM:\SOFTWARE\Zettelwirtschaft',
    [switch]$DryRun,
    [string]$DryRunLog = '',
    [string]$DryRunState = ''
)

$ErrorActionPreference = 'Stop'

# -Remove ohne -ShareName/-UserName: die eingerichteten Namen stehen in der Registry (F6).
$ZwExplicitShareName = $PSBoundParameters.ContainsKey('ShareName')
$ZwExplicitUserName = $PSBoundParameters.ContainsKey('UserName')
$ZwBoundParameters = @{}
foreach ($zwKey in $PSBoundParameters.Keys) { $ZwBoundParameters[$zwKey] = $PSBoundParameters[$zwKey] }

$ZwFirewallRule = 'Zettelwirtschaft Scanner-Freigabe (SMB)'
# Jeder, Authentifizierte Benutzer, Benutzer, Interaktiv, Netzwerk: zu diesen
# gehoert das Scanner-Konto bei einer Netzwerkanmeldung implizit (F1/F4).
$ZwBroadSids = @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545', 'S-1-5-4', 'S-1-5-2')
$ZwShareNamePattern = '^[A-Za-z0-9][A-Za-z0-9-]{0,39}$'
$ZwUserNamePattern = '^[A-Za-z0-9][A-Za-z0-9-]{0,19}$'
$ZwSidCreatorOwner = 'S-1-3-0'
$ZwSubDirs = @('verarbeitet', 'rejected')
$ZwSpecialAccounts = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'

# --------------------------------------------------------------- Systemeingriffe
# Alle Aenderungen laufen ueber Invoke-ZwStep: im -DryRun nur protokolliert
# (ohne Passwoerter), sonst ausgefuehrt.
$script:DryRunStateData = @{}

function Write-ZwStepLog([string]$step, [hashtable]$params) {
    if (-not $DryRunLog) { return }
    $entry = @{ step = $step; params = $params } | ConvertTo-Json -Compress -Depth 4
    [System.IO.File]::AppendAllText($DryRunLog, $entry + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-ZwStep([string]$step, [hashtable]$params, [scriptblock]$action) {
    if ($DryRun) {
        Write-ZwStepLog $step $params
        # Tests: einen Fehlschlag in genau diesem Schritt simulieren (Rollback).
        if ($script:DryRunStateData.ContainsKey('FailStep') -and $script:DryRunStateData['FailStep'] -eq $step) {
            throw "Simulierter Fehler in $step"
        }
        return
    }
    & $action
}

function Get-ZwState([string]$name, $default) {
    if ($script:DryRunStateData.ContainsKey($name)) { return $script:DryRunStateData[$name] }
    return $default
}

function Test-ZwUserExists {
    if ($DryRun) { return [bool](Get-ZwState 'UserExists' $false) }
    return [bool](Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue)
}

function Get-ZwSharePath {
    if ($DryRun) { return [string](Get-ZwState 'SharePath' '') }
    $share = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
    if ($share) { return [string]$share.Path }
    return ''
}

function Test-ZwFirewallRule {
    if ($DryRun) { return [bool](Get-ZwState 'FirewallRule' $false) }
    return [bool](Get-NetFirewallRule -DisplayName $ZwFirewallRule -ErrorAction SilentlyContinue)
}

function Get-ZwUserGroups {
    # Lokale Gruppen des vorhandenen Kontos (F7). ADSI statt Get-LocalGroupMember
    # (das bricht bei verwaisten SIDs in einer Gruppe ab).
    if ($DryRun) { return @(Get-ZwState 'UserGroups' @()) }
    try {
        $user = [ADSI]"WinNT://$env:COMPUTERNAME/$UserName,user"
        return @($user.Groups() | ForEach-Object { $_.GetType().InvokeMember('Name', 'GetProperty', $null, $_, $null) })
    } catch {
        return @()
    }
}

function Test-ZwUserEnabled {
    if ($DryRun) { return [bool](Get-ZwState 'UserEnabled' $true) }
    return [bool](Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue).Enabled
}

# Andere (nicht administrative) Freigaben, die Jeder/Benutzer/Authentifizierte
# Benutzer zulassen - mit den Scanner-Zugangsdaten erreichbar (F4).
function Get-ZwOpenShares {
    if ($DryRun) { return @(Get-ZwState 'OpenShares' @()) }
    $open = @()
    foreach ($share in @(Get-SmbShare -Special $false -ErrorAction SilentlyContinue)) {
        if ($share.Name -eq $ShareName) { continue }
        foreach ($access in @(Get-SmbShareAccess -Name $share.Name -ErrorAction SilentlyContinue)) {
            if ($access.AccessControlType -ne 'Allow') { continue }
            try {
                $sid = (New-Object System.Security.Principal.NTAccount($access.AccountName)).Translate([System.Security.Principal.SecurityIdentifier]).Value
            } catch { continue }
            if ($ZwBroadSids -contains $sid) { $open += $share.Name; break }
        }
    }
    return $open
}

function Get-ZwUserSid {
    if ($DryRun) { return 'S-1-5-21-0-0-0-1099' }
    $account = New-Object System.Security.Principal.NTAccount("$env:COMPUTERNAME\$UserName")
    return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
}

function Get-ZwRegistryValue([string]$name) {
    if ($DryRun) { return [string](Get-ZwState "Reg_$name" '') }
    $item = Get-ItemProperty -LiteralPath $RegistryKey -Name $name -ErrorAction SilentlyContinue
    if ($item) { return [string]$item.$name }
    return ''
}

# --------------------------------------------------------------- LSA-Rechte
$ZwLsaSource = @'
using System;
using System.Runtime.InteropServices;
using System.Security.Principal;

public static class ZwLsa {
    [StructLayout(LayoutKind.Sequential)]
    struct LSA_UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }

    [StructLayout(LayoutKind.Sequential)]
    struct LSA_OBJECT_ATTRIBUTES {
        public int Length; public IntPtr RootDirectory; public IntPtr ObjectName;
        public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService;
    }

    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr systemName, ref LSA_OBJECT_ATTRIBUTES attributes, uint access, out IntPtr handle);
    [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr handle, byte[] sid, LSA_UNICODE_STRING[] rights, uint count);
    [DllImport("advapi32.dll")] static extern uint LsaRemoveAccountRights(IntPtr handle, byte[] sid, bool allRights, LSA_UNICODE_STRING[] rights, uint count);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint status);

    const uint POLICY_CREATE_ACCOUNT = 0x10;
    const uint POLICY_LOOKUP_NAMES = 0x800;
    const uint STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034;

    static IntPtr Open() {
        var attributes = new LSA_OBJECT_ATTRIBUTES();
        attributes.Length = Marshal.SizeOf(typeof(LSA_OBJECT_ATTRIBUTES));
        IntPtr handle;
        uint status = LsaOpenPolicy(IntPtr.Zero, ref attributes, POLICY_CREATE_ACCOUNT | POLICY_LOOKUP_NAMES, out handle);
        if (status != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(status));
        return handle;
    }

    static byte[] SidBytes(string sid) {
        var identifier = new SecurityIdentifier(sid);
        var bytes = new byte[identifier.BinaryLength];
        identifier.GetBinaryForm(bytes, 0);
        return bytes;
    }

    public static void AddRight(string sid, string right) {
        IntPtr handle = Open();
        var value = new LSA_UNICODE_STRING();
        value.Buffer = Marshal.StringToHGlobalUni(right);
        value.Length = (ushort)(right.Length * 2);
        value.MaximumLength = (ushort)(value.Length + 2);
        try {
            uint status = LsaAddAccountRights(handle, SidBytes(sid), new[] { value }, 1);
            if (status != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(status));
        } finally {
            Marshal.FreeHGlobal(value.Buffer);
            LsaClose(handle);
        }
    }

    public static void RemoveAllRights(string sid) {
        IntPtr handle = Open();
        try {
            uint status = LsaRemoveAccountRights(handle, SidBytes(sid), true, null, 0);
            if (status != 0 && status != STATUS_OBJECT_NAME_NOT_FOUND)
                throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(status));
        } finally {
            LsaClose(handle);
        }
    }
}
'@

function Initialize-ZwLsa {
    if (-not ('ZwLsa' -as [type])) { Add-Type -TypeDefinition $ZwLsaSource -Language CSharp }
}

# --------------------------------------------------------------- Passwort
function New-ZwScannerPassword {
    # Eigene Datei (auch vom Setup genutzt): 16 Zeichen, nur A-Z a-z 2-9 ohne
    # verwechselbare Zeichen - viele Scanner erlauben keine Sonderzeichen.
    # new-secret.ps1 garantiert Gross-, Kleinbuchstaben und Ziffern (Kennwort-
    # Komplexitaetsrichtlinie, F9).
    $value = & (Join-Path $PSScriptRoot 'new-secret.ps1') -Kind password
    $value = ([string]$value).Trim()
    if ($value -cnotmatch '^[A-Za-z2-9]{16}$' -or $value -cnotmatch '[A-Z]' -or $value -cnotmatch '[a-z]' -or $value -notmatch '[2-9]') {
        throw 'Passwort konnte nicht erzeugt werden.'
    }
    return $value
}

# --------------------------------------------------------------- Ordnerpruefung
function Test-ZwUnder([string]$path, [string]$root) {
    if (-not $root) { return $false }
    $p = $path.TrimEnd('\') + '\'
    $r = $root.TrimEnd('\') + '\'
    return $p.StartsWith($r, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-ZwSamePath([string]$a, [string]$b) {
    if (-not $a -or -not $b) { return $false }
    return $a.TrimEnd('\') -ieq $b.TrimEnd('\')
}

# F2: Junction/Symlink IRGENDWO im Pfad (nicht nur am Ende). GetFullPath loest
# keine Verknuepfungen auf - alle weiteren Pruefungen waeren sonst rein textuell
# und liessen sich mit einer Junction im Datenordner umgehen.
function Get-ZwLinkInPath([string]$full) {
    $root = [System.IO.Path]::GetPathRoot($full)
    $current = $root.TrimEnd('\')
    foreach ($part in ($full.Substring($root.Length).Split('\') | Where-Object { $_ })) {
        $current = "$current\$part"
        if (-not (Test-Path -LiteralPath $current)) { break }
        $item = Get-Item -LiteralPath $current -Force
        if ($item.LinkType -in @('Junction', 'SymbolicLink')) { return $current }
    }
    return ''
}

# Liefert "" wenn der Ordner freigegeben werden darf, sonst den Grund.
function Get-ZwShareBlocker([string]$path, [string]$dataRoot, [string]$installDir) {
    if (-not $path) { return 'Kein Eingangsordner angegeben.' }
    if ($path.StartsWith('\\') -or $path.StartsWith('//')) {
        return 'Der Eingangsordner ist ein Netzwerkpfad. Freigeben lässt sich nur ein lokaler Ordner – für eine NAS bitte in der Weboberfläche den Netzwerkpfad mit Benutzer und Passwort eintragen.'
    }
    if ($path -notmatch '^[A-Za-z]:\\') { return "Kein absoluter lokaler Pfad: $path" }
    $full = [System.IO.Path]::GetFullPath($path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) { return "Eine Laufwerkswurzel wird nicht freigegeben: $full" }
    try { $drive = New-Object System.IO.DriveInfo($root) } catch { return "Laufwerk unbekannt: $root" }
    if ($drive.DriveType -ne [System.IO.DriveType]::Fixed) {
        return "Der Ordner liegt nicht auf einer lokalen Festplatte ($($drive.DriveType)): $full"
    }
    # F9: Rechte lassen sich nur auf NTFS/ReFS einschraenken (USB-Platten mit
    # exFAT/FAT32 melden sich ebenfalls als "Fixed").
    $format = ''
    try { $format = [string]$drive.DriveFormat } catch { $format = '' }
    if ($format -notin @('NTFS', 'ReFS')) {
        return "Der Ordner liegt nicht auf einem NTFS-Laufwerk ($format) – dort lassen sich die Zugriffsrechte nicht einschränken: $full"
    }
    $link = Get-ZwLinkInPath $full
    if ($link) {
        return "Der Pfad führt über eine Verknüpfung (Junction/Symlink) und wird nicht freigegeben: $link"
    }
    $forbidden = @($env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432, $env:ProgramData, $installDir) | Where-Object { $_ }
    foreach ($f in $forbidden) {
        if (Test-ZwUnder $full $f) { return "Systemordner oder Programmordner werden nicht freigegeben: $full" }
    }
    # Datenordner selbst, seine Elternordner und die von der App verwalteten
    # Ablagen (Datenbank, Archiv, Uploads, Backups ...) - wie im Backend.
    if ($dataRoot) {
        $dr = [System.IO.Path]::GetFullPath($dataRoot).TrimEnd('\')
        if ((Test-ZwUnder $dr $full) -or (Test-ZwSamePath $full "$dr\data")) {
            return "Der Datenordner (bzw. ein Elternordner) wird nicht freigegeben: $full"
        }
        foreach ($managed in 'data\archive', 'data\uploads', 'data\thumbnails', 'data\backups', 'data\chromadb', 'logs') {
            if (Test-ZwUnder $full "$dr\$managed") { return "Von der App verwaltete Ablagen werden nicht freigegeben: $full" }
        }
    }
    $usersRoot = if ($env:PUBLIC) { Split-Path -Parent $env:PUBLIC } else { "$env:SystemDrive\Users" }
    if ($full.TrimEnd('\') -ieq $usersRoot.TrimEnd('\')) { return "Der Benutzerordner selbst wird nicht freigegeben: $full" }
    if (Test-ZwUnder $full $usersRoot) {
        $profileName = $full.Substring($usersRoot.TrimEnd('\').Length + 1).Split('\')[0]
        $allowed = @('Public')
        if ($dataRoot -and (Test-ZwUnder $dataRoot $usersRoot)) { $allowed += $dataRoot.Substring($usersRoot.TrimEnd('\').Length + 1).Split('\')[0] }
        if (-not ($allowed | Where-Object { $_ -ieq $profileName })) {
            return "Der Ordner liegt im Profil eines anderen Benutzers: $full"
        }
    }
    if (Test-Path -LiteralPath $full) {
        $item = Get-Item -LiteralPath $full -Force
        if (-not $item.PSIsContainer) { return "Kein Ordner: $full" }
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            return "Der Ordner ist eine Verknüpfung (Junction/Symlink) und wird nicht freigegeben: $full"
        }
    } else {
        return "Der Ordner existiert nicht: $full"
    }
    return ''
}

# --------------------------------------------------------------- NTFS
function Set-ZwDirAcl([string]$path, [scriptblock]$change) {
    $item = Get-Item -LiteralPath $path -Force
    $acl = Get-Acl -LiteralPath $path
    & $change $acl
    # Nur die DACL schreiben (Besitzer bleibt) - wie secure-acl.ps1.
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.DirectoryInfo]$item, [System.Security.AccessControl.DirectorySecurity]$acl)
    } else {
        $item.SetAccessControl($acl)
    }
}

function New-ZwRule([string]$sid, [System.Security.AccessControl.FileSystemRights]$rights, [string]$inherit, [string]$propagate, [string]$type) {
    $id = New-Object System.Security.Principal.SecurityIdentifier($sid)
    return New-Object System.Security.AccessControl.FileSystemAccessRule($id, $rights, $inherit, $propagate, $type)
}

# Rechte des Scanner-Kontos (siehe Kopf): nur dieser Ordner, ohne Vererbung.
$ZwScannerRootRights = [System.Security.AccessControl.FileSystemRights]'ListDirectory, CreateFiles, ReadAttributes, ReadExtendedAttributes, Traverse, Synchronize'
$ZwCreatorOwnerRights = [System.Security.AccessControl.FileSystemRights]'Modify, Synchronize'

function Grant-ZwScannerAcl([string]$dir, [string]$sid) {
    Invoke-ZwStep 'Acl.Grant' @{ Path = $dir; Sid = $sid; Rights = $ZwScannerRootRights.ToString(); Inherit = 'None' } {
        Set-ZwDirAcl $dir {
            param($acl)
            $acl.PurgeAccessRules((New-Object System.Security.Principal.SecurityIdentifier($sid)))
            $acl.AddAccessRule((New-ZwRule $sid $ZwScannerRootRights 'None' 'None' 'Allow'))
            # Eigene Dateien darf das Konto bearbeiten/umbenennen (Temp-Datei -> PDF).
            $acl.AddAccessRule((New-ZwRule $ZwSidCreatorOwner $ZwCreatorOwnerRights 'ObjectInherit' 'InheritOnly, NoPropagateInherit' 'Allow'))
        }
    }
    foreach ($sub in $ZwSubDirs) {
        $subDir = Join-Path $dir $sub
        Invoke-ZwStep 'Acl.Deny' @{ Path = $subDir; Sid = $sid } {
            if (-not (Test-Path -LiteralPath $subDir)) { New-Item -ItemType Directory -Path $subDir | Out-Null }
            Set-ZwDirAcl $subDir {
                param($acl)
                $acl.AddAccessRule((New-ZwRule $sid ([System.Security.AccessControl.FileSystemRights]::FullControl) 'ContainerInherit, ObjectInherit' 'None' 'Deny'))
            }
        }
    }
}

# F1: Allow-Eintraege (auch geerbte) fuer Jeder/Benutzer/Authentifizierte
# Benutzer/Interaktiv/Netzwerk - die gaelten ueber die Freigabe auch fuer das
# Scanner-Konto. Nur lesend (auch im DryRun).
function Get-ZwBroadAccess([string]$dir) {
    $acl = Get-Acl -LiteralPath $dir
    $sids = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
        Where-Object { $_.AccessControlType -eq 'Allow' -and $ZwBroadSids -contains $_.IdentityReference.Value } |
        ForEach-Object { $_.IdentityReference.Value })
    return @($sids | Select-Object -Unique)
}

# Vererbung abschalten (vorhandene Eintraege als explizite uebernehmen), dann
# alle breiten Allow-Eintraege entfernen; SYSTEM, Administratoren, Besitzer und
# das ausfuehrende Konto behalten Vollzugriff (wie secure-acl.ps1).
function Set-ZwPrivateAcl([string]$dir) {
    Set-ZwDirAcl $dir {
        param($acl)
        if (-not $acl.AreAccessRulesProtected) { $acl.SetAccessRuleProtection($true, $true) }
        foreach ($rule in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
            if ($rule.AccessControlType -eq 'Allow' -and $ZwBroadSids -contains $rule.IdentityReference.Value) {
                [void]$acl.RemoveAccessRuleSpecific($rule)
            }
        }
        $owner = $null
        try { $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value } catch { $owner = $null }
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        foreach ($keep in (@('S-1-5-18', 'S-1-5-32-544', $me, $owner) | Where-Object { $_ } | Select-Object -Unique)) {
            if ($ZwBroadSids -contains $keep) { continue }
            $acl.AddAccessRule((New-ZwRule $keep ([System.Security.AccessControl.FileSystemRights]::FullControl) 'ContainerInherit, ObjectInherit' 'None' 'Allow'))
        }
    }
    if (@(Get-ZwBroadAccess $dir).Count -gt 0) { throw "Breite Zugriffsrechte auf $dir lassen sich nicht entfernen." }
}

function Revoke-ZwScannerAcl([string]$dir, [string]$sid) {
    if (-not $dir) { return }
    Invoke-ZwStep 'Acl.Revoke' @{ Path = $dir; Sid = $sid } {
        if (-not (Test-Path -LiteralPath $dir)) { return }
        Set-ZwDirAcl $dir {
            param($acl)
            $acl.PurgeAccessRules((New-Object System.Security.Principal.SecurityIdentifier($sid)))
            [void]$acl.RemoveAccessRuleSpecific((New-ZwRule $ZwSidCreatorOwner $ZwCreatorOwnerRights 'ObjectInherit' 'InheritOnly, NoPropagateInherit' 'Allow'))
        }
        foreach ($sub in $ZwSubDirs) {
            $subDir = Join-Path $dir $sub
            if (Test-Path -LiteralPath $subDir) {
                Set-ZwDirAcl $subDir { param($acl) $acl.PurgeAccessRules((New-Object System.Security.Principal.SecurityIdentifier($sid))) }
            }
        }
    }
}

# --------------------------------------------------------------- Ergebnis
function Write-ZwResultFile([System.Collections.IDictionary]$values) {
    if (-not $ResultFile) { return }
    $lines = @('[share]')
    foreach ($k in $values.Keys) { $lines += ('{0}={1}' -f $k, ($values[$k] -replace '[\r\n]', ' ')) }
    $dir = Split-Path -Parent $ResultFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    # UTF-16LE mit BOM: NSIS liest sie mit ReadINIStr (GetPrivateProfileStringW).
    [System.IO.File]::WriteAllText($ResultFile, (($lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::Unicode)
    # Enthaelt das Passwort: nur SYSTEM, Administratoren und das ausfuehrende Konto.
    . (Join-Path $PSScriptRoot 'secure-acl.ps1')
    [void](Protect-ZwPath -LiteralPath $ResultFile)
}

function Write-ZwWarn([string]$text) {
    Write-Host "[WARNUNG] $text" -ForegroundColor Yellow
    $script:Warnings += $text
}

# --------------------------------------------------------------- Ablauf
function Resolve-ZwWatchDir {
    if ($WatchDir) { return $WatchDir.Trim().Trim('"') }
    $installDir = Get-ZwRegistryValue 'InstallDir'
    $configPath = Get-ZwRegistryValue 'ConfigPath'
    $exe = Join-Path $installDir 'backend\zettelwirtschaft-backend.exe'
    if (-not $installDir -or -not (Test-Path -LiteralPath $exe)) {
        throw 'Zettelwirtschaft-Installation nicht gefunden – bitte -WatchDir angeben.'
    }
    $out = & $exe --config $configPath --print-watch-dir 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Der Eingangsordner konnte nicht ermittelt werden (--print-watch-dir).' }
    return (@($out) | Select-Object -Last 1).Trim()
}

# F6: ohne -ShareName/-UserName die beim Einrichten gespeicherten Namen
# verwenden - sonst blieben ein eigens benanntes Konto und seine Freigabe
# nach der Deinstallation stehen (ohne Spur in der Registry).
function Use-ZwRegisteredNames {
    if (-not $ZwExplicitShareName) {
        $registered = Get-ZwRegistryValue 'ScannerShareName'
        if ($registered -match $ZwShareNamePattern) { $script:ShareName = $registered }
    }
    if (-not $ZwExplicitUserName) {
        $registered = Get-ZwRegistryValue 'ScannerShareUser'
        $name = if ($registered.Contains('\')) { $registered.Substring($registered.LastIndexOf('\') + 1) } else { $registered }
        if ($name -match $ZwUserNamePattern) { $script:UserName = $name }
    }
}

function Remove-ZwAccount([string]$sid) {
    Invoke-ZwStep 'Lsa.RemoveAll' @{ Sid = $sid } { Initialize-ZwLsa; [ZwLsa]::RemoveAllRights($sid) }
    Invoke-ZwStep 'User.Remove' @{ Name = $UserName } { Remove-LocalUser -Name $UserName -ErrorAction Stop }
    Invoke-ZwStep 'Registry.RemoveHidden' @{ Path = $ZwSpecialAccounts; Name = $UserName } {
        Remove-ItemProperty -LiteralPath $ZwSpecialAccounts -Name $UserName -ErrorAction SilentlyContinue
    }
}

function Invoke-ZwRemove {
    Use-ZwRegisteredNames
    $sharePath = Get-ZwSharePath
    $registeredPath = Get-ZwRegistryValue 'ScannerSharePath'
    $userExists = Test-ZwUserExists
    $sid = $null
    if ($userExists) { $sid = Get-ZwUserSid }

    if ($sharePath) {
        Invoke-ZwStep 'Share.Remove' @{ Name = $ShareName } { Remove-SmbShare -Name $ShareName -Force -ErrorAction Stop }
        Write-Host "[OK] Freigabe $ShareName entfernt"
    }
    # NTFS-Eintraege VOR dem Konto entfernen - sonst bleiben verwaiste SIDs stehen.
    if ($sid) {
        foreach ($dir in (@($sharePath, $registeredPath, $WatchDir) | Where-Object { $_ } | Select-Object -Unique)) {
            Revoke-ZwScannerAcl $dir $sid
        }
    }
    if (Test-ZwFirewallRule) {
        Invoke-ZwStep 'Firewall.Remove' @{ DisplayName = $ZwFirewallRule } { Remove-NetFirewallRule -DisplayName $ZwFirewallRule -ErrorAction Stop }
        Write-Host '[OK] Firewall-Regel entfernt'
    }
    if ($userExists) {
        Invoke-ZwStep 'Lsa.RemoveAll' @{ Sid = $sid } { Initialize-ZwLsa; [ZwLsa]::RemoveAllRights($sid) }
        Invoke-ZwStep 'User.Remove' @{ Name = $UserName } { Remove-LocalUser -Name $UserName -ErrorAction Stop }
        Write-Host "[OK] Benutzer $UserName entfernt"
    }
    Invoke-ZwStep 'Registry.RemoveHidden' @{ Path = $ZwSpecialAccounts; Name = $UserName } {
        Remove-ItemProperty -LiteralPath $ZwSpecialAccounts -Name $UserName -ErrorAction SilentlyContinue
    }
    # Die Registry-Werte sind die einzige Spur der Einrichtung: erst loeschen,
    # wenn Freigabe UND Konto wirklich weg sind.
    if (-not $DryRun -and ((Get-ZwSharePath) -or (Test-ZwUserExists))) {
        throw "Freigabe $ShareName bzw. Konto $UserName konnten nicht vollständig entfernt werden."
    }
    Invoke-ZwStep 'Registry.Remove' @{ Path = $RegistryKey; Names = 'ScannerShareName,ScannerShareUser,ScannerSharePath' } {
        foreach ($n in 'ScannerShareName', 'ScannerShareUser', 'ScannerSharePath') {
            Remove-ItemProperty -LiteralPath $RegistryKey -Name $n -ErrorAction SilentlyContinue
        }
    }
    Write-Host '[OK] Scanner-Freigabe entfernt (bzw. war nicht eingerichtet).'
}

function Invoke-ZwSetup {
    $script:ZwCreatedUser = $false
    $script:ZwCreatedSid = $null
    $script:ZwRestoreShare = $null
    $dir = Resolve-ZwWatchDir
    $dataRoot = if ($DataDir) { [System.IO.Path]::GetFullPath($DataDir.Trim().Trim('"')) } else { Get-ZwRegistryValue 'DataDir' }
    $installDir = Get-ZwRegistryValue 'InstallDir'
    $blocker = Get-ZwShareBlocker $dir $dataRoot $installDir
    if ($blocker) {
        Write-Host "FEHLER: $blocker" -ForegroundColor Red
        Write-ZwResultFile ([ordered]@{ status = 'error'; reason = $blocker })
        return 4
    }
    $dir = [System.IO.Path]::GetFullPath($dir).TrimEnd('\')
    $insideData = [bool]($dataRoot -and (Test-ZwUnder $dir $dataRoot))
    $broad = @(Get-ZwBroadAccess $dir)
    $script:ZwSetupDir = $dir
    $script:ZwSetupBroad = $broad
    if (-not $insideData) {
        # Der Watch-Ordner kommt aus der Weboberflaeche (PIN-Inhaber) - ein
        # Ordner ausserhalb des Datenordners wird nur nach Bestaetigung geteilt.
        if ($ResultFile) {
            $reason = "Der Ordner liegt nicht im Datenordner und wird im Setup nicht freigegeben: $dir"
            Write-Host "FEHLER: $reason" -ForegroundColor Red
            Write-ZwResultFile ([ordered]@{ status = 'error'; reason = $reason })
            return 4
        }
        if (-not $Yes) {
            Write-Host ''
            Write-Host "Dieser Ordner wird im Netzwerk für das Scanner-Konto beschreibbar:" -ForegroundColor Yellow
            Write-Host "  $dir"
            if ($broad.Count -gt 0) {
                Write-Host 'Der Ordner ist für alle Benutzer dieses PCs zugänglich. Diese breiten Rechte werden entfernt' -ForegroundColor Yellow
                Write-Host '(nur noch SYSTEM, Administratoren und der Besitzer) - sonst gälten sie auch für das Scanner-Konto.' -ForegroundColor Yellow
            }
            $answer = Read-Host 'Wirklich freigeben? (ja/nein)'
            if ($answer -notmatch '^(j|ja|y|yes)$') { Write-Host 'Abgebrochen.'; return 4 }
        }
    }

    $computer = $env:COMPUTERNAME
    $userLabel = "$computer\$UserName"
    $password = $null
    $userExisted = Test-ZwUserExists
    if ($userExisted) {
        # F7: ein vorhandenes Konto nur uebernehmen, wenn es in keiner Gruppe ist
        # (z.B. Administratoren) - es bekaeme sonst deren Rechte ueber die Freigabe.
        $groups = @(Get-ZwUserGroups)
        if ($groups.Count -gt 0) {
            throw "Das vorhandene Konto $userLabel ist Mitglied von: $($groups -join ', '). Bitte das Konto entfernen (-Remove) oder einen anderen -UserName wählen."
        }
        if (-not (Test-ZwUserEnabled)) {
            Write-ZwWarn "Das Konto $userLabel ist deaktiviert – der Scanner kann sich so nicht anmelden."
        }
    }

    try {
        Invoke-ZwSetupSteps
    } catch {
        Undo-ZwSetup
        throw
    }
    return 0
}

# Fehlschlag mitten im Einrichten (F9): zuruecknehmen, was dieser Lauf neu
# angelegt hat - ein neues Konto mit Rechten aber ohne Freigabe bliebe sonst stehen.
function Undo-ZwSetup {
    if ($script:ZwRestoreShare) {
        $old = $script:ZwRestoreShare
        try {
            Invoke-ZwStep 'Share.Restore' @{ Name = $ShareName; Path = $old; ChangeAccess = $script:ZwUserLabel } {
                New-SmbShare -Name $ShareName -Path $old -ChangeAccess $script:ZwUserLabel -FolderEnumerationMode AccessBased -CachingMode None `
                    -Description 'Zettelwirtschaft: Eingang für Netzwerk-Scanner' -ErrorAction Stop | Out-Null
            }
            Write-Host "[OK] Bisherige Freigabe wiederhergestellt: $old"
        } catch { Write-Host "WARNUNG: Bisherige Freigabe konnte nicht wiederhergestellt werden: $($_.Exception.Message)" -ForegroundColor Yellow }
    }
    if ($script:ZwCreatedUser) {
        try {
            if ($script:ZwCreatedSid) { Revoke-ZwScannerAcl $script:ZwSetupDir $script:ZwCreatedSid }
            Remove-ZwAccount $script:ZwCreatedSid
            Write-Host "[OK] Neu angelegtes Konto $UserName wieder entfernt"
        } catch { Write-Host "WARNUNG: Konto $UserName konnte nicht entfernt werden: $($_.Exception.Message)" -ForegroundColor Yellow }
    }
}

function Invoke-ZwSetupSteps {
    $dir = $script:ZwSetupDir
    $broad = $script:ZwSetupBroad
    $computer = $env:COMPUTERNAME
    $userLabel = "$computer\$UserName"
    $script:ZwUserLabel = $userLabel
    $password = $null

    # 1. Konto
    if (Test-ZwUserExists) {
        if ($NewPassword -or $ResultFile) {
            $password = New-ZwScannerPassword
            $secure = ConvertTo-SecureString -String $password -AsPlainText -Force
            Invoke-ZwStep 'User.SetPassword' @{ Name = $UserName; Password = '***' } { Set-LocalUser -Name $UserName -Password $secure }
            Write-Host "[OK] Neues Passwort für $userLabel gesetzt"
            if ($ResultFile -and -not $NewPassword) {
                Write-Host '     (Setup: das bisherige Passwort wird nirgends angezeigt - deshalb ein neues)'
            }
        } else {
            Write-Host "[OK] Benutzer $userLabel existiert bereits (Passwort unverändert; neues mit -NewPassword)"
        }
    } else {
        $password = New-ZwScannerPassword
        $secure = ConvertTo-SecureString -String $password -AsPlainText -Force
        Invoke-ZwStep 'User.Create' @{ Name = $UserName; Password = '***'; Groups = '' } {
            New-LocalUser -Name $UserName -Password $secure -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires `
                -Description 'Zettelwirtschaft Netzwerk-Scanner' -ErrorAction Stop | Out-Null
        }
        $script:ZwCreatedUser = $true
        Write-Host "[OK] Benutzer $userLabel angelegt (in keiner lokalen Gruppe)"
    }
    $sid = Get-ZwUserSid
    if ($script:ZwCreatedUser) { $script:ZwCreatedSid = $sid }
    $denyRights = 'SeDenyInteractiveLogonRight', 'SeDenyRemoteInteractiveLogonRight', 'SeDenyBatchLogonRight', 'SeDenyServiceLogonRight'
    Invoke-ZwStep 'Lsa.DenyLogon' @{ Sid = $sid; Rights = ($denyRights -join ',') } {
        Initialize-ZwLsa
        foreach ($right in $denyRights) { [ZwLsa]::AddRight($sid, $right) }
    }
    Invoke-ZwStep 'Registry.Hide' @{ Path = $ZwSpecialAccounts; Name = $UserName; Value = 0 } {
        if (-not (Test-Path -LiteralPath $ZwSpecialAccounts)) { New-Item -Path $ZwSpecialAccounts -Force | Out-Null }
        New-ItemProperty -LiteralPath $ZwSpecialAccounts -Name $UserName -PropertyType DWord -Value 0 -Force | Out-Null
    }
    Write-Host '[OK] Konto ohne Anmeldung am PC, im Anmeldebildschirm ausgeblendet'

    # 2. NTFS
    $existing = Get-ZwSharePath
    # F5: Ordnerwechsel - die Rechte des Kontos auf dem bisherigen Ordner
    # zuruecknehmen (die Registry kennt danach nur noch den neuen).
    $previous = @($existing, (Get-ZwRegistryValue 'ScannerSharePath')) | Where-Object { $_ -and -not (Test-ZwSamePath $_ $dir) } | Select-Object -Unique
    foreach ($old in $previous) {
        Revoke-ZwScannerAcl $old $sid
        Write-Host "[OK] Rechte des Scanner-Kontos auf dem bisherigen Ordner entfernt: $old"
    }
    if ($broad.Count -gt 0) {
        Invoke-ZwStep 'Acl.Protect' @{ Path = $dir; Removed = ($broad -join ',') } { Set-ZwPrivateAcl $dir }
        Write-Host "[OK] Breite Rechte (alle Benutzer) auf $dir entfernt"
    }
    Grant-ZwScannerAcl $dir $sid
    Write-Host "[OK] Schreibrecht (nur neue Dateien) auf $dir"

    # 3. Freigabe (F9: eine funktionierende Freigabe nie ersatzlos loeschen)
    if ($existing -and (Test-ZwSamePath $existing $dir)) {
        Invoke-ZwStep 'Share.UpdateAccess' @{ Name = $ShareName; Path = $dir; ChangeAccess = $userLabel } {
            foreach ($access in @(Get-SmbShareAccess -Name $ShareName -ErrorAction Stop)) {
                if ($access.AccountName -ine $userLabel) {
                    Revoke-SmbShareAccess -Name $ShareName -AccountName $access.AccountName -Force -ErrorAction Stop | Out-Null
                }
            }
            Grant-SmbShareAccess -Name $ShareName -AccountName $userLabel -AccessRight Change -Force -ErrorAction Stop | Out-Null
            Set-SmbShare -Name $ShareName -FolderEnumerationMode AccessBased -CachingMode None -Force -ErrorAction Stop
        }
    } else {
        if ($existing) {
            Invoke-ZwStep 'Share.Remove' @{ Name = $ShareName } { Remove-SmbShare -Name $ShareName -Force -ErrorAction Stop }
            $script:ZwRestoreShare = $existing
        }
        Invoke-ZwStep 'Share.Create' @{ Name = $ShareName; Path = $dir; ChangeAccess = $userLabel; FullAccess = ''; ReadAccess = ''; FolderEnumerationMode = 'AccessBased' } {
            New-SmbShare -Name $ShareName -Path $dir -ChangeAccess $userLabel -FolderEnumerationMode AccessBased -CachingMode None `
                -Description 'Zettelwirtschaft: Eingang für Netzwerk-Scanner' -ErrorAction Stop | Out-Null
        }
        $script:ZwRestoreShare = $null
    }
    Write-Host "[OK] Freigabe \\$computer\$ShareName (nur $userLabel, Ändern)"

    # 4. Firewall (eigene Regel, die System-Gruppe bleibt unveraendert)
    $remote = if ($ScannerAddress) { $ScannerAddress } else { 'LocalSubnet' }
    if (-not (Test-ZwFirewallRule)) {
        Invoke-ZwStep 'Firewall.Create' @{ DisplayName = $ZwFirewallRule; Group = 'Zettelwirtschaft'; Direction = 'Inbound'; Protocol = 'TCP'; LocalPort = '445'; RemoteAddress = $remote; Profile = 'Private'; Action = 'Allow' } {
            New-NetFirewallRule -DisplayName $ZwFirewallRule -Group 'Zettelwirtschaft' -Direction Inbound -Protocol TCP -LocalPort 445 `
                -RemoteAddress $remote -Profile Private -Action Allow -ErrorAction Stop | Out-Null
        }
    } elseif ($ScannerAddress) {
        Invoke-ZwStep 'Firewall.Update' @{ DisplayName = $ZwFirewallRule; RemoteAddress = $remote } {
            Set-NetFirewallRule -DisplayName $ZwFirewallRule -RemoteAddress $remote -ErrorAction Stop
        }
    }
    $from = if ($ScannerAddress) { "nur von $ScannerAddress" } else { 'nur aus dem lokalen Netz' }
    Write-Host "[OK] Firewall: SMB (TCP 445) $from, Profil Privat"
    Write-ZwWarn "Die Firewall-Regel öffnet den SMB-Dienst des ganzen PCs (alle Freigaben), nicht nur diese Freigabe. Mit -ScannerAddress <IP des Scanners> lässt sie sich auf den Scanner beschränken."
    foreach ($open in @(Get-ZwOpenShares)) {
        Write-ZwWarn "Die Freigabe ""$open"" erlaubt Zugriff für alle Benutzer ('Jeder'/'Benutzer') – sie ist auch mit den Scanner-Zugangsdaten erreichbar."
    }

    # 5. Pruefungen (nur Hinweise)
    $lanman = if ($DryRun) { [string](Get-ZwState 'LanmanServer' 'Running') } else { [string](Get-Service LanmanServer -ErrorAction SilentlyContinue).Status }
    if ($lanman -ne 'Running') { Write-ZwWarn 'Der Dienst "Server" (LanmanServer) läuft nicht – ohne ihn ist die Freigabe nicht erreichbar.' }
    $categories = if ($DryRun) { @(Get-ZwState 'NetworkCategories' @('Private')) } else { @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.NetworkCategory }) }
    if ($categories.Count -gt 0 -and -not ($categories -contains 'Private' -or $categories -contains 'DomainAuthenticated')) {
        Write-ZwWarn 'Das Netzwerk ist als "Öffentlich" eingestuft – die Firewall-Regel gilt nur im privaten Netzwerk (Einstellungen > Netzwerk > Netzwerkprofil "Privat").'
    }

    # 6. Registry (kein Passwort)
    Invoke-ZwStep 'Registry.Set' @{ Path = $RegistryKey; ScannerShareName = $ShareName; ScannerShareUser = $userLabel; ScannerSharePath = $dir } {
        if (-not (Test-Path -LiteralPath $RegistryKey)) { New-Item -Path $RegistryKey -Force | Out-Null }
        New-ItemProperty -LiteralPath $RegistryKey -Name 'ScannerShareName' -Value $ShareName -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $RegistryKey -Name 'ScannerShareUser' -Value $userLabel -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $RegistryKey -Name 'ScannerSharePath' -Value $dir -PropertyType String -Force | Out-Null
    }

    # 7. Ausgabe - das Passwort genau einmal
    $unc = "\\$computer\$ShareName"
    $passwordText = if ($password) { $password } else { '(unverändert)' }
    if ($ResultFile) {
        Write-ZwResultFile ([ordered]@{ status = 'ok'; unc = $unc; user = $userLabel; password = $passwordText; warnings = ($script:Warnings -join ' ') })
        Write-Host "[OK] Scanner-Freigabe eingerichtet: $unc (Zugangsdaten stehen auf der Abschlussseite)"
    } else {
        Write-Host ''
        Write-Host '================ Im Scanner eintragen ================' -ForegroundColor Cyan
        Write-Host "  Netzwerkordner: $unc"
        Write-Host "  Benutzer:       $userLabel"
        Write-Host "  Passwort:       $passwordText"
        Write-Host '======================================================' -ForegroundColor Cyan
        Write-Host 'Das Passwort wird nicht gespeichert – bei Verlust mit -NewPassword neu setzen.'
        Write-Host 'Der Scanner braucht SMB2 oder neuer (SMB1 wird bewusst nicht aktiviert).'
        Write-Host 'Alle Geräte mit diesen Zugangsdaten können noch nicht verarbeitete Scans lesen und löschen.'
    }
}

# Ein Argument fuer eine Windows-Befehlszeile quoten (CommandLineToArgvW-
# Regeln): Leerzeichen, Anfuehrungszeichen und Backslashes davor bleiben erhalten.
function ConvertTo-ZwArgument([string]$value) {
    if ($value -eq '') { return '""' }
    if ($value -notmatch '[\s"]') { return $value }
    $escaped = [regex]::Replace($value, '(\\*)"', { param($m) ($m.Groups[1].Value * 2) + '\"' })
    $escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    return '"' + $escaped + '"'
}

function Get-ZwRelaunchArguments {
    $list = @()
    foreach ($p in $ZwBoundParameters.GetEnumerator()) {
        if ($p.Key -in @('Elevate', 'PauseOnExit')) { continue }
        if ($p.Value -is [System.Management.Automation.SwitchParameter]) {
            if ($p.Value.IsPresent) { $list += "-$($p.Key)" }
        } else {
            $list += "-$($p.Key)"; $list += [string]$p.Value
        }
    }
    return $list
}

# F8: Rechte selbst anfordern - die Argumente gehen als sauber gequotete
# Befehlszeile an den neuen Prozess (kein cmd/powershell -Command dazwischen).
function Invoke-ZwElevated {
    $argLine = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath) + (Get-ZwRelaunchArguments) + @('-PauseOnExit') |
        ForEach-Object { ConvertTo-ZwArgument $_ }) -join ' '
    Write-Host 'Fordere Administrator-Rechte an... (das Einrichten läuft im neuen Fenster)'
    $exe = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path -LiteralPath $exe)) { $exe = (Get-Process -Id $PID).Path }
    try {
        $process = Start-Process -FilePath $exe -ArgumentList $argLine -Verb RunAs -Wait -PassThru
    } catch {
        Write-Host 'FEHLER: Administrator-Rechte wurden nicht erteilt.' -ForegroundColor Red
        return 3
    }
    return [int]$process.ExitCode
}

function Invoke-ZwMain {
    $script:Warnings = @()
    # UTF-8 ausdruecklich: Windows PowerShell 5.1 liest sonst ANSI und verstuemmelt Umlaute in Pfaden.
    if ($DryRunState) { $script:DryRunStateData = @{}; (Get-Content -LiteralPath $DryRunState -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $script:DryRunStateData[$_.Name] = $_.Value } }
    if (-not $DryRun) {
        $principal = New-Object System.Security.Principal.WindowsPrincipal([System.Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
            if ($Elevate) { return (Invoke-ZwElevated) }
            Write-Host 'FEHLER: Bitte als Administrator ausführen (freigabe-einrichten.bat fordert die Rechte an).' -ForegroundColor Red
            return 3
        }
    }
    try {
        if ($Remove) {
            Invoke-ZwRemove | Out-Null
            return 0
        }
        $rc = Invoke-ZwSetup
        return [int](@($rc)[-1])
    } catch {
        $msg = $_.Exception.Message
        Write-Host "FEHLER: $msg" -ForegroundColor Red
        Write-ZwResultFile ([ordered]@{ status = 'error'; reason = $msg })
        return 1
    }
}

# Dot-Sourcing (Tests): nur Funktionen laden.
if ($MyInvocation.InvocationName -eq '.') { return }

# 32-Bit-PowerShell (z.B. aus dem 32-Bit-Setup) -> als 64-Bit-Prozess neu starten.
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
    $ps64 = Join-Path $env:WINDIR 'sysnative\WindowsPowerShell\v1.0\powershell.exe'
    $relaunch = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath) + (Get-ZwRelaunchArguments)
    if ($Elevate) { $relaunch += '-Elevate' }
    if ($PauseOnExit) { $relaunch += '-PauseOnExit' }
    & $ps64 @relaunch
    exit $LASTEXITCODE
}

$ZwExitCode = Invoke-ZwMain
if ($PauseOnExit) { [void](Read-Host 'Eingabetaste drücken zum Schließen') }
exit $ZwExitCode
