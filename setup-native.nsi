; Zettelwirtschaft Native-Windows-Installer
;
; Build:  makensis /DVERSION=1.3.0 /DDIST=dist\native setup-native.nsi
; Output: Zettelwirtschaft-1.3.0-Native-Setup.exe
;
; Im Gegensatz zum alten setup.nsi (Docker-basiert) braucht dieser Setup
; weder Docker Desktop noch eine externe install-gui.ps1. Backend laeuft als
; Windows-Service, ChromaDB embedded, Tesseract+poppler gebundled.

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
    !define VERSION "dev"
!endif
!ifndef DIST
    !define DIST "dist\native"
!endif

; --- General ---
Name "Zettelwirtschaft ${VERSION}"
OutFile "Zettelwirtschaft-${VERSION}-Native-Setup.exe"
Unicode True
InstallDir "$PROGRAMFILES64\Zettelwirtschaft"
InstallDirRegKey HKLM "Software\Zettelwirtschaft" "InstallDir"
RequestExecutionLevel admin
BrandingText "Zettelwirtschaft ${VERSION} Native"

; --- MUI ---
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Zettelwirtschaft ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Willkommen.$\r$\n$\r$\nDieses Setup installiert Zettelwirtschaft als Windows-Service.$\r$\n$\r$\nKeine Docker-Installation noetig. Ollama (LLM-Server) wird im Anschluss als optionaler Download angeboten.$\r$\n$\r$\nKlicke 'Weiter' um zu beginnen."

!define MUI_DIRECTORYPAGE_TEXT_TOP "Programmordner waehlen. Programmdateien werden hier abgelegt; die Nutzerdaten kommen in einen separaten Ordner (im naechsten Schritt)."

; Variablen fuer Custom-Pages
Var DATA_DIR
Var DATA_DIR_LABEL
Var DATA_DIR_TEXT
Var DATA_DIR_BUTTON
Var PIN_CODE
Var SERVER_PORT
Var CONFIG_PATH
Var FRONTEND_DIST_PATH

; --- Pages ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY

; Custom Page: Datenordner waehlen
Page custom DataDirPage DataDirPageLeave

!insertmacro MUI_PAGE_INSTFILES

; FINISH-Page: Browser oeffnen
!define MUI_FINISHPAGE_TITLE "Installation abgeschlossen"
!define MUI_FINISHPAGE_TEXT "Zettelwirtschaft laeuft als Hintergrunddienst.$\r$\n$\r$\nDie Weboberflaeche ist erreichbar unter:$\r$\nhttp://localhost:$SERVER_PORT$\r$\n$\r$\nPIN: $PIN_CODE (bitte notieren!)"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Browser jetzt oeffnen"
!define MUI_FINISHPAGE_RUN_FUNCTION "OpenBrowser"
!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Logs im Datenordner ansehen"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION "OpenLogs"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "German"

; --- Init ---
Function .onInit
    StrCpy $DATA_DIR "$DOCUMENTS\Zettelwirtschaft"
    StrCpy $SERVER_PORT "8080"

    ; PIN automatisch generieren — 6-stellig numerisch
    Push 1000000
    Call GenerateRandomNumber
    Pop $PIN_CODE
FunctionEnd

; --- Custom Page: Datenordner ---
Function DataDirPage
    !insertmacro MUI_HEADER_TEXT "Datenordner" "Hier landen Datenbank, Archiv, Uploads. Frei waehlbar — NAS, externes Laufwerk, Cloud-Sync."

    nsDialogs::Create 1018
    Pop $0

    ${NSD_CreateLabel} 0 0 100% 24u "Datenordner (Datenbank + Archiv + Uploads):"
    Pop $DATA_DIR_LABEL

    ${NSD_CreateText} 0 30u 80% 14u "$DATA_DIR"
    Pop $DATA_DIR_TEXT

    ${NSD_CreateBrowseButton} 82% 28u 18% 16u "Durchsuchen"
    Pop $DATA_DIR_BUTTON
    ${NSD_OnClick} $DATA_DIR_BUTTON OnBrowseDataDir

    ${NSD_CreateLabel} 0 60u 100% 36u "Tipp: ein Pfad in OneDrive/Dropbox/Nextcloud erzeugt automatisch ein Cloud-Backup deiner Dokumente. Ein NAS-Pfad (Z:\) bindet das Familienarchiv ins NAS-Snapshot-System ein."
    Pop $0

    ${NSD_CreateLabel} 0 110u 100% 12u "Port der Weboberflaeche:"
    Pop $0
    ${NSD_CreateNumber} 0 124u 60u 14u "$SERVER_PORT"
    Pop $0
    ${NSD_OnChange} $0 OnPortChange

    nsDialogs::Show
