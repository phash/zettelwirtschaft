; Zettelwirtschaft Native-Windows-Installer
;
; Build:  makensis /WX /DVERSION=1.3.0 /DDIST=dist\native setup-native.nsi
; Output: Zettelwirtschaft-1.3.0-Native-Setup.exe
;
; Im Gegensatz zum alten setup.nsi (Docker-basiert) braucht dieser Setup
; weder Docker Desktop noch eine externe install-gui.ps1. Backend laeuft als
; Windows-Service, ChromaDB embedded, Tesseract+poppler gebundled.
;
; Datei ist UTF-8 MIT BOM gespeichert (NSIS liest sie dann als UTF-8 ->
; Umlaute in den UI-Texten). Nicht ohne BOM speichern.
;
; Drei Modi:
;   - Erstinstallation: Datenordner waehlen, PIN + Fernet-Key erzeugen,
;     config.toml schreiben (New-NativeConfig.ps1), Dienst registrieren.
;   - Upgrade (bestehende Installation in der Registry gefunden): Pfade aus der
;     Registry, Sicherungs-Backup, Dienst stoppen, Programmdateien ersetzen.
;     config.toml (PIN, EMAIL_ENCRYPTION_KEY, eigene Einstellungen) bleibt
;     unangetastet.
;   - Uebernahme (ADOPT, ab 1.6.1): Dienst ZettelwirtschaftBackend existiert,
;     aber kein Registry-Eintrag (manuell per service-install.bat eingerichtet,
;     z.B. nach der Docker->Native-Migration). legacy-install.ps1 liest die
;     NSSM-Einstellungen des Dienstes (Programm-, Config-, Daten-, Log-Pfad).
;     Ablauf: Dienst stoppen -> Backup (DB + config.toml als ZIP, ohne die alte
;     Exe) -> neue Programmdateien in einen NEUEN Programmordner -> Dienst neu
;     registrieren (service-install.bat ersetzt den alten) -> erst dann in der
;     config.toml nur FRONTEND_DIST_DIR umstellen -> Registry -> Health-Check ->
;     alte Programmdateien optional loeschen (nur bekannte Dateien, kein
;     RMDir /r). Bricht das Setup vor der Neuregistrierung ab, stellt
;     RestoreLegacyService den alten Dienst wieder her (config.toml unveraendert).
;
; Registry: 64-Bit-Ansicht (SetRegView 64). Aeltere Setups schrieben als
; 32-Bit-Prozess nach WOW6432Node, wo backup.bat/update-wizard.ps1 (64-Bit)
; nichts fanden; diese Altwerte werden beim Upgrade gelesen und migriert.

Unicode True

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "TextFunc.nsh"
; GenerateSecret / TrimNewlines / POWERSHELL (eigene Datei, damit
; scripts/test-scripts.ps1 sie in einem Test-Installer ausfuehren kann)
!include "setup-native-helpers.nsh"

!ifndef VERSION
    !define VERSION "dev"
!endif
!ifndef DIST
    !define DIST "dist\native"
!endif

!define REG_KEY "Software\Zettelwirtschaft"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft"
!define SERVICE_NAME "ZettelwirtschaftBackend"
!define OLLAMA_DOWNLOAD_URL "https://ollama.com/download"
; Mindestversion fuer die Default-Modelle (gemma4, qwen3-embedding); gleicher
; Wert wie OLLAMA_MIN_VERSION in backend/app/services/ollama_client.py und
; scripts/model-tiers.ps1 (test_docs_consistency.py prueft das).
!define OLLAMA_MIN_VERSION "0.30.3"
!define OLLAMA_TESTED_VERSION "0.40.5"

; --- General ---
Name "Zettelwirtschaft ${VERSION}"
!ifdef OUTFILE
    OutFile "${OUTFILE}"
!else
    OutFile "Zettelwirtschaft-${VERSION}-Native-Setup.exe"
!endif
InstallDir "$PROGRAMFILES64\Zettelwirtschaft"
RequestExecutionLevel admin
BrandingText "Zettelwirtschaft ${VERSION} Native"

; --- MUI ---
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Zettelwirtschaft ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Willkommen.$\r$\n$\r$\nDieses Setup installiert Zettelwirtschaft als Windows-Dienst. Docker wird nicht benötigt.$\r$\n$\r$\nFür die KI-Analyse braucht Zettelwirtschaft zusätzlich Ollama (lokaler KI-Server) ab Version ${OLLAMA_MIN_VERSION} (getestet mit ${OLLAMA_TESTED_VERSION}). Ollama wird NICHT mitinstalliert, sondern separat von ${OLLAMA_DOWNLOAD_URL} heruntergeladen; eine ältere Ollama-Version bitte vorher aktualisieren. Die KI-Modelle (mehrere GB) lädt Zettelwirtschaft nach dem Start selbst; bis dahin landen neue Dokumente zur Prüfung.$\r$\n$\r$\nKlicke 'Weiter', um zu beginnen."

!define MUI_DIRECTORYPAGE_TEXT_TOP "Programmordner wählen. Programmdateien werden hier abgelegt; die Nutzerdaten kommen in einen separaten Ordner (im nächsten Schritt)."

; Variablen
Var DATA_DIR
Var DATA_DIR_LABEL
Var DATA_DIR_TEXT
Var DATA_DIR_BUTTON
Var PORT_TEXT
Var PIN_CODE
Var SERVER_PORT
Var CONFIG_PATH
Var UPGRADE          ; "1" = bestehende Installation wird aktualisiert
Var OLD_VERSION
Var LEGACY_REGVIEW   ; "1" = Altwerte lagen in der 32-Bit-Registry-Ansicht
Var FINISH_PIN_TEXT
Var LOCAL_ONLY_CHECK
Var LOCAL_ONLY       ; ${BST_CHECKED} = nur 127.0.0.1 (INFRA-07)
Var SERVER_HOST
Var LOG_DIR          ; Log-Ordner des Dienstes (Standard: $DATA_DIR\logs)
Var FINISH_NOTE      ; Zusatztext der Abschlussseite (Uebernahme)
; Uebernahme einer manuell eingerichteten Installation (siehe Kopfkommentar)
Var ADOPT            ; "1" = Uebernahme
Var OLD_DIR          ; bisheriger Programmordner
Var OLD_NSSM         ; nssm.exe des bisherigen Dienstes
Var OLD_APP          ; bisherige Backend-Exe
Var OLD_APPDIR       ; bisheriges AppDirectory
Var OLD_STDOUT
Var OLD_STDERR
Var OLD_OLLAMA_DEP   ; "1" = Dienst hing von Ollama ab
Var OLD_FRONTEND_KEY ; "1" = config.toml hat FRONTEND_DIST_DIR
Var OLD_WAS_RUNNING  ; "1" = alter Dienst lief vor dem Setup (Rollback startet ihn wieder)
Var OLD_DATA_INSIDE  ; "1" = Daten/Config/Logs liegen im alten Programmordner
Var OLD_DIR_DELETABLE
Var DB_PATH
Var BACKUP_ZIP
Var ADOPT_STOPPED    ; "1" = alter Dienst wurde angehalten (-> Rollback bei Abbruch)
Var SERVICE_EXIT     ; Exit-Code von service-install.bat

; --- Pages ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipPageOnUpgrade
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE InstDirPageLeave
!insertmacro MUI_PAGE_DIRECTORY

; Custom Page: Datenordner wählen (bei Upgrade übersprungen)
Page custom DataDirPage DataDirPageLeave

!insertmacro MUI_PAGE_INSTFILES

