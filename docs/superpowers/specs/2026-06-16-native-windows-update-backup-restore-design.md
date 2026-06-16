# Native-Windows: Update-Wizard + Backup + Restore (Design)

**Datum:** 2026-06-16
**Branch:** `feat/native-update-backup-restore` (Basis: `feat/1.4.1-update-check-and-https`)
**Vorbild:** `E:\claude\zeiterfassung\praxiszeit\installer\windows\` (PraxisZeit)

## 1. Kontext & Motivation

Der Native-Windows-Pfad von Zettelwirtschaft (ab v1.3) hat bereits einen vollständigen
NSIS-Installer (`setup-native.nsi`), NSSM-Service-Skripte (`scripts/service-install.bat`,
`service-uninstall.bat`) und einen Build (`scripts/build-native.ps1`, Output `dist/native/`).

Was **fehlt** gegenüber dem PraxisZeit-Installer (zeiterfassung) — und genau das ist der Auftrag:

| Fähigkeit | PraxisZeit | Zettelwirtschaft Native (heute) |
|---|---|---|
| **Update** einer bestehenden Installation | GUI-Wizard `update-wizard.ps1` | — (man müsste Setup.exe neu laufen lassen) |
| **Backup** (manuell / vor Update / vor Uninstall) | `backup.bat` + Scheduled-Task | nur in-process Auto-Backup im Backend |
| **Restore** aus Backup | `restore-backup.template.bat` | — |
| **Uninstall mit Sicherungs-Backup** | `uninstall.bat` (Backup → Desktop) | NSIS-Uninstaller **ohne** Backup |

Die `feat/1.4.1`-Branch bringt bereits eine **In-App-Update-*Prüfung*** (Manifest + Banner,
`update_service.py`, `UpdateCheck.vue`). Dieses Feature ist der **Mechanismus**, der das vom
Banner angekündigte Update tatsächlich durchführt. Beide gehören in 1.4.x zusammen.

## 2. Ziele / Nicht-Ziele

**Ziele**
- GUI-Update-Wizard (WinForms, wie PraxisZeit) + Headless-Modus für eine bestehende
  Native-Installation.
- Manuelles Backup + geführter Restore.
- Uninstaller erstellt vor dem Entfernen ein Sicherungs-Backup.
- Eingebettet in den bestehenden `dist/native` + NSIS-Workflow (kein paralleles Skript-Set).

**Nicht-Ziele**
- Kein Auto-Launch des Wizards aus dem Web-Banner (eine Web-App kann keinen Admin-Installer
  elevieren). Das Banner bleibt Hinweis + Download-Link.
- Kein Docker-Pfad (der hat `update.bat`/`uninstall.bat` bereits).
- Kein Windows-Scheduled-Backup-Task (siehe Entscheidung E2).

## 3. Architektur-Entscheidungen

### E1 — Backup offline über die Backend-Exe, nicht über HTTP
Die PIN-Middleware (`main.py`) whitelistet nur `/api/health` + `/api/auth*`. Da der Installer
PIN standardmäßig **aktiviert**, würde `POST /api/system/backup` ohne Session-Cookie **401**
liefern. Lösung: ein **Offline-Subcommand** an der Backend-Exe, das `backup_service.create_backup`
direkt aufruft — keine Auth, funktioniert auch bei gestopptem/ungesundem Service, und nutzt die
bereits getestete, konsistente SQLite-Sicherung (`sqlite3.Connection.backup()`).

### E2 — Kein Windows-Scheduled-Task für tägliche Backups
Das Backend hat bereits `backup_service.run_auto_backup` (täglich, Retention 7 daily + 4 weekly),
das als Background-Task im Service läuft. Der Service ist Auto-Start → tägliche Backups passieren
bereits. Ein zusätzlicher Scheduled-Task wäre redundant. (PraxisZeit braucht ihn nur, weil sein
Backup extern via `pg_dump` läuft und nicht im Server-Prozess.)

### E3 — Backup-Umfang: DB + Config (Standard), Full optional
Für Pre-Update / Pre-Uninstall genügt **DB-only** (`include_documents=False`). Begründung:
- Dokumente liegen ohnehin als statische Dateien im Archiv (`ARCHIVE_DIR`) und werden von einem
  Update nicht angefasst.
- ChromaDB ist aus DB+Archiv re-derivierbar ("Vektor-Index neu aufbauen").
- DB-only ist klein und schnell (daily-tauglich).
`--full` (inkl. Dokumente) bleibt manuell verfügbar.

### E4 — Update = Datei-Ersetzung, keine pip/VC++-Phase
Das Backend ist ein eingefrorenes PyInstaller-Onedir-Bundle. Ein Update ist reine
Datei-Ersetzung (`robocopy` neuer `backend/`+`frontend/`+`bin/` über InstallDir) + Service-Neustart.
Alembic-Migrationen laufen automatisch beim Service-Start (`entrypoint.py:_run_migrations`).
Die PraxisZeit-Schritte **pip-install** und **vc_redist** entfallen ersatzlos.

### E5 — Install/Data/Version aus der Registry erkennen
`setup-native.nsi` schreibt `HKLM\Software\Zettelwirtschaft` mit `InstallDir`, `DataDir`,
`ConfigPath`, `Version`. Der Wizard liest diese statt einer Pfad-Rateliste (sauberer als PraxisZeit).
Aktuelle Version = `<InstallDir>\VERSION`, neue Version = `<UpdateQuelle>\VERSION`.

### E6 — Trennung Install-Dir ↔ Data-Dir vereinfacht den Copy
Programmdateien liegen in `$INSTDIR` (Program Files), Nutzerdaten + `config.toml` in einem
separaten Data-Dir. `robocopy` der neuen Programmdateien fasst Config/Daten nie an. `robocopy`
läuft **additiv** (kein `/PURGE`), damit `Uninstall.exe` / `config.toml.example` im InstallDir
erhalten bleiben.

## 4. Komponenten & Schnittstellen

### 4.1 Backend: `entrypoint.py` — neuer Modus `--backup`
- **Aufruf:** `zettelwirtschaft-backend.exe --config <config.toml> --backup [--full]`
- **Verhalten:** Config setzen → Settings laden → `backup_service.create_backup(settings,
  include_documents=args.full)` → absoluten ZIP-Pfad auf stdout drucken → exit 0; bei Fehler
  Stacktrace auf stderr + exit 1.
- **Abhängigkeit:** `app.services.backup_service` (existiert, unverändert).
- **Reihenfolge:** vor dem uvicorn-Start, analog zu `--migrate-only` / `--version`. Kein
  `_ensure_data_dirs`/`_run_migrations` nötig (Backup liest nur vorhandene Dateien); das
  Backup-Verzeichnis legt `backup_service._backup_dir` selbst an.

### 4.2 `scripts/update-wizard.ps1` (+ `update-wizard.bat`)
- **Launcher (`.bat`):** prüft Admin (`net session`), `chcp 65001`, startet
  `powershell -NoProfile -ExecutionPolicy Bypass -STA -File update-wizard.ps1 %*`.
- **Parameter:** `-InstallDir <pfad>` (optional, sonst Registry), `-DryRun`, `-Headless`.
- **GUI:** WinForms, drei Seiten — Welcome (erkannte Umgebung: InstallDir, DataDir, aktuelle/neue
  Version, Quelle) → Fortschritt (Schritt-Status + Progressbar + Log-TextBox) → Fertig.
- **Headless:** keine GUI, maschinenlesbare Marker auf stdout (`[STEP]`, `[LOG]`, `[PROGRESS]`,
  `[DONE]`), Exit 0/1.
- **Guard:** Wizard verweigert Ausführung, wenn `InstallDir == WizardDir` (Update muss aus
  extrahiertem Temp-Ordner laufen).
- **Schritte (`Invoke-Update`):**
  1. **Backup** — `<InstallDir>\backend\zettelwirtschaft-backend.exe --config <ConfigPath> --backup`.
     Nicht-fatal: Warnung bei Fehler, Update fährt fort.
  2. **Service stoppen** — `Stop-Service ZettelwirtschaftBackend -Force`, auf `Stopped` warten.
     Fatal bei Fehler.
  3. **Dateien kopieren** — `robocopy <WizardDir> <InstallDir> /E /R:2 /W:2` (additiv).
     `/XF Uninstall.exe` (NSIS-Uninstaller nie überschreiben). robocopy-Exit < 8 = Erfolg. Fatal
     bei ≥ 8 → Versuch, Service trotzdem wieder zu starten.
  4. **Service starten** — `Start-Service`, auf `Running` warten (Migrationen laufen beim Start).
- **Versionsanzeige:** `Get-AppVersion` liest `<dir>\VERSION`.

### 4.3 `scripts/backup.bat`
- **Aufruf:** `backup.bat` (liest `ConfigPath`/`InstallDir` aus Registry) oder
  `backup.bat <ConfigPath>`.
- **Verhalten:** `<InstallDir>\backend\zettelwirtschaft-backend.exe --config <ConfigPath> --backup`,
  Ausgabe + Exit-Code in `<DataDir>\logs\backup.log` loggen.

### 4.4 `scripts/restore-backup.ps1` (+ `restore-backup.bat`)
- **Launcher (`.bat`):** Admin-Check, startet die PS1.
- **Parameter:** `-BackupZip <pfad>` (Pflicht), `-ConfigPath <pfad>` (optional, sonst Registry).
- **Destruktiv → explizite Tippbestätigung:** Nutzer muss `WIEDERHERSTELLEN` eingeben.
- **Verhalten (offline):**
  1. `config.toml` parsen → DB-Pfad (aus `DATABASE_URL`) + `ARCHIVE_DIR`.
  2. `Stop-Service ZettelwirtschaftBackend`.
  3. ZIP-Layout: `database/zettelwirtschaft.db` (+ optional `documents/<rel>`).
  4. Live-DB-Sidecars `*-wal` / `*-shm` löschen (sonst merged SQLite stale WAL in die
     wiederhergestellte DB), dann `database/zettelwirtschaft.db` über die Live-DB extrahieren.
  5. Falls `documents/` im ZIP: nach `ARCHIVE_DIR` extrahieren (überschreibend).
  6. `Start-Service`.
  7. Hinweis: bei DB-Restore ohne passende ChromaDB ggf. **Vektor-Index neu aufbauen**
     (Einstellungen → Wartung).

### 4.5 Build: `scripts/build-native.ps1`
- Nach `dist/native` kopieren: `update-wizard.bat`, `update-wizard.ps1`, `backup.bat`,
  `restore-backup.bat`, `restore-backup.ps1` (Schritt „NSSM + Service-Skripte").
- Effekt: Skripte landen sowohl in der Setup.exe (→ InstallDir) als auch im `dist/native`-ZIP
  (→ Update-Paket).

### 4.6 Installer: `setup-native.nsi`
- **Install-Section:** die 5 neuen Skripte mit `File` nach `$INSTDIR` aufnehmen.
- **Startmenü:** Verknüpfungen „Update", „Backup jetzt", „Wiederherstellen" (zeigen auf die
  jeweiligen `.bat`).
- **Uninstall-Section:** **vor** `service-uninstall.bat` ein Sicherungs-Backup via
  `nsExec` → `<INSTDIR>\backend\zettelwirtschaft-backend.exe --config <ConfigPath> --backup`;
  Pfad dem Nutzer per `DetailPrint`/MessageBox nennen. Danach: neue Skript-Dateien in der
  Datei-Löschliste ergänzen.
- **Update-Verteilung (dokumentiert, kein Code):** neues `dist/native`-ZIP in Temp extrahieren,
  `update-wizard.bat` als Admin starten.

## 5. Datenfluss (Update)

```
Nutzer lädt Zettelwirtschaft-<neu>-native.zip → entpackt nach C:\Temp\zw-update\
  → Rechtsklick update-wizard.bat → "Als Administrator"
    → update-wizard.ps1 liest Registry (InstallDir/DataDir/ConfigPath) + VERSION-Dateien
      1. backend.exe --backup        (Service läuft, konsistentes DB-ZIP in DataDir\data\backups)
      2. Stop-Service                (NSSM)
      3. robocopy Temp → InstallDir  (backend/ frontend/ bin/ …, additiv, ohne Uninstall.exe)
      4. Start-Service               → entrypoint.py:_run_migrations (Alembic upgrade head)
    → Fertig-Seite / [DONE] success
