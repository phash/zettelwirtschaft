; Zettelwirtschaft - NSIS Installer
; Erzeugt eine Setup.exe die alle Dateien entpackt und den Konfigurationsassistenten startet

!include "MUI2.nsh"

; --- Version (wird beim Build per -DVERSION=x.y.z gesetzt) ---
!ifndef VERSION
    !define VERSION "dev"
!endif

; --- General ---
Name "Zettelwirtschaft ${VERSION}"
OutFile "Zettelwirtschaft-${VERSION}-Setup.exe"
Unicode True
InstallDir "$LOCALAPPDATA\Zettelwirtschaft"
InstallDirRegKey HKCU "Software\Zettelwirtschaft" "InstallDir"
RequestExecutionLevel user
BrandingText "Zettelwirtschaft ${VERSION}"

; --- MUI Settings ---
!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "Installation abbrechen?"

!define MUI_WELCOMEPAGE_TITLE "Zettelwirtschaft ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "\
Willkommen beim Setup von Zettelwirtschaft.$\r$\n$\r$\n\
Dieses Programm entpackt die Anwendungsdateien und startet$\r$\n\
den Konfigurationsassistenten.$\r$\n$\r$\n\
Der Assistent richtet Docker-Container ein, konfiguriert$\r$\n\
das System und laedt das KI-Modell herunter.$\r$\n$\r$\n\
Voraussetzung: Docker Desktop muss installiert sein.$\r$\n\
(https://docker.com/products/docker-desktop/)"

!define MUI_DIRECTORYPAGE_TEXT_TOP "Waehle den Ordner, in den Zettelwirtschaft installiert werden soll.$\r$\nDokumente und Datenbank werden im Unterordner 'data' gespeichert."

!define MUI_FINISHPAGE_TITLE "Dateien entpackt"
!define MUI_FINISHPAGE_TEXT "\
Die Dateien wurden erfolgreich entpackt nach:$\r$\n$\r$\n\
$INSTDIR$\r$\n$\r$\n\
Klicke 'Fertig stellen' um den Konfigurationsassistenten$\r$\n\
zu starten. Dieser richtet Docker ein und konfiguriert$\r$\n\
alle Dienste."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Konfigurationsassistenten starten"
!define MUI_FINISHPAGE_RUN_FUNCTION "LaunchSetup"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

; --- Pages ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; --- Language ---
!insertmacro MUI_LANGUAGE "German"

; --- Install Section ---
Section "Zettelwirtschaft" SecMain
    SectionIn RO
    SetOutPath "$INSTDIR"

    ; Anwendungsdateien
    File "docker-compose.yml"
    File ".env.example"
    File "install.bat"
    File "install.ps1"
    File "install-gui.ps1"
    File "start.bat"
    File "stop.bat"
    File "update.bat"
    File "uninstall.bat"

    ; data-Verzeichnis anlegen
    CreateDirectory "$INSTDIR\data"

    ; Installationsverzeichnis merken
    WriteRegStr HKCU "Software\Zettelwirtschaft" "InstallDir" "$INSTDIR"

    ; Uninstaller erzeugen
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Eintrag in Programme & Features (Systemsteuerung)
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "DisplayName" "Zettelwirtschaft ${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "Publisher" "Zettelwirtschaft"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft" \
        "NoRepair" 1

    ; Startmenue-Eintrag
    CreateDirectory "$SMPROGRAMS\Zettelwirtschaft"
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Zettelwirtschaft.lnk" "$INSTDIR\start.bat" \
        "" "$INSTDIR\start.bat" 0
    CreateShortcut "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

; --- Launch Setup Wizard ---
Function LaunchSetup
    Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install-gui.ps1"'
FunctionEnd

; --- Uninstall Section ---
Section "Uninstall"
    ; Container stoppen
    nsExec::ExecToLog 'cmd /c "cd /d "$INSTDIR" && docker compose down --remove-orphans 2>nul"'

    ; Anwendungsdateien entfernen
    Delete "$INSTDIR\docker-compose.yml"
    Delete "$INSTDIR\docker-compose.override.yml"
    Delete "$INSTDIR\.env.example"
    Delete "$INSTDIR\.env"
    Delete "$INSTDIR\install.bat"
    Delete "$INSTDIR\install.ps1"
    Delete "$INSTDIR\install-gui.ps1"
    Delete "$INSTDIR\start.bat"
    Delete "$INSTDIR\stop.bat"
    Delete "$INSTDIR\update.bat"
    Delete "$INSTDIR\uninstall.bat"
    Delete "$INSTDIR\Uninstall.exe"

    ; Startmenue entfernen
    Delete "$SMPROGRAMS\Zettelwirtschaft\Zettelwirtschaft.lnk"
    Delete "$SMPROGRAMS\Zettelwirtschaft\Deinstallieren.lnk"
    RMDir "$SMPROGRAMS\Zettelwirtschaft"

    ; Desktop-Verknuepfung entfernen
    Delete "$DESKTOP\Zettelwirtschaft.lnk"

    ; Daten-Verzeichnis: Benutzer fragen
    IfFileExists "$INSTDIR\data\*.*" 0 noData
        MessageBox MB_YESNO|MB_ICONQUESTION \
            "Sollen auch alle Dokumente und die Datenbank geloescht werden?$\r$\n$\r$\nDies kann nicht rueckgaengig gemacht werden!" \
            /SD IDNO IDNO noData
        RMDir /r "$INSTDIR\data"
    noData:

    ; Installationsverzeichnis entfernen (nur wenn leer)
    RMDir "$INSTDIR"

    ; Registry aufraeumen
    DeleteRegKey HKCU "Software\Zettelwirtschaft"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zettelwirtschaft"
SectionEnd