; FINISH-Page: Browser öffnen
!define MUI_FINISHPAGE_TITLE "Installation abgeschlossen"
!define MUI_FINISHPAGE_TEXT "Zettelwirtschaft läuft als Hintergrunddienst.$\r$\n$\r$\nDie Weboberfläche ist erreichbar unter:$\r$\nhttp://localhost:$SERVER_PORT$\r$\n$\r$\n$FINISH_PIN_TEXT$FINISH_NOTE$\r$\n$\r$\nOhne Ollama ist keine KI-Analyse möglich."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Browser jetzt öffnen"
!define MUI_FINISHPAGE_RUN_FUNCTION "OpenBrowser"
!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Logs im Datenordner ansehen"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION "OpenLogs"
!define MUI_FINISHPAGE_LINK "Ollama herunterladen (für die KI-Analyse nötig)"
!define MUI_FINISHPAGE_LINK_LOCATION "${OLLAMA_DOWNLOAD_URL}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "German"

; --- Init ---
Function .onInit
    SetRegView 64
    StrCpy $DATA_DIR "$DOCUMENTS\Zettelwirtschaft"
    StrCpy $SERVER_PORT "8080"
    StrCpy $UPGRADE "0"
    StrCpy $LEGACY_REGVIEW "0"
    StrCpy $LOCAL_ONLY "0"
    StrCpy $ADOPT "0"
    StrCpy $ADOPT_STOPPED "0"
    StrCpy $OLD_WAS_RUNNING "1"

    ; GenerateSecret braucht new-secret.ps1 im PLUGINSDIR — immer entpacken:
    ; auch ein Upgrade ohne config.toml erzeugt in der Install-Section einen PIN.
    ; legacy-install.ps1 (+ secure-acl.ps1 fuer die Rechte des Backup-ZIPs)
    ; braucht schon DetectExistingInstall.
    InitPluginsDir
    File "/oname=$PLUGINSDIR\new-secret.ps1" "${DIST}\new-secret.ps1"
    File "/oname=$PLUGINSDIR\legacy-install.ps1" "${DIST}\legacy-install.ps1"
    File "/oname=$PLUGINSDIR\secure-acl.ps1" "${DIST}\secure-acl.ps1"

    Call DetectExistingInstall

    ${If} $UPGRADE == "1"
    ${OrIf} $ADOPT == "1"
        StrCpy $FINISH_PIN_TEXT "PIN: unverändert (steht in $CONFIG_PATH)."
    ${Else}
        ; PIN automatisch generieren — 6-stellig numerisch, kryptografischer Zufall
        Push "pin"
        Call GenerateSecret
        Pop $PIN_CODE
        ${If} $PIN_CODE == ""
            MessageBox MB_OK|MB_ICONSTOP "Der Zugangs-PIN konnte nicht erzeugt werden (PowerShell nicht verfügbar?).$\r$\nDas Setup wird beendet." /SD IDOK
            Abort
        ${EndIf}
        StrCpy $FINISH_PIN_TEXT "PIN: $PIN_CODE (bitte notieren!)"
    ${EndIf}
FunctionEnd

; Bestehende Installation erkennen (64-Bit-Ansicht, Fallback: alte 32-Bit-Werte).
Function DetectExistingInstall
    ReadRegStr $R0 HKLM "${REG_KEY}" "InstallDir"
    ${If} $R0 == ""
        SetRegView 32
        ReadRegStr $R0 HKLM "${REG_KEY}" "InstallDir"
        ${If} $R0 != ""
            StrCpy $LEGACY_REGVIEW "1"
        ${EndIf}
    ${EndIf}
    ReadRegStr $R1 HKLM "${REG_KEY}" "ConfigPath"
    ReadRegStr $R2 HKLM "${REG_KEY}" "DataDir"
    ReadRegStr $R3 HKLM "${REG_KEY}" "Version"
    SetRegView 64

    ${If} $R0 == ""
    ${OrIfNot} ${FileExists} "$R0\backend\zettelwirtschaft-backend.exe"
        ; Kein Setup-Eintrag. Ein manuell registrierter Dienst (z.B. nach einer
        ; Docker-Migration per service-install.bat) wuerde von einer
        ; Erstinstallation ueberschrieben -> uebernehmen (oder abbrechen).
        nsExec::ExecToStack 'sc query ${SERVICE_NAME}'
        Pop $R4
        Pop $R5
        ${If} $R4 == "0"
            Call DetectLegacyInstall
        ${EndIf}
        Return
    ${EndIf}

    MessageBox MB_YESNO|MB_ICONQUESTION "Zettelwirtschaft $R3 ist bereits installiert:$\r$\n$R0$\r$\n$\r$\nJetzt auf Version ${VERSION} aktualisieren?$\r$\n$\r$\nDatenbank, Dokumente, Einstellungen und PIN bleiben erhalten. Vorher wird ein Sicherungs-Backup erstellt." /SD IDYES IDYES upgrade
    Abort
    upgrade:
    StrCpy $UPGRADE "1"
    StrCpy $INSTDIR $R0
    StrCpy $CONFIG_PATH $R1
    StrCpy $DATA_DIR $R2
    StrCpy $OLD_VERSION $R3
    ${If} $CONFIG_PATH == ""
        StrCpy $CONFIG_PATH "$DATA_DIR\config.toml"
    ${EndIf}
    Call ReadConfigPort
FunctionEnd

