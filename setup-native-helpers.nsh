; setup-native-helpers.nsh - Hilfsfunktionen fuer setup-native.nsi.
; Ausgelagert, damit scripts/test-scripts.ps1 sie in einem kleinen Test-Installer
; real ausfuehren kann (Regression: kaputter Fernet-Key-Einzeiler -> ungueltige
; config.toml). Erwartet LogicLib.nsh.

!ifndef ZW_SETUP_NATIVE_HELPERS
!define ZW_SETUP_NATIVE_HELPERS

!define POWERSHELL '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass'

; --- Helper: Geheimnis erzeugen ---
; Stack in: "pin" | "fernet". Stack out: Wert, "" bei Fehler.
; Nutzt $PLUGINSDIR\new-secret.ps1 (in .onInit entpackt). Kein Inline-
; PowerShell: NSIS kennt kein ''-Escape und interpretiert $variablen selbst.
Function GenerateSecret
    Exch $R0  ; kind
    Push $R1
    Push $R2
    nsExec::ExecToStack '${POWERSHELL} -File "$PLUGINSDIR\new-secret.ps1" -Kind $R0'
    Pop $R1  ; exit code
    Pop $R2  ; output
    ${If} $R1 == "0"
        Push $R2
        Call TrimNewlines
        Pop $R2
    ${Else}
        StrCpy $R2 ""
    ${EndIf}
    ; Format pruefen: PIN 6 Stellen, Fernet-Key 44 Zeichen
    StrLen $R1 $R2
    ${If} $R0 == "pin"
    ${AndIf} $R1 != 6
        StrCpy $R2 ""
    ${ElseIf} $R0 == "fernet"
    ${AndIf} $R1 != 44
        StrCpy $R2 ""
    ${EndIf}
    StrCpy $R0 $R2
    Pop $R2
    Pop $R1
    Exch $R0
FunctionEnd

; --- Helper: legacy-install.ps1 ausfuehren ---
; Stack in: Argumente (z.B. '-Action Detect -ResultFile "..."'). Stack out: Exit-Code
; ("0" = Erfolg). Ergebnisse stehen in der -ResultFile-INI (UTF-16LE, ReadINIStr),
; nicht in der Konsolenausgabe: die kaeme ueber nsExec in der OEM-/ANSI-Codepage
; an und verstuemmelte Pfade mit Umlauten. Erwartet $PLUGINSDIR\legacy-install.ps1.
Function LegacyHelper
    Exch $R0  ; Argumente
    Push $R1
    nsExec::ExecToStack '${POWERSHELL} -File "$PLUGINSDIR\legacy-install.ps1" $R0'
    Pop $R0  ; exit code
    Pop $R1  ; output (verworfen)
    Pop $R1
    Exch $R0
FunctionEnd

; --- Helper: ueberschneiden sich zwei Ordner? ---
; Stack in: Pfad A, dann Pfad B pushen. Stack out: "1", wenn A und B gleich sind
; oder einer im anderen liegt (ohne Gross-/Kleinschreibung), sonst "0".
Function PathsOverlap
    Exch $R1  ; B
    Exch
    Exch $R0  ; A
    Push $R2
    Push $R3
    StrCpy $R3 "0"
    ${If} $R0 != ""
    ${AndIf} $R1 != ""
        StrCpy $R2 $R0 1 -1
        ${If} $R2 == "\"
            StrCpy $R0 $R0 -1
        ${EndIf}
        StrCpy $R2 $R1 1 -1
        ${If} $R2 == "\"
            StrCpy $R1 $R1 -1
        ${EndIf}
        StrCpy $R0 "$R0\"
        StrCpy $R1 "$R1\"
        ; StrCmp (LogicLib ==) vergleicht ohne Gross-/Kleinschreibung.
        StrLen $R2 $R1
        StrCpy $R2 $R0 $R2
        ${If} $R2 == $R1
            StrCpy $R3 "1"
        ${EndIf}
        StrLen $R2 $R0
        StrCpy $R2 $R1 $R2
        ${If} $R2 == $R0
            StrCpy $R3 "1"
        ${EndIf}
    ${EndIf}
    StrCpy $R0 $R3
    Pop $R3
    Pop $R2
    Exch
    Pop $R1
    Exch $R0
FunctionEnd

; Schneidet am ersten CR/LF/Leerzeichen ab. Sichert alle benutzten Register.
Function TrimNewlines
    Exch $R0
    Push $R1
    Push $R2
    StrCpy $R1 0
    loop:
        StrCpy $R2 $R0 1 $R1
        StrCmp $R2 "$\r" trim
        StrCmp $R2 "$\n" trim
        StrCmp $R2 " " trim
        StrCmp $R2 "" done
        IntOp $R1 $R1 + 1
        Goto loop
    trim:
        StrCpy $R0 $R0 $R1
    done:
        Pop $R2
        Pop $R1
        Exch $R0
FunctionEnd

!endif