FunctionEnd

Function OnBrowseDataDir
    Var /GLOBAL TMP_DIR
    nsDialogs::SelectFolderDialog "Datenordner waehlen" "$DATA_DIR"
    Pop $TMP_DIR
    ${If} $TMP_DIR != "error"
        StrCpy $DATA_DIR "$TMP_DIR"
        ${NSD_SetText} $DATA_DIR_TEXT "$DATA_DIR"
    ${EndIf}
FunctionEnd

Function OnPortChange
    Pop $0
    ${NSD_GetText} $0 $1
    StrCpy $SERVER_PORT $1
FunctionEnd

Function DataDirPageLeave
    ${NSD_GetText} $DATA_DIR_TEXT $DATA_DIR
    ${If} $DATA_DIR == ""
        MessageBox MB_OK|MB_ICONEXCLAMATION "Bitte einen Datenordner waehlen."
        Abort
    ${EndIf}
    ; Pfad anlegen
    CreateDirectory "$DATA_DIR\data"
    CreateDirectory "$DATA_DIR\logs"
    StrCpy $CONFIG_PATH "$DATA_DIR\config.toml"
FunctionEnd

; --- Install Section ---
Section "Zettelwirtschaft" SecMain
    SectionIn RO
    SetOutPath "$INSTDIR"

    ; Bundle entpacken — Inhalt von %DIST% kommt 1:1 in $INSTDIR
    File /r "${DIST}\backend"
    File /r "${DIST}\frontend"
    File /r "${DIST}\bin"
    File "${DIST}\nssm.exe"
    File "${DIST}\service-install.bat"
    File "${DIST}\service-uninstall.bat"
    File "${DIST}\config.toml.example"
    File "${DIST}\VERSION"
    File "scripts\Convert-Env-To-Config.ps1"
    File "${DIST}\update-wizard.bat"
    File "${DIST}\update-wizard.ps1"
    File "${DIST}\backup.bat"
    File "${DIST}\restore-backup.bat"
    File "${DIST}\restore-backup.ps1"

    ; FRONTEND_DIST_PATH fuer config.toml
    StrCpy $FRONTEND_DIST_PATH "$INSTDIR\frontend"

    ; config.toml schreiben (mit / statt \\ — TOML/Python tolerant)
    Push "$DATA_DIR"
    Call BackslashToForwardSlash
    Pop $R0
    Push "$FRONTEND_DIST_PATH"
    Call BackslashToForwardSlash
    Pop $R1

    DetailPrint "Schreibe config.toml..."
    FileOpen $0 "$CONFIG_PATH" w
    FileWrite $0 "# Zettelwirtschaft Native ${VERSION} - generiert vom Setup$\r$\n"
    FileWrite $0 "SERVER_HOST = $\"0.0.0.0$\"$\r$\n"
    FileWrite $0 "SERVER_PORT = $SERVER_PORT$\r$\n"
    FileWrite $0 "DATABASE_URL = $\"sqlite+aiosqlite:///$R0/data/zettelwirtschaft.db$\"$\r$\n"
    FileWrite $0 "UPLOAD_DIR = $\"$R0/data/uploads$\"$\r$\n"
    FileWrite $0 "ARCHIVE_DIR = $\"$R0/data/archive$\"$\r$\n"
    FileWrite $0 "THUMBNAIL_DIR = $\"$R0/data/thumbnails$\"$\r$\n"
    FileWrite $0 "WATCH_DIR = $\"$R0/data/watch$\"$\r$\n"
    FileWrite $0 "EXPORT_DIR = $\"$\"$\r$\n"
    FileWrite $0 "FRONTEND_DIST_DIR = $\"$R1$\"$\r$\n"
    FileWrite $0 "OLLAMA_BASE_URL = $\"http://localhost:11434$\"$\r$\n"
    FileWrite $0 "OLLAMA_MODEL = $\"qwen2.5:7b-instruct$\"$\r$\n"
    FileWrite $0 "PIN_ENABLED = true$\r$\n"
    FileWrite $0 "PIN_CODE = $\"$PIN_CODE$\"$\r$\n"
    FileWrite $0 "CHROMADB_MODE = $\"embedded$\"$\r$\n"
    FileWrite $0 "CHROMADB_PATH = $\"$R0/data/chromadb$\"$\r$\n"
    FileWrite $0 "EMBEDDING_MODEL = $\"bge-m3$\"$\r$\n"
    FileWrite $0 "LOG_LEVEL = $\"INFO$\"$\r$\n"
    ; Fernet-Key generieren via PowerShell (kryptografisch sicher)
    nsExec::ExecToStack 'powershell -NoProfile -Command "$bytes=New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes); [Convert]::ToBase64String($bytes).Replace(''+'',''-'').Replace(''/'',''_'')"'
    Pop $R2  ; exit code
    Pop $R3  ; output (Key)
    ; Newline trimmen
    Push $R3
    Call TrimNewlines
    Pop $R3
    FileWrite $0 "EMAIL_ENCRYPTION_KEY = $\"$R3$\"$\r$\n"
    FileClose $0

    ; Firewall-Rule
    DetailPrint "Firewall-Regel..."
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "New-NetFirewallRule -DisplayName Zettelwirtschaft -Direction Inbound -Protocol TCP -LocalPort $SERVER_PORT -Action Allow -Profile Private -ErrorAction SilentlyContinue"'

    ; Service installieren
    DetailPrint "Service registrieren..."
    nsExec::ExecToLog '"$INSTDIR\service-install.bat" "$INSTDIR" "$CONFIG_PATH" "$DATA_DIR\logs"'

    ; Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Registry
    WriteRegStr HKLM "Software\Zettelwirtschaft" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\Zettelwirtschaft" "DataDir" "$DATA_DIR"
    WriteRegStr HKLM "Software\Zettelwirtschaft" "ConfigPath" "$CONFIG_PATH"
    WriteRegStr HKLM "Software\Zettelwirtschaft" "Version" "${VERSION}"

    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "DisplayName" "Zettelwirtschaft ${VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "DisplayVersion" "${VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "Publisher" "Zettelwirtschaft Open Source"
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "NoRepair" 1

    ; Startmenue
    CreateDirectory "$SMPROGRAMS\Zettelwirtschaft"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Zettelwirtschaft oeffnen.lnk" \
        "http://localhost:$SERVER_PORT" "" "$SYSDIR\shell32.dll" 13
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Logs ansehen.lnk" \
        "$DATA_DIR\logs"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk" \
        "$INSTDIR\Uninstall.exe"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Update.lnk" \
        "$INSTDIR\update-wizard.bat" "" "$SYSDIR\shell32.dll" 46
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Backup jetzt.lnk" \
        "$INSTDIR\backup.bat" "" "$SYSDIR\shell32.dll" 45
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Backup wiederherstellen.lnk" \
        "$INSTDIR\restore-backup.bat" "" "$SYSDIR\shell32.dll" 238
SectionEnd

; --- Browser oeffnen ---
Function OpenBrowser
    ExecShell "open" "http://localhost:$SERVER_PORT"
FunctionEnd

Function OpenLogs
    ExecShell "open" "$DATA_DIR\logs"
FunctionEnd

; --- Helper: Random-Number ---
Function GenerateRandomNumber
    Exch $0  ; max
    Push $1
    Push $2
    ; PowerShell-Roundtrip (Get-Random 0..999999)
    nsExec::ExecToStack 'powershell -NoProfile -Command "Get-Random -Minimum 0 -Maximum $0 | ForEach-Object { $_.ToString().PadLeft(6,$\'0$\') }"'
    Pop $1  ; exit code
    Pop $2  ; output
    Push $2
    Call TrimNewlines
    Pop $2
    Pop $1
    Exch $2
FunctionEnd

Function TrimNewlines
    Exch $R0
    Push $R1
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
        Goto done
    done:
        Pop $R1
        Exch $R0
FunctionEnd

Function BackslashToForwardSlash
    Exch $R0
    Push $R1
    Push $R2
    Push $R3
    StrCpy $R3 ""
    StrCpy $R1 0
    loop:
        StrCpy $R2 $R0 1 $R1
        StrCmp $R2 "" done
        StrCmp $R2 "\" 0 +3
            StrCpy $R3 "$R3/"
            Goto next
        StrCpy $R3 "$R3$R2"
        next:
        IntOp $R1 $R1 + 1
        Goto loop
    done:
        StrCpy $R0 $R3
        Pop $R3
        Pop $R2
        Pop $R1
        Exch $R0
FunctionEnd

; --- Uninstall ---
Section "Uninstall"
    ; Sicherungs-Backup vor dem Entfernen (best-effort, Dienst laeuft ggf. noch).
    ; WICHTIG: --out-dir nach $DOCUMENTS, NICHT in den Datenordner — sonst loescht
    ; das RMDir /r unten (bei "Daten loeschen? Ja") genau dieses Backup wieder mit.
    ReadRegStr $1 HKLM "Software\Zettelwirtschaft" "ConfigPath"
    ${If} ${FileExists} "$INSTDIR\backend\zettelwirtschaft-backend.exe"
    ${AndIf} $1 != ""
        DetailPrint "Erstelle Sicherungs-Backup in $DOCUMENTS\Zettelwirtschaft-Backups..."
        nsExec::ExecToLog '"$INSTDIR\backend\zettelwirtschaft-backend.exe" --config "$1" --backup --out-dir "$DOCUMENTS\Zettelwirtschaft-Backups"'
    ${EndIf}

    ; Service entfernen
    nsExec::ExecToLog '"$INSTDIR\service-uninstall.bat" "$INSTDIR"'

    ; Files
    RMDir /r "$INSTDIR\backend"
    RMDir /r "$INSTDIR\frontend"
    RMDir /r "$INSTDIR\bin"
    Delete "$INSTDIR\nssm.exe"
    Delete "$INSTDIR\service-install.bat"
    Delete "$INSTDIR\service-uninstall.bat"
    Delete "$INSTDIR\config.toml.example"
    Delete "$INSTDIR\VERSION"
    Delete "$INSTDIR\Convert-Env-To-Config.ps1"
    Delete "$INSTDIR\update-wizard.bat"
    Delete "$INSTDIR\update-wizard.ps1"
    Delete "$INSTDIR\backup.bat"
    Delete "$INSTDIR\restore-backup.bat"
    Delete "$INSTDIR\restore-backup.ps1"
    Delete "$INSTDIR\Uninstall.exe"

    ; Startmenue
    Delete "$SMPROGRAMS\Zettelwirtschaft\Zettelwirtschaft oeffnen.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Logs ansehen.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Update.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Backup jetzt.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Backup wiederherstellen.lnk"
    RMDir "$SMPROGRAMS\Zettelwirtschaft"

    ; Daten: User fragen
    ReadRegStr $0 HKLM "Software\Zettelwirtschaft" "DataDir"
    ${If} $0 != ""
    ${AndIf} ${FileExists} "$0\data\*.*"
        MessageBox MB_YESNO|MB_ICONQUESTION \
            "Sollen die Daten (Dokumente, Datenbank, Backups) in$\r$\n$0$\r$\nebenfalls geloescht werden?$\r$\n$\r$\nEin Sicherungs-Backup der Datenbank liegt in$\r$\n$DOCUMENTS\Zettelwirtschaft-Backups.$\r$\n$\r$\nDies kann NICHT rueckgaengig gemacht werden." \
            /SD IDNO IDNO keep_data
        RMDir /r "$0"
        keep_data:
    ${EndIf}

    ; INSTDIR (sollte leer sein)
    RMDir "$INSTDIR"

    ; Registry
    DeleteRegKey HKLM "Software\Zettelwirtschaft"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft"
SectionEnd