; Manuell eingerichteten Dienst erkennen (legacy-install.ps1 -Action Detect) und
; die Uebernahme anbieten. Nicht uebernehmbar (kein NSSM-Dienst, keine config.toml
; ...) -> Abbruch mit Anleitung, denn eine Erstinstallation wuerde den Dienst
; ueberschreiben.
Function DetectLegacyInstall
    StrCpy $R6 "$PLUGINSDIR\legacy.ini"
    Delete "$R6"
    Push '-Action Detect -ServiceName ${SERVICE_NAME} -ResultFile "$R6"'
    Call LegacyHelper
    Pop $R7
    ReadINIStr $R8 "$R6" "result" "Status"
    ${If} $R7 != "0"
    ${OrIf} $R8 != "adoptable"
        ReadINIStr $R9 "$R6" "result" "Reason"
        StrCpy $R5 "$\r$\n$\r$\nSo geht es weiter: den Dienst als Administrator entfernen (sc stop ${SERVICE_NAME}, danach sc delete ${SERVICE_NAME}) und dieses Setup erneut starten. Als Datenordner den Ordner mit der bisherigen config.toml wählen — Datenbank, Dokumente, Einstellungen und PIN bleiben dann erhalten."
        ${If} $R9 == "not-nssm"
            StrCpy $R9 "Der Dienst wird nicht über NSSM ausgeführt."
        ${ElseIf} $R9 == "no-application"
            StrCpy $R9 "Beim Dienst ist kein Programm eingetragen."
        ${ElseIf} $R9 == "no-config-env"
            StrCpy $R9 "Beim Dienst ist keine Konfigurationsdatei (ZETTELWIRTSCHAFT_CONFIG) eingetragen."
        ${ElseIf} $R9 == "config-missing"
            ReadINIStr $R8 "$R6" "result" "ConfigPath"
            StrCpy $R9 "Die Konfigurationsdatei des Dienstes fehlt: $R8"
        ${ElseIf} $R9 == "db-url"
            StrCpy $R9 "DATABASE_URL in der config.toml ist keine SQLite-Datei."
        ${ElseIf} $R9 == "relative-paths"
            ReadINIStr $R8 "$R6" "result" "ConfigPath"
            StrCpy $R9 "Die config.toml ($R8) enthält relative oder fehlende Datenpfade (DATABASE_URL, UPLOAD_DIR, ARCHIVE_DIR, THUMBNAIL_DIR, WATCH_DIR). Nach der Übernahme zeigten sie in den neuen Programmordner."
            StrCpy $R5 "$\r$\n$\r$\nSo geht es weiter: in der config.toml absolute Pfade eintragen (z. B. C:/Zettelwirtschaft/data/archive) und dieses Setup erneut starten."
        ${Else}
            StrCpy $R9 "Die Einstellungen des Dienstes konnten nicht gelesen werden."
        ${EndIf}
        MessageBox MB_OK|MB_ICONSTOP "Der Dienst ${SERVICE_NAME} ist bereits eingerichtet, aber nicht über dieses Setup installiert worden. Das Setup kann ihn nicht automatisch übernehmen:$\r$\n$R9$R5" /SD IDOK
        Abort
    ${EndIf}

    ReadINIStr $OLD_DIR "$R6" "result" "OldInstallDir"
    ReadINIStr $OLD_NSSM "$R6" "result" "Nssm"
    ReadINIStr $OLD_APP "$R6" "result" "Application"
    ReadINIStr $OLD_APPDIR "$R6" "result" "AppDirectory"
    ReadINIStr $OLD_STDOUT "$R6" "result" "AppStdout"
    ReadINIStr $OLD_STDERR "$R6" "result" "AppStderr"
    ReadINIStr $OLD_OLLAMA_DEP "$R6" "result" "OllamaDependency"
    ReadINIStr $OLD_FRONTEND_KEY "$R6" "result" "FrontendKey"
    ReadINIStr $OLD_DATA_INSIDE "$R6" "result" "DataInsideOldDir"
    ReadINIStr $OLD_DIR_DELETABLE "$R6" "result" "OldDirDeletable"
    ReadINIStr $OLD_VERSION "$R6" "result" "OldVersion"
    ReadINIStr $CONFIG_PATH "$R6" "result" "ConfigPath"
    ReadINIStr $DATA_DIR "$R6" "result" "DataDir"
    ReadINIStr $DB_PATH "$R6" "result" "DbPath"
    ReadINIStr $LOG_DIR "$R6" "result" "LogDir"

    ; Neuer Programmordner: nie der alte (liegt oft im Benutzerprofil, INFRA-03).
    Push "$INSTDIR"
    Push "$OLD_DIR"
    Call PathsOverlap
    Pop $R7
    ${If} $R7 == "1"
        StrCpy $INSTDIR "$PROGRAMFILES64\Zettelwirtschaft-${VERSION}"
    ${EndIf}

    MessageBox MB_YESNO|MB_ICONQUESTION "Eine manuell eingerichtete Zettelwirtschaft-Installation (Version $OLD_VERSION) wurde gefunden:$\r$\n$OLD_DIR$\r$\nDatenordner: $DATA_DIR$\r$\n$\r$\nJetzt auf Version ${VERSION} aktualisieren?$\r$\n$\r$\nDatenbank, Dokumente, Einstellungen und PIN bleiben erhalten; vorher wird eine Sicherung erstellt. Die Programmdateien werden nach $INSTDIR verlegt (im nächsten Schritt änderbar)." /SD IDNO IDYES adopt
    Abort
    adopt:
    StrCpy $ADOPT "1"
    Call ReadConfigPort
FunctionEnd

; SERVER_PORT aus einer vorhandenen config.toml uebernehmen (Upgrade bzw.
; Erstinstallation in einen Datenordner mit config.toml): Firewall-Regel,
; Verknuepfungen und Abschlussseite muessen den tatsaechlichen Port nennen.
Function ReadConfigPort
    Push $R4
    ${If} ${FileExists} "$CONFIG_PATH"
        ClearErrors
        ${ConfigRead} "$CONFIG_PATH" "SERVER_PORT = " $R4
        ${IfNot} ${Errors}
            StrCpy $SERVER_PORT $R4
        ${EndIf}
    ${EndIf}
    Pop $R4
FunctionEnd

Function SkipPageOnUpgrade
    ${If} $UPGRADE == "1"
        Abort
    ${EndIf}
FunctionEnd

; INFRA-03: Ein frei gewaehlter Programmordner ausserhalb von Program Files kann
; vorab von einem anderen lokalen Konto angelegt und mit eigenen Dateien (DLLs,
; Exe) bestueckt worden sein - die liefen spaeter als SYSTEM. Deshalb dort nur
; in einen neuen oder leeren Ordner installieren. Die Rechte des Ordners setzt
; die Install-Section (secure-install-dir.ps1) vor dem Entpacken.
Function InstDirPageLeave
    Push $R1
    Push $R2
    ; Uebernahme: nicht in den alten Programmordner (liegt meist im Benutzerprofil,
    ; die alten Dateien wuerden vermischt) und nicht in Daten-/Log-Ordner
    ; (secure-install-dir.ps1 setzt die Rechte des Programmordners rekursiv zurueck).
    ${If} $ADOPT == "1"
        Push "$INSTDIR"
        Push "$OLD_DIR"
        Call PathsOverlap
        Pop $R1
        ${If} $R1 == "1"
            MessageBox MB_OK|MB_ICONEXCLAMATION "Der Ordner $INSTDIR ist der Ordner der bisherigen Installation (oder liegt darin bzw. enthält ihn):$\r$\n$OLD_DIR$\r$\n$\r$\nBitte einen neuen Programmordner wählen, z. B. $PROGRAMFILES64\Zettelwirtschaft." /SD IDOK
            Pop $R2
            Pop $R1
            Abort
        ${EndIf}
        Push "$INSTDIR"
        Push "$DATA_DIR"
        Call PathsOverlap
        Pop $R1
        Push "$INSTDIR"
        Push "$LOG_DIR"
        Call PathsOverlap
        Pop $R2
        ${If} $R2 == "1"
            StrCpy $R1 "1"
        ${EndIf}
        ; config.toml und Datenbank koennen ausserhalb des Datenordners liegen.
        Push "$INSTDIR"
        Push "$CONFIG_PATH"
        Call PathsOverlap
        Pop $R2
        ${If} $R2 == "1"
            StrCpy $R1 "1"
        ${EndIf}
        Push "$INSTDIR"
        Push "$DB_PATH"
        Call PathsOverlap
        Pop $R2
        ${If} $R1 == "1"
        ${OrIf} $R2 == "1"
            MessageBox MB_OK|MB_ICONEXCLAMATION "Der Ordner $INSTDIR überschneidet sich mit dem Daten- oder Log-Ordner (bzw. config.toml oder Datenbank) der bisherigen Installation:$\r$\n$DATA_DIR$\r$\n$CONFIG_PATH$\r$\n$\r$\nBitte einen eigenen Programmordner wählen, z. B. $PROGRAMFILES64\Zettelwirtschaft." /SD IDOK
            Pop $R2
            Pop $R1
            Abort
        ${EndIf}
    ${EndIf}
    StrLen $R1 "$PROGRAMFILES64\"
    StrCpy $R2 "$INSTDIR\" $R1
    ${If} $R2 != "$PROGRAMFILES64\"
        ${DirState} "$INSTDIR" $R1
        ${If} $R1 == 1
            MessageBox MB_OK|MB_ICONEXCLAMATION "Der Ordner $INSTDIR ist nicht leer.$\r$\n$\r$\nAußerhalb von $PROGRAMFILES64 installiert das Setup nur in einen neuen oder leeren Ordner (Schutz vor untergeschobenen Programmdateien). Bitte einen anderen Ordner wählen." /SD IDOK
            Pop $R2
            Pop $R1
            Abort
        ${EndIf}
    ${EndIf}
    Pop $R2
    Pop $R1
FunctionEnd