```

## 6. Fehlerbehandlung

- **Backup-Schritt** nicht-fatal (Warnung, Update fährt fort) — wie PraxisZeit.
- **Stop/Copy/Start** fatal; bei Copy-Fehler Best-effort-Neustart des alten Service.
- **robocopy** Exit 0–7 = Erfolg, ≥ 8 = Fehler.
- **Service-Start-Timeout** → Hinweis auf `<DataDir>\logs\backend.log`.
- **Restore** bricht bei fehlender/ungültiger `config.toml` oder fehlendem `database/`-Eintrag im
  ZIP ab (vor dem Service-Stop, kein halber Zustand).
- `-DryRun` simuliert alle Schritte ohne Seiteneffekte (Sleep + OK), wie PraxisZeit.

## 7. Testing

- **Backend (pytest):** `--backup` erzeugt ein ZIP im Backup-Verzeichnis und druckt einen
  existierenden Pfad; `--full` nimmt Dokumente auf. (analog zu vorhandenen `test_update_service`/
  `test_cert_generator` auf der 1.4.1-Branch.)
- **PS-Skripte:** `-DryRun`-Pfad lokal; dokumentierter manueller Smoke-Test gegen eine echte
  Native-Installation (Service nötig → nicht CI-fähig).
- **Bestehende Suite** (374 Backend-Tests) muss grün bleiben.

## 8. Deliverables (Dateiliste)

**Neu**
- `backend/app/entrypoint.py` → `--backup`/`--full` (Edit)
- `backend/tests/.../test_entrypoint_backup.py`
- `scripts/update-wizard.ps1`
- `scripts/update-wizard.bat`
- `scripts/backup.bat`
- `scripts/restore-backup.ps1`
- `scripts/restore-backup.bat`

**Geändert**
- `scripts/build-native.ps1` (neue Skripte nach `dist/native`)
- `setup-native.nsi` (Install + Startmenü + Uninstall-Backup)
- `CLAUDE.md` / `memory/release-deployment.md` (Native-Update-Ops dokumentieren)

## 9. Offene Punkte
- Genauer Speicherort des pytest für `--backup` (vorhandene Test-Struktur: `backend/tests/...`).
- Exakter ZIP-DB-Pfad (`database/zettelwirtschaft.db`) ist durch `backup_service.create_backup`
  vorgegeben — Restore muss diesen Pfad spiegeln.
</content>
</invoke>
