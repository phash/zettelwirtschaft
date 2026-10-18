# new-secret.ps1 - Erzeugt Geheimnisse fuer den Native-Installer (setup-native.nsi).
#
# Ausgelagert statt Inline-PowerShell im NSIS-Skript: NSIS kennt kein ''-Escape
# und interpretiert $variablen selbst, wodurch der Inline-Befehl fuer den
# Fernet-Key frueher zerbrach und eine ungueltige config.toml entstand.
# Als eigene Datei ist die Logik ohne Quoting-Fallen aufrufbar und testbar
# (scripts/test-scripts.ps1).
#
# Aufruf:
#   powershell -NoProfile -ExecutionPolicy Bypass -File new-secret.ps1 -Kind pin
#   powershell -NoProfile -ExecutionPolicy Bypass -File new-secret.ps1 -Kind fernet
#   powershell -NoProfile -ExecutionPolicy Bypass -File new-secret.ps1 -Kind password
#
# Ausgabe: genau eine Zeile ohne Leerzeichen (Pipeline/stdout), Exit 0. Bei
# Fehlern Exit 1 und Meldung auf stderr. Aus PowerShell heraus aufrufbar
# ($key = & "$PSScriptRoot\new-secret.ps1" -Kind fernet; so nutzt es
# New-NativeConfig.ps1).
#   pin    -> 6-stelliger numerischer PIN (gleiches Verfahren wie install-gui.ps1)
#   fernet -> 44 Zeichen URL-safe Base64 (32 Zufallsbytes, Fernet-Key-Format)
#   password -> 16 Zeichen aus A-Z a-z 2-9 ohne verwechselbare Zeichen (I O l o 0 1),
#               ~93 Bit; fuer das Scanner-Konto (freigabe-einrichten.ps1) - viele
#               Scanner erlauben keine Sonderzeichen und hoechstens 32 Zeichen.
#               Enthaelt immer Gross-, Kleinbuchstaben UND Ziffern: mit aktiver
#               Kennwort-Komplexitaetsrichtlinie scheiterte sonst jedes ~12.
#               Kennwort (ohne Ziffer) beim Anlegen des Kontos.
# Windows PowerShell 5.1 kompatibel (kein RandomNumberGenerator::GetInt32).

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('pin', 'fernet', 'password')]
    [string]$Kind
)

$ErrorActionPreference = 'Stop'

function Get-RandomBytes([int]$count) {
    $bytes = New-Object byte[] $count
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ,$bytes
}

try {
    switch ($Kind) {
        'pin' {
            # Rejection Sampling: nur Werte unterhalb des groessten Vielfachen
            # von 10^6 annehmen, damit die Modulo-Reduktion gleichverteilt ist.
            $limit = [uint32]([math]::Floor([uint32]::MaxValue / 1000000) * 1000000)
            do {
                $num = [BitConverter]::ToUInt32((Get-RandomBytes 4), 0)
            } while ($num -ge $limit)
            $value = '{0:D6}' -f ($num % 1000000)
        }
        'fernet' {
            $value = [Convert]::ToBase64String((Get-RandomBytes 32)).Replace('+', '-').Replace('/', '_')
        }
        'password' {
            $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
            # Rejection Sampling: nur Bytes unterhalb des groessten Vielfachen der
            # Alphabetlaenge, damit jedes Zeichen gleich wahrscheinlich ist.
            $limit = [math]::Floor(256 / $alphabet.Length) * $alphabet.Length
            do {
                $chars = New-Object System.Text.StringBuilder
                while ($chars.Length -lt 16) {
                    foreach ($b in (Get-RandomBytes 32)) {
                        if ($b -lt $limit -and $chars.Length -lt 16) { [void]$chars.Append($alphabet[$b % $alphabet.Length]) }
                    }
                }
                $value = $chars.ToString()
                # Verwerfen statt nachbessern: die Verteilung bleibt gleichmaessig.
            } while ($value -cnotmatch '[A-Z]' -or $value -cnotmatch '[a-z]' -or $value -notmatch '[2-9]')
        }
    }
    Write-Output $value
    exit 0
} catch {
    [Console]::Error.WriteLine("Geheimnis konnte nicht erzeugt werden: $_")
    exit 1
}