; --- Custom Page: Datenordner ---
Function DataDirPage
    ${If} $UPGRADE == "1"
    ${OrIf} $ADOPT == "1"
        Abort
    ${EndIf}
    !insertmacro MUI_HEADER_TEXT "Datenordner" "Hier landen Datenbank, Archiv, Uploads. Frei wählbar — NAS, externes Laufwerk, Cloud-Sync."

    nsDialogs::Create 1018
    Pop $0

    ${NSD_CreateLabel} 0 0 100% 24u "Datenordner (Datenbank + Archiv + Uploads):"
    Pop $DATA_DIR_LABEL

    ${NSD_CreateText} 0 30u 80% 14u "$DATA_DIR"
    Pop $DATA_DIR_TEXT

    ${NSD_CreateBrowseButton} 82% 28u 18% 16u "Durchsuchen"
    Pop $DATA_DIR_BUTTON
    ${NSD_OnClick} $DATA_DIR_BUTTON OnBrowseDataDir

    ${NSD_CreateLabel} 0 52u 100% 32u "Tipp: ein Pfad in OneDrive/Dropbox/Nextcloud erzeugt automatisch ein Cloud-Backup deiner Dokumente. Ein NAS-Pfad (Z:\) bindet das Familienarchiv ins NAS-Snapshot-System ein."
    Pop $0

    ${NSD_CreateLabel} 0 90u 100% 12u "Port der Weboberfläche:"
    Pop $0
    ${NSD_CreateNumber} 0 102u 60u 14u "$SERVER_PORT"
    Pop $PORT_TEXT

    ; INFRA-07: ohne HTTPS laufen PIN, Sitzungs-Cookie und Dokumente im Heimnetz
    ; unverschluesselt. Wer kein Smartphone/Tablet nutzt, bindet nur an 127.0.0.1.
    ${NSD_CreateCheckbox} 0 122u 100% 14u "Nur von diesem PC erreichbar (kein Zugriff von Smartphone/anderen Geräten)"
    Pop $LOCAL_ONLY_CHECK
    ${NSD_SetState} $LOCAL_ONLY_CHECK $LOCAL_ONLY

    nsDialogs::Show
FunctionEnd

Function OnBrowseDataDir
    Pop $0  ; Button-Handle
    nsDialogs::SelectFolderDialog "Datenordner wählen" "$DATA_DIR"
    Pop $1
    ${If} $1 != "error"
        StrCpy $DATA_DIR "$1"
        ${NSD_SetText} $DATA_DIR_TEXT "$DATA_DIR"
    ${EndIf}
FunctionEnd

Function DataDirPageLeave
    ${NSD_GetText} $DATA_DIR_TEXT $DATA_DIR
    ${NSD_GetText} $PORT_TEXT $SERVER_PORT
    ${NSD_GetState} $LOCAL_ONLY_CHECK $LOCAL_ONLY
    ; Abschliessenden Backslash entfernen ("D:\" -> "D:"): sonst escaped er das
    ; schliessende Anfuehrungszeichen der Kommandozeile fuer New-NativeConfig.ps1.
    StrCpy $0 $DATA_DIR 1 -1
    ${If} $0 == "\"
        StrCpy $DATA_DIR $DATA_DIR -1
    ${EndIf}
    ${If} $DATA_DIR == ""
        MessageBox MB_OK|MB_ICONEXCLAMATION "Bitte einen Datenordner wählen."
        Abort
    ${EndIf}
    ${If} $SERVER_PORT == ""
    ${OrIf} $SERVER_PORT < 1
    ${OrIf} $SERVER_PORT > 65535
        MessageBox MB_OK|MB_ICONEXCLAMATION "Bitte einen Port zwischen 1 und 65535 angeben."
        Abort
    ${EndIf}
    ; Pfad anlegen
    CreateDirectory "$DATA_DIR\data"
    CreateDirectory "$DATA_DIR\logs"
    StrCpy $CONFIG_PATH "$DATA_DIR\config.toml"
FunctionEnd

; --- Upgrade: Backup + Dienst stoppen + alte Programmdateien entfernen ---
Function PrepareUpgrade
    DetailPrint "Erstelle Sicherungs-Backup (Version $OLD_VERSION)..."
    nsExec::ExecToLog '"$INSTDIR\backend\zettelwirtschaft-backend.exe" --config "$CONFIG_PATH" --backup'
    Pop $R0
    ${If} $R0 != "0"
        MessageBox MB_YESNO|MB_ICONEXCLAMATION "Das Sicherungs-Backup ist fehlgeschlagen (Code $R0).$\r$\n$\r$\nTrotzdem aktualisieren?" /SD IDNO IDYES backup_done
        Abort "Update abgebrochen: kein Sicherungs-Backup."
    ${EndIf}
    backup_done:

    DetailPrint "Stoppe Dienst ${SERVICE_NAME}..."
    ; Exit-Code egal: der Dienst kann bereits gestoppt sein.
    nsExec::ExecToLog 'net stop ${SERVICE_NAME}'
    Pop $R0

    ; Programm-Unterordner gehoeren komplett zur installierten Version: vor dem
    ; Entpacken leeren, damit keine veralteten Dateien (alte .pyd/dist-info,
    ; Frontend-Assets) liegen bleiben. Nutzerdaten liegen nicht darin.
    RMDir /r "$INSTDIR\backend"
    RMDir /r "$INSTDIR\frontend"
    RMDir /r "$INSTDIR\bin"
    ${If} ${FileExists} "$INSTDIR\backend\*.*"
        MessageBox MB_OK|MB_ICONSTOP "Die alten Programmdateien in $INSTDIR\backend konnten nicht entfernt werden (Dienst läuft noch?).$\r$\nBitte den Dienst ${SERVICE_NAME} beenden und das Setup erneut starten." /SD IDOK
        Abort "Update abgebrochen: Programmdateien gesperrt."
    ${EndIf}
FunctionEnd

; --- Uebernahme: Dienst stoppen + Sicherungs-Backup (ohne die alte Exe) ---
; Bis service-install.bat bleibt der alte Dienst registriert. Jeder Abbruch
; stellt ihn wieder her: explizit vor Abort, sonst (z.B. Datei nicht entpackbar)
; .onInstFailed -> RestoreLegacyService.
Function PrepareAdoption
    StrCpy $ADOPT_STOPPED "1"
    DetailPrint "Stoppe Dienst ${SERVICE_NAME} (bisherige Version $OLD_VERSION)..."
    Delete "$PLUGINSDIR\stop.ini"
    Push '-Action StopService -ServiceName ${SERVICE_NAME} -TimeoutSeconds 90 -ResultFile "$PLUGINSDIR\stop.ini"'
    Call LegacyHelper
    Pop $R0
    ; Ein vom Nutzer angehaltener Dienst wird beim Rollback nicht gestartet.
    ReadINIStr $R1 "$PLUGINSDIR\stop.ini" "result" "WasRunning"
    ${If} $R1 == "0"
        StrCpy $OLD_WAS_RUNNING "0"
    ${EndIf}
    ${If} $R0 != "0"
        MessageBox MB_OK|MB_ICONSTOP "Der Dienst ${SERVICE_NAME} ließ sich nicht beenden.$\r$\nEs wurde nichts verändert; die bisherige Installation läuft weiter." /SD IDOK
        Call RestoreLegacyService
        Abort "Update abgebrochen: Dienst läuft noch."
    ${EndIf}

    DetailPrint "Erstelle Sicherungs-Backup (Datenbank + config.toml) in $DOCUMENTS\Zettelwirtschaft-Backups..."
    Delete "$PLUGINSDIR\backup.ini"
    Push '-Action Backup -ConfigPath "$CONFIG_PATH" -DbPath "$DB_PATH" -OutDir "$DOCUMENTS\Zettelwirtschaft-Backups" -Version "${VERSION}" -ResultFile "$PLUGINSDIR\backup.ini"'
    Call LegacyHelper
    Pop $R0
    ReadINIStr $BACKUP_ZIP "$PLUGINSDIR\backup.ini" "result" "Path"
    ${If} $R0 != "0"
    ${OrIf} $BACKUP_ZIP == ""
        StrCpy $BACKUP_ZIP ""
        ReadINIStr $R1 "$PLUGINSDIR\backup.ini" "result" "Reason"
        MessageBox MB_YESNO|MB_ICONEXCLAMATION "Das Sicherungs-Backup ist fehlgeschlagen ($R1).$\r$\nDatenbank: $DB_PATH$\r$\n$\r$\nOhne Sicherung aktualisieren? (Nicht empfohlen.)$\r$\n$\r$\nBei „Nein“ wird das Setup beendet und die bisherige Installation wieder gestartet." /SD IDNO IDYES backup_skipped
        Call RestoreLegacyService
        Abort "Update abgebrochen: kein Sicherungs-Backup."
        backup_skipped:
        DetailPrint "WARNUNG: Aktualisierung ohne Sicherungs-Backup."
    ${Else}
        DetailPrint "Sicherungs-Backup: $BACKUP_ZIP"
    ${EndIf}
FunctionEnd

; Welcher Dienst ist registriert? Stack out: "none" | "old" | "new" | "other".
Function GetServiceState
    Push $R0
    Push $R1
    nsExec::ExecToStack 'sc query ${SERVICE_NAME}'
    Pop $R0
    Pop $R1
    ${If} $R0 != "0"
        StrCpy $R0 "none"
    ${Else}
        Delete "$PLUGINSDIR\state.ini"
        Push '-Action Detect -ServiceName ${SERVICE_NAME} -ResultFile "$PLUGINSDIR\state.ini"'
        Call LegacyHelper
        Pop $R0
        ReadINIStr $R1 "$PLUGINSDIR\state.ini" "result" "Application"
        ${If} $R1 == "$INSTDIR\backend\zettelwirtschaft-backend.exe"
            StrCpy $R0 "new"
        ${ElseIf} $R1 == $OLD_APP
            StrCpy $R0 "old"
        ${Else}
            StrCpy $R0 "other"
        ${EndIf}
    ${EndIf}
    Pop $R1
    Exch $R0
FunctionEnd

; Rollback der Uebernahme (aus .onInstFailed): bisherigen Dienst mit den alten
; Programmdateien wieder in Betrieb nehmen. Daten und config.toml wurden bis
; hierher nicht veraendert (Migrationen laufen erst beim Start des NEUEN
; Dienstes, FRONTEND_DIST_DIR wird erst danach in FinishAdoption umgestellt).
Function RestoreLegacyService
    ; Nur einmal (explizit vor Abort oder aus .onInstFailed).
    StrCpy $ADOPT_STOPPED "0"
    DetailPrint "Stelle die bisherige Installation wieder her..."
    Call GetServiceState
    Pop $R0
    ${If} $R0 == "new"
        Return
    ${EndIf}
    StrCpy $R2 "$OLD_NSSM"
    ${IfNot} ${FileExists} "$R2"
        StrCpy $R2 "$INSTDIR\nssm.exe"
    ${EndIf}
    StrCpy $R3 "0"
    ${If} $R0 == "none"
        ; service-install.bat hat den alten Dienst schon entfernt: mit den alten
        ; NSSM-Einstellungen neu registrieren.
        nsExec::ExecToStack '"$R2" install ${SERVICE_NAME} "$OLD_APP"'
        Pop $R3
        Pop $R4
        ${If} $R3 == "0"
            nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} AppDirectory "$OLD_APPDIR"'
            Pop $R4
            Pop $R4
            nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} AppEnvironmentExtra "ZETTELWIRTSCHAFT_CONFIG=$CONFIG_PATH"'
            Pop $R4
            Pop $R4
            ${If} $OLD_STDOUT != ""
                nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} AppStdout "$OLD_STDOUT"'
                Pop $R4
                Pop $R4
            ${EndIf}
            ${If} $OLD_STDERR != ""
                nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} AppStderr "$OLD_STDERR"'
                Pop $R4
                Pop $R4
            ${EndIf}
            nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} Start SERVICE_AUTO_START'
            Pop $R4
            Pop $R4
            nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} DisplayName Zettelwirtschaft'
            Pop $R4
            Pop $R4
            ${If} $OLD_OLLAMA_DEP == "1"
                nsExec::ExecToStack '"$R2" set ${SERVICE_NAME} DependOnService Ollama'
                Pop $R4
                Pop $R4
            ${EndIf}
        ${EndIf}
    ${ElseIf} $R0 == "other"
        StrCpy $R3 "1"
    ${EndIf}
    ${If} $R3 == "0"
    ${AndIf} $OLD_WAS_RUNNING == "0"
        MessageBox MB_OK|MB_ICONINFORMATION "Die Aktualisierung wurde abgebrochen. Die bisherige Installation (Version $OLD_VERSION) ist wieder eingerichtet:$\r$\n$OLD_DIR$\r$\n$\r$\nDer Dienst war vor dem Setup angehalten und wurde nicht gestartet.$\r$\nDie bereits kopierten neuen Programmdateien in $INSTDIR können gelöscht werden." /SD IDOK
    ${ElseIf} $R3 == "0"
        nsExec::ExecToStack 'net start ${SERVICE_NAME}'
        Pop $R4
        Pop $R5
        ; net start: 0 = gestartet, 2 = lief bereits
        ${If} $R4 == "0"
        ${OrIf} $R4 == "2"
            MessageBox MB_OK|MB_ICONINFORMATION "Die Aktualisierung wurde abgebrochen. Die bisherige Installation (Version $OLD_VERSION) ist wieder eingerichtet und läuft:$\r$\n$OLD_DIR$\r$\n$\r$\nDie bereits kopierten neuen Programmdateien in $INSTDIR können gelöscht werden." /SD IDOK
        ${Else}
            MessageBox MB_OK|MB_ICONEXCLAMATION "Die Aktualisierung wurde abgebrochen. Die bisherige Installation (Version $OLD_VERSION) ist wieder eingerichtet, der Dienst startet aber nicht (Code $R4):$\r$\n$OLD_DIR$\r$\n$\r$\nBitte den Rechner neu starten oder als Administrator „net start ${SERVICE_NAME}“ ausführen. Sicherung: $BACKUP_ZIP$\r$\nDie bereits kopierten neuen Programmdateien in $INSTDIR können gelöscht werden." /SD IDOK
        ${EndIf}
    ${Else}
        MessageBox MB_OK|MB_ICONSTOP "Die Aktualisierung wurde abgebrochen, und der bisherige Dienst konnte nicht automatisch wiederhergestellt werden.$\r$\n$\r$\nBitte in einer Eingabeaufforderung als Administrator ausführen:$\r$\n$\"$R2$\" install ${SERVICE_NAME} $\"$OLD_APP$\"$\r$\n$\"$R2$\" set ${SERVICE_NAME} AppDirectory $\"$OLD_APPDIR$\"$\r$\n$\"$R2$\" set ${SERVICE_NAME} AppEnvironmentExtra $\"ZETTELWIRTSCHAFT_CONFIG=$CONFIG_PATH$\"$\r$\n$\"$R2$\" set ${SERVICE_NAME} AppStdout $\"$OLD_STDOUT$\"$\r$\nnet start ${SERVICE_NAME}$\r$\n$\r$\n(Meldet nssm „marked for deletion“, zuerst neu starten.) Sicherung: $BACKUP_ZIP" /SD IDOK
    ${EndIf}
FunctionEnd

; Nach service-install.bat: Ergebnis pruefen, Health-Check, alten Programmordner
; optional loeschen, Text fuer die Abschlussseite.
Function FinishAdoption
    ${If} $BACKUP_ZIP != ""
        StrCpy $R5 "Sicherung vor dem Update: $BACKUP_ZIP"
    ${Else}
        StrCpy $R5 "Es wurde KEINE Sicherung erstellt"
    ${EndIf}

    ${If} $SERVICE_EXIT != "0"
        Call GetServiceState
        Pop $R1
        ${If} $R1 != "new"
            ; Kein neuer Dienst: Abbruch -> .onInstFailed stellt den alten wieder her.
            MessageBox MB_OK|MB_ICONSTOP "Der Dienst ${SERVICE_NAME} konnte nicht neu eingerichtet werden (Code $SERVICE_EXIT).$\r$\nDie bisherige Installation wird wiederhergestellt." /SD IDOK
            Call RestoreLegacyService
            Abort "Update abgebrochen: Dienst nicht eingerichtet."
        ${EndIf}
    ${EndIf}

    ; Ab hier ist der neue Dienst registriert, der alte nicht mehr: erst jetzt
    ; FRONTEND_DIST_DIR umstellen. Vorher haette ein Rollback oder ein
    ; Stromausfall die alten Programmdateien mit einer bereits geaenderten
    ; config.toml starten lassen (alte Versionen liefern die Oberflaeche aus
    ; FRONTEND_DIST_DIR aus). Das Bundle nimmt ohnehin immer <install>\frontend
    ; (INFRA-01) und warnt beim ersten Start einmal; nur diese eine Zeile wird
    ; ersetzt (atomar), der Rest der config.toml bleibt Byte fuer Byte erhalten.
    ${If} $OLD_FRONTEND_KEY == "1"
        DetailPrint "config.toml: FRONTEND_DIST_DIR auf $INSTDIR\frontend umstellen..."
        Delete "$PLUGINSDIR\frontend.ini"
        Push '-Action SetFrontendDir -ConfigPath "$CONFIG_PATH" -FrontendDir "$INSTDIR\frontend" -ResultFile "$PLUGINSDIR\frontend.ini"'
        Call LegacyHelper
        Pop $R0
        ${If} $R0 != "0"
            DetailPrint "Hinweis: FRONTEND_DIST_DIR nicht angepasst (Code $R0) - die Weboberfläche kommt trotzdem aus $INSTDIR\frontend."
        ${EndIf}
    ${EndIf}

    ${If} $SERVICE_EXIT != "0"
        MessageBox MB_OK|MB_ICONEXCLAMATION "Der Dienst ${SERVICE_NAME} ist eingerichtet, startet aber nicht (Code $SERVICE_EXIT).$\r$\nDetails: $LOG_DIR\backend.log$\r$\n$\r$\n$R5.$\r$\nDie bisherigen Programmdateien bleiben in $OLD_DIR." /SD IDOK
        StrCpy $FINISH_NOTE "$\r$\n$\r$\nACHTUNG: Der Dienst startet nicht (siehe $LOG_DIR\backend.log). $R5."
        Return
    ${EndIf}

    DetailPrint "Warte auf die Weboberfläche (bis zu 3 Minuten; beim ersten Start wird die Datenbank aktualisiert)..."
    Push '-Action WaitHealth -ServiceName ${SERVICE_NAME} -ConfigPath "$CONFIG_PATH" -TimeoutSeconds 180'
    Call LegacyHelper
    Pop $R1
    ${If} $R1 != "0"
        MessageBox MB_OK|MB_ICONEXCLAMATION "Zettelwirtschaft ${VERSION} ist eingerichtet, die Weboberfläche antwortet aber noch nicht.$\r$\nDetails: $LOG_DIR\backend.log$\r$\n$\r$\nDer alte Programmordner bleibt vorsichtshalber erhalten:$\r$\n$OLD_DIR" /SD IDOK
        StrCpy $FINISH_NOTE "$\r$\n$\r$\nDie bisherige Installation (Version $OLD_VERSION) wurde übernommen. $R5. Der alte Programmordner bleibt erhalten: $OLD_DIR"
        Return
    ${EndIf}

    ${If} $OLD_DIR_DELETABLE == "1"
        MessageBox MB_YESNO|MB_ICONQUESTION "Zettelwirtschaft ${VERSION} läuft.$\r$\n$\r$\nDie alten Programmdateien (Version $OLD_VERSION) jetzt löschen?$\r$\n$OLD_DIR$\r$\n$\r$\nGelöscht werden nur bekannte Programmdateien (backend, frontend, bin, nssm.exe, Skripte). Datenbank, Dokumente, config.toml und Logs liegen woanders; andere Dateien in diesem Ordner bleiben erhalten." /SD IDNO IDNO keep_old
        ; Kein RMDir /r: der Ordner kann eigene Dateien des Nutzers enthalten
        ; (Notizen, alte .env, Kopien). legacy-install.ps1 loescht nur bekannte
        ; Programmdateien, folgt keinen Verknuepfungen und entfernt den Ordner
        ; nur, wenn er danach leer ist.
        Delete "$PLUGINSDIR\remove.ini"
        Push '-Action RemoveOldDir -OldDir "$OLD_DIR" -ConfigPath "$CONFIG_PATH" -DbPath "$DB_PATH" -ResultFile "$PLUGINSDIR\remove.ini"'
        Call LegacyHelper
        Pop $R2
        ReadINIStr $R3 "$PLUGINSDIR\remove.ini" "result" "Status"
        ${If} $R3 == "removed"
            StrCpy $R6 "Der alte Programmordner wurde gelöscht."
        ${ElseIf} $R3 == "partial"
            ReadINIStr $R4 "$PLUGINSDIR\remove.ini" "result" "Remaining"
            StrCpy $R6 "Die alten Programmdateien wurden gelöscht. Im Ordner $OLD_DIR liegen noch weitere Dateien, die nicht angetastet wurden: $R4"
        ${Else}
            StrCpy $R6 "Der alte Programmordner konnte nicht gelöscht werden: $OLD_DIR"
        ${EndIf}
        Goto old_done
        keep_old:
        StrCpy $R6 "Der alte Programmordner bleibt erhalten und kann gelöscht werden: $OLD_DIR"
        old_done:
    ${ElseIf} $OLD_DATA_INSIDE == "1"
        StrCpy $R6 "Der alte Programmordner $OLD_DIR enthält noch Daten (Datenbank, Konfiguration oder Logs) und bleibt deshalb bestehen."
    ${Else}
        StrCpy $R6 "Der alte Programmordner bleibt erhalten: $OLD_DIR"
    ${EndIf}
    DetailPrint "$R6"
    StrCpy $FINISH_NOTE "$\r$\n$\r$\nDie bisherige Installation (Version $OLD_VERSION) wurde übernommen. $R5. $R6"
FunctionEnd

; --- Install Section ---
Section "Zettelwirtschaft" SecMain
    SectionIn RO
    SetOutPath "$INSTDIR"
    ; Erstinstallation/Upgrade: Logs im Datenordner. Uebernahme: Log-Ordner des
    ; bisherigen Dienstes (AppStdout, aus DetectLegacyInstall).
    ${If} $LOG_DIR == ""
        StrCpy $LOG_DIR "$DATA_DIR\logs"
    ${EndIf}

    ; INFRA-03: Programmordner VOR dem Entpacken (und vor dem Backup-Aufruf der
    ; alten Exe beim Upgrade) auf SYSTEM/Administratoren Vollzugriff, Benutzer
    ; Lesen setzen - ein frei gewaehlter Ordner erbt sonst "Authentifizierte
    ; Benutzer: Aendern" von der Laufwerkswurzel. Das Skript laeuft aus
    ; $PLUGINSDIR (Temp des Admins), nicht aus dem noch ungeschuetzten Ordner.
    File "/oname=$PLUGINSDIR\secure-install-dir.ps1" "${DIST}\secure-install-dir.ps1"
    DetailPrint "Zugriffsrechte des Programmordners festlegen..."
    nsExec::ExecToLog '${POWERSHELL} -File "$PLUGINSDIR\secure-install-dir.ps1" "$INSTDIR"'
    Pop $R0
    ${If} $R0 != "0"
    ${AndIf} $R0 != "2"
        MessageBox MB_OK|MB_ICONSTOP "Die Zugriffsrechte des Programmordners $INSTDIR konnten nicht gesetzt werden (Code $R0).$\r$\nOhne sie könnten andere Benutzer Programmdateien des Dienstes austauschen. Bitte einen Ordner unter $PROGRAMFILES64 wählen." /SD IDOK
        Abort "Installation abgebrochen: Programmordner nicht geschützt."
    ${EndIf}

    ${If} $UPGRADE == "1"
        Call PrepareUpgrade
    ${ElseIf} $ADOPT == "1"
        Call PrepareAdoption
    ${EndIf}

    ; Bundle entpacken — Inhalt von %DIST% kommt 1:1 in $INSTDIR
    File /r "${DIST}\backend"
    File /r "${DIST}\frontend"
    File /r "${DIST}\bin"
    File "${DIST}\nssm.exe"
    File "${DIST}\service-install.bat"
    File "${DIST}\service-uninstall.bat"
    File "${DIST}\config.toml.example"
    File "${DIST}\VERSION"
    File "${DIST}\Convert-Env-To-Config.ps1"
    File "${DIST}\New-NativeConfig.ps1"
    File "${DIST}\model-tiers.ps1"
    File "${DIST}\secure-acl.ps1"
    File "${DIST}\secure-install-dir.ps1"
    File "${DIST}\new-secret.ps1"
    File "${DIST}\legacy-install.ps1"
    File "${DIST}\update-wizard.bat"
    File "${DIST}\update-wizard.ps1"
    File "${DIST}\backup.bat"
    File "${DIST}\restore-backup.bat"
    File "${DIST}\restore-backup.ps1"

    ; config.toml nur bei Erstinstallation (oder wenn sie fehlt) schreiben —
    ; ein neuer EMAIL_ENCRYPTION_KEY wuerde gespeicherte IMAP-Passwoerter
    ; unbrauchbar machen.
    ${If} ${FileExists} "$CONFIG_PATH"
        DetailPrint "Behalte bestehende config.toml: $CONFIG_PATH"
        ; Auch bei einer Erstinstallation in einen Datenordner mit vorhandener
        ; config.toml gelten deren PIN und Port, nicht die aus dem Setup-Dialog.
        StrCpy $FINISH_PIN_TEXT "PIN: unverändert (steht in $CONFIG_PATH)."
        Call ReadConfigPort
    ${Else}
        ${If} $PIN_CODE == ""
            ; Upgrade ohne config.toml: frischen PIN erzeugen.
            Push "pin"
            Call GenerateSecret
            Pop $PIN_CODE
            StrCpy $FINISH_PIN_TEXT "PIN: $PIN_CODE (bitte notieren!)"
        ${EndIf}
        DetailPrint "Schreibe config.toml..."
        ${If} $LOCAL_ONLY == ${BST_CHECKED}
            StrCpy $SERVER_HOST "127.0.0.1"
        ${Else}
            StrCpy $SERVER_HOST "0.0.0.0"
        ${EndIf}
        nsExec::ExecToLog '${POWERSHELL} -File "$INSTDIR\New-NativeConfig.ps1" -OutPath "$CONFIG_PATH" -DataDir "$DATA_DIR" -FrontendDistDir "$INSTDIR\frontend" -Port $SERVER_PORT -ServerHost $SERVER_HOST -PinCode "$PIN_CODE" -Version "${VERSION}"'
        Pop $R0
        ${If} $R0 != "0"
            MessageBox MB_OK|MB_ICONSTOP "Die Konfiguration (config.toml) konnte nicht geschrieben werden (Code $R0).$\r$\nDetails stehen im Installationsprotokoll." /SD IDOK
            Abort "Installation abgebrochen: config.toml fehlt."
        ${EndIf}
    ${EndIf}

    ; SEC-DATA-03: Datenbank, Archiv, Backups, Logs und config.toml nur fuer
    ; SYSTEM (Dienst), Administratoren und das installierende Konto. Ohne das
    ; erbt ein frei gewaehlter Datenordner (C:\Zettelwirtschaft, D:\...) von der
    ; Laufwerkswurzel "Authentifizierte Benutzer: Aendern". Idempotent, laeuft
    ; auch bei Upgrades; Netzwerkpfade/Fehler nur als Hinweis im Protokoll.
    DetailPrint "Zugriffsrechte des Datenordners einschraenken..."
    nsExec::ExecToLog '${POWERSHELL} -File "$INSTDIR\secure-acl.ps1" "$DATA_DIR\data" "$LOG_DIR" "$CONFIG_PATH"'
    Pop $R0

    ; Firewall-Rule (nur Erstinstallation — sonst entstehen doppelte Regeln;
    ; nicht bei "nur dieser PC", INFRA-07)
    ${If} $UPGRADE != "1"
    ${AndIf} $LOCAL_ONLY != ${BST_CHECKED}
        ${If} $ADOPT == "1"
            ; Uebernahme: Bind-Adresse aus der bestehenden config.toml; eine von
            ; Hand angelegte Regel gleichen Namens nicht verdoppeln.
            ClearErrors
            ${ConfigRead} "$CONFIG_PATH" "SERVER_HOST = " $R1
            ${If} $R1 != '"127.0.0.1"'
                DetailPrint "Firewall-Regel (falls noch nicht vorhanden)..."
                nsExec::ExecToLog '${POWERSHELL} -Command "if (-not (Get-NetFirewallRule -DisplayName Zettelwirtschaft -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName Zettelwirtschaft -Direction Inbound -Protocol TCP -LocalPort $SERVER_PORT -Action Allow -Profile Private -ErrorAction SilentlyContinue }"'
                Pop $R0
            ${EndIf}
        ${Else}
            DetailPrint "Firewall-Regel..."
            nsExec::ExecToLog '${POWERSHELL} -Command "New-NetFirewallRule -DisplayName Zettelwirtschaft -Direction Inbound -Protocol TCP -LocalPort $SERVER_PORT -Action Allow -Profile Private -ErrorAction SilentlyContinue"'
            Pop $R0
        ${EndIf}
    ${EndIf}

    ; Service installieren (idempotent, ersetzt einen vorhandenen Dienst - bei
    ; der Uebernahme den bisherigen, manuell eingerichteten)
    DetailPrint "Service registrieren..."
    nsExec::ExecToLog '"$INSTDIR\service-install.bat" "$INSTDIR" "$CONFIG_PATH" "$LOG_DIR"'
    Pop $SERVICE_EXIT
    ${If} $ADOPT == "1"
        Call FinishAdoption
    ${ElseIf} $SERVICE_EXIT != "0"
        MessageBox MB_OK|MB_ICONEXCLAMATION "Der Dienst ${SERVICE_NAME} konnte nicht gestartet werden (Code $SERVICE_EXIT).$\r$\nDetails: $LOG_DIR\backend.log" /SD IDOK
    ${EndIf}

    ; Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Registry (64-Bit-Ansicht, siehe Kopfkommentar)
    ${If} $LEGACY_REGVIEW == "1"
        SetRegView 32
        DeleteRegKey HKLM "${REG_KEY}"
        DeleteRegKey HKLM "${UNINST_KEY}"
        SetRegView 64
    ${EndIf}
    WriteRegStr HKLM "${REG_KEY}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "${REG_KEY}" "DataDir" "$DATA_DIR"
    WriteRegStr HKLM "${REG_KEY}" "ConfigPath" "$CONFIG_PATH"
    WriteRegStr HKLM "${REG_KEY}" "Version" "${VERSION}"

    WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "Zettelwirtschaft ${VERSION}"
    WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "Zettelwirtschaft Open Source"
    WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1

    ; Startmenü
    CreateDirectory "$SMPROGRAMS\Zettelwirtschaft"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Zettelwirtschaft oeffnen.lnk" \
        "http://localhost:$SERVER_PORT" "" "$SYSDIR\shell32.dll" 13
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Logs ansehen.lnk" \
        "$LOG_DIR"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk" \
        "$INSTDIR\Uninstall.exe"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Update.lnk" \
        "$INSTDIR\update-wizard.bat" "" "$SYSDIR\shell32.dll" 46
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Backup jetzt.lnk" \
        "$INSTDIR\backup.bat" "" "$SYSDIR\shell32.dll" 45
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Backup wiederherstellen.lnk" \
        "$INSTDIR\restore-backup.bat" "" "$SYSDIR\shell32.dll" 238

    ; Ollama-Hinweis (wird nicht mitinstalliert, siehe Willkommensseite)
    Call DetectOllama
    Pop $R0
    ${If} $R0 != "1"
        MessageBox MB_YESNO|MB_ICONINFORMATION "Ollama (lokaler KI-Server) wurde auf diesem Rechner nicht gefunden.$\r$\n$\r$\nOhne Ollama werden Dokumente zwar archiviert, aber nicht automatisch analysiert. Benötigt wird Ollama ab Version ${OLLAMA_MIN_VERSION}. Die KI-Modelle (mehrere GB) lädt Zettelwirtschaft nach der Ollama-Installation selbst herunter.$\r$\n$\r$\nDownload-Seite von Ollama jetzt öffnen?" /SD IDNO IDNO ollama_done
        ExecShell "open" "${OLLAMA_DOWNLOAD_URL}"
        ollama_done:
    ${EndIf}
SectionEnd

; --- Browser öffnen ---
Function OpenBrowser
    ExecShell "open" "http://localhost:$SERVER_PORT"
FunctionEnd

Function OpenLogs
    ExecShell "open" "$LOG_DIR"
FunctionEnd

; Abbruch der Installation (Abort in der Section, Datei nicht entpackbar):
; bei einer Uebernahme den bisherigen Dienst wiederherstellen.
Function .onInstFailed
    ${If} $ADOPT == "1"
    ${AndIf} $ADOPT_STOPPED == "1"
        Call RestoreLegacyService
    ${EndIf}
FunctionEnd

; --- Helper: Ollama vorhanden? Stack out: "1" oder "0" ---
Function DetectOllama
    Push $R0
    Push $R1
    nsExec::ExecToStack 'sc query Ollama'
    Pop $R0  ; exit code
    Pop $R1  ; output (verworfen)
    StrCpy $R1 "0"
    ${If} $R0 == "0"
        StrCpy $R1 "1"
    ${ElseIf} ${FileExists} "$LOCALAPPDATA\Programs\Ollama\ollama.exe"
        StrCpy $R1 "1"
    ${ElseIf} ${FileExists} "$PROGRAMFILES64\Ollama\ollama.exe"
        StrCpy $R1 "1"
    ${EndIf}
    StrCpy $R0 $R1
    Pop $R1
    Exch $R0
FunctionEnd

; --- Uninstall ---
Function un.onInit
    SetRegView 64
FunctionEnd

Section "Uninstall"
    ; Pfade aus der 64-Bit-Ansicht, Fallback auf Altwerte (32-Bit-Ansicht)
    ReadRegStr $1 HKLM "${REG_KEY}" "ConfigPath"
    ReadRegStr $0 HKLM "${REG_KEY}" "DataDir"
    ${If} $1 == ""
        SetRegView 32
        ReadRegStr $1 HKLM "${REG_KEY}" "ConfigPath"
        ReadRegStr $0 HKLM "${REG_KEY}" "DataDir"
        SetRegView 64
    ${EndIf}

    ; Sicherungs-Backup vor dem Entfernen (best-effort, Dienst laeuft ggf. noch).
    ; WICHTIG: --out-dir nach $DOCUMENTS, NICHT in den Datenordner — sonst loescht
    ; das RMDir /r unten (bei "Daten loeschen? Ja") genau dieses Backup wieder mit.
    StrCpy $R2 ""   ; Backup-Status-Text fuer den Loesch-Dialog (leer = kein Backup versucht)
    ${If} ${FileExists} "$INSTDIR\backend\zettelwirtschaft-backend.exe"
    ${AndIf} $1 != ""
        DetailPrint "Erstelle Sicherungs-Backup in $DOCUMENTS\Zettelwirtschaft-Backups..."
        nsExec::ExecToLog '"$INSTDIR\backend\zettelwirtschaft-backend.exe" --config "$1" --backup --out-dir "$DOCUMENTS\Zettelwirtschaft-Backups"'
        Pop $R0
        ${If} $R0 == "0"
            StrCpy $R2 "Ein Sicherungs-Backup der Datenbank liegt in$\r$\n$DOCUMENTS\Zettelwirtschaft-Backups.$\r$\n$\r$\n"
        ${Else}
            StrCpy $R2 "WARNUNG: Das automatische Sicherungs-Backup ist fehlgeschlagen (Code $R0). Es existiert KEIN frisches Backup.$\r$\n$\r$\n"
        ${EndIf}
    ${EndIf}

    ; Service entfernen
    nsExec::ExecToLog '"$INSTDIR\service-uninstall.bat" "$INSTDIR"'
    Pop $R0

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
    Delete "$INSTDIR\New-NativeConfig.ps1"
    Delete "$INSTDIR\model-tiers.ps1"
    Delete "$INSTDIR\secure-acl.ps1"
    Delete "$INSTDIR\secure-install-dir.ps1"
    Delete "$INSTDIR\new-secret.ps1"
    Delete "$INSTDIR\legacy-install.ps1"
    Delete "$INSTDIR\update-wizard.bat"
    Delete "$INSTDIR\update-wizard.ps1"
    Delete "$INSTDIR\backup.bat"
    Delete "$INSTDIR\restore-backup.bat"
    Delete "$INSTDIR\restore-backup.ps1"
    Delete "$INSTDIR\Uninstall.exe"

    ; Startmenü
    Delete "$SMPROGRAMS\Zettelwirtschaft\Zettelwirtschaft oeffnen.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Logs ansehen.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Update.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Backup jetzt.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Backup wiederherstellen.lnk"
    RMDir "$SMPROGRAMS\Zettelwirtschaft"

    ; Daten: User fragen
    ${If} $0 != ""
    ${AndIf} ${FileExists} "$0\data\*.*"
        MessageBox MB_YESNO|MB_ICONQUESTION \
            "Sollen die Daten (Dokumente, Datenbank, Backups) in$\r$\n$0$\r$\nebenfalls gelöscht werden?$\r$\n$\r$\n$R2Dies kann NICHT rückgängig gemacht werden." \
            /SD IDNO IDNO keep_data
        RMDir /r "$0"
        keep_data:
    ${EndIf}

    ; INSTDIR (sollte leer sein)
    RMDir "$INSTDIR"

    ; Registry (beide Ansichten, falls noch Altwerte existieren)
    DeleteRegKey HKLM "${REG_KEY}"
    DeleteRegKey HKLM "${UNINST_KEY}"
    SetRegView 32
    DeleteRegKey HKLM "${REG_KEY}"
    DeleteRegKey HKLM "${UNINST_KEY}"
    SetRegView 64
SectionEnd
