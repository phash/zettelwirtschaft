# Zettelwirtschaft

## Projektuebersicht

Lokales Dokumentenmanagementsystem fuer Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI (Ollama/lokales LLM) analysiert, kategorisiert und durchsuchbar archiviert. Laeuft ausschliesslich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

## Quickstart

Es gibt **drei Betriebs-Modi**:

| Modus | Wann | Aufruf |
|---|---|---|
| **Docker** | klassischer Stack, Multi-Plattform | `docker compose up -d` |
| **Native** (ab v1.3) | Setup.exe → Windows-Service, ChromaDB embedded | siehe `planung/native-windows-konzept.md` |
| **Dev** (lokal ohne Docker/Setup) | Entwicklung, Tests | `uvicorn app.main:app --reload` |

```bash
# Docker (Backend, Frontend, Ollama, ChromaDB als Container)
docker compose up -d

# Frontend: http://localhost:8080
# Backend NUR intern erreichbar (N-001), Aufrufe gehen ueber nginx-Proxy

# Logs / Health
curl http://localhost:8080/api/health
docker compose logs -f backend
```

**Lokaler Entwicklungs-Modus** (ohne Docker):

```bash
# Backend (Python 3.13, virtualenv empfohlen)
cd backend
pip install -r requirements-dev.txt  # Runtime + pytest (Docker-Image: nur requirements.txt)
python -m alembic upgrade head      # DB-Migrationen
uvicorn app.main:app --reload       # Port 8000

# Frontend (Node 22.12+ / empfohlen 24 LTS)
cd frontend
npm install
npm run dev                          # Vite Dev-Server, Port 3000

# Tests
cd backend && python -m pytest -q   # Anzahl: pytest --collect-only -q (1 skipped ohne Tesseract)
cd e2e && npx playwright test     # baut Frontend + vite preview auf :4181 (E2E_PORT), beide Projekte
# Gegen einen schon laufenden Preview-Server: BASE_URL=http://localhost:4190 npx playwright test
# NIE gegen :8080 (echte Installation) — playwright.config.ts verweigert das.
```

**Native-Build** (Setup.exe fuer Endkunden):

```powershell
# Voraussetzungen am Build-Host:
#   - Python 3.13, Node 22, NSIS 3.x (makensis im PATH)
#   - tools/tesseract/, tools/poppler/, tools/nssm/win64/nssm.exe vorbereitet

pip install -r backend/requirements.txt -r backend/requirements-build.txt
pwsh scripts/build-native.ps1

# Output: Zettelwirtschaft-<version>-Native-Setup.exe + dist/native/
```

**Lokale CI** (GitHub Actions deaktiviert, siehe `.github/workflows/ci.yml.disabled`):

```bash
pwsh scripts/ci-local.ps1                    # Windows: alles
pwsh scripts/ci-local.ps1 -SkipE2E -SkipDocker
bash scripts/ci-local.sh --skip-e2e          # Linux/macOS
```

## Native-Service-Ops

**Service-Steuerung** (NSSM-managed):

```powershell
sc query ZettelwirtschaftBackend     # Status (RUNNING/STOPPED)
sc start ZettelwirtschaftBackend     # braucht Admin
sc stop ZettelwirtschaftBackend      # braucht Admin
# GUI: services.msc -> "Zettelwirtschaft"
```

**Ollama** wird NICHT vom Setup installiert. Der offizielle Windows-Installer
(ollama.com) legt eine Tray-App pro Benutzer an (`%LOCALAPPDATA%\Programs\Ollama`,
startet beim Login) — **keinen** Windows-Dienst. `service-install.bat` setzt
`DependOnService Ollama` daher nur, wenn ein Dienst namens `Ollama` existiert (z.B.
manuell per NSSM eingerichtet, damit Ollama auch ohne Anmeldung laeuft). Das Setup
erkennt Ollama (Dienst oder `ollama.exe`) und verlinkt sonst die Download-Seite.
Die Modelle (`OLLAMA_MODEL` + `EMBEDDING_MODEL`) zieht das Backend beim Start selbst
(`ollama_client.ensure_configured_models`, exakter Tag-Abgleich).

Logs: `~/Documents/Zettelwirtschaft/logs/backend.log` (NSSM-Rotation bei 10 MB).

**Migration Docker → Native** (Bestandskunden, getestet 2026-05):

1. **Backup** (Pflicht vor Migration):
   ```powershell
   $bak = "$env:USERPROFILE\Documents\Zettelwirtschaft-Backups"
   Compress-Archive "$env:LOCALAPPDATA\Zettelwirtschaft\data","$env:LOCALAPPDATA\Zettelwirtschaft\.env" `
       -DestinationPath "$bak\pre-native_$(Get-Date -f yyyyMMdd_HHmmss).zip"
   docker run --rm -v zettelwirtschaft_chromadb-data:/src -v "${bak}:/dst" alpine `
       tar czf /dst/chromadb-volume.tar.gz -C /src .
   ```
2. **Daten kopieren** in Native-Datenordner (`robocopy` mit `/E`):
   `robocopy %LOCALAPPDATA%\Zettelwirtschaft\data ~/Documents/Zettelwirtschaft/data /E`
3. **config.toml generieren** aus alter `.env`:
   `pwsh scripts/Convert-Env-To-Config.ps1 -EnvPath ... -OutPath ... -DataDir ...`
4. **Docker stoppen**: `docker compose down` im alten AppData-Ordner.
5. **ChromaDB-Volume migrieren** ueber `C:\Temp`-Zwischenstation (Docker kann User-Pfade auf Windows nicht direkt mounten):
   ```
   docker run --rm -v zettelwirtschaft_chromadb-data:/src -v C:\Temp\chroma:/dst alpine cp -r /src/. /dst/
   robocopy C:\Temp\chroma ~/Documents/Zettelwirtschaft/data/chromadb /E
   ```
   Alternativ: leerer Ordner → Backend re-indexiert beim ersten Start (~ 30s pro 1000 Docs).
6. **Service installieren:** `Zettelwirtschaft-<version>-Native-Setup.exe` ausführen und als
   Datenordner den Ordner mit der neuen `config.toml` wählen (bestehende config.toml bleibt,
   PIN/Keys werden nicht neu erzeugt). Früher manuell per
   `scripts/service-install.bat <install-dir> <config> <log-dir>` (Admin) — solche
   Installationen übernimmt das Setup ab v1.6.1 (siehe „Manuell eingerichtete Installation“).
7. **Ollama-Modelle** migrieren (gleicher C:\Temp-Trick) oder neu ziehen lassen: das Backend
   pullt fehlende Modelle beim Start automatisch. Manuell: `ollama pull <EMBEDDING_MODEL>` +
   `ollama pull <OLLAMA_MODEL>` — **exakt** die Tags aus `config.toml` (`gemma4:12b` ist ein
   anderer Tag als `gemma4:12b-it-qat`). `Convert-Env-To-Config.ps1` uebernimmt die Modelle der
   alten `.env`; fehlt dort ein Eintrag, lief die Installation mit dem alten Default — der
   Konverter setzt dann bewusst `qwen2.5:7b-instruct` / `bge-m3` (nicht die neuen Defaults).
   Ollama muss **>= 0.30.3** sein (gemma4/qwen3-embedding; getestet 0.40.5).

**Manuell eingerichtete Installation** (Dienst per `service-install.bat`, kein
`HKLM\SOFTWARE\Zettelwirtschaft`, ab v1.6.1): einfach die neue Setup.exe starten. Sie
erkennt den Dienst über `scripts/legacy-install.ps1 -Action Detect` (liest die
NSSM-Parameter aus `HKLM\SYSTEM\CurrentControlSet\Services\ZettelwirtschaftBackend\Parameters`,
Ergebnis als UTF-16-INI für `ReadINIStr`), bietet die Übernahme an und: stoppt den
Dienst → ZIP-Backup von DB (+WAL) und config.toml nach
`%USERPROFILE%\Documents\Zettelwirtschaft-Backups\pre-<version>_<zeit>.zip` (ohne alte Exe)
→ installiert in einen NEUEN Programmordner (nie den alten) → stellt in der config.toml nur
`FRONTEND_DIST_DIR` um → `service-install.bat` ersetzt den Dienst (gleicher Log-Ordner)
→ Registry → Health-Check → alten Programmordner optional löschen. Abbruch davor →
alter Dienst wird wiederhergestellt (`RestoreLegacyService`). Abgelehnt: kein NSSM,
fehlende config.toml, relative Datenpfade. Der Registry-Upgrade-Pfad ist unverändert.

**Update / Backup / Restore** (Native, ab v1.4):

- **Update**: neues `dist/native`-ZIP in einen TEMP-Ordner entpacken, dort
  `update-wizard.bat` als Admin starten (GUI-Wizard). Schritte: Backup -> Dienst
  stoppen -> robocopy neuer Dateien in den Install-Ordner -> Dienst starten
  (Alembic-Migrationen laufen automatisch). Headless: `update-wizard.ps1 -Headless`.
  Der Wizard erkennt Install-/Daten-/Config-Pfad aus `HKLM\SOFTWARE\Zettelwirtschaft`
  und verweigert den Start aus dem Install-Ordner heraus.
- **Backup manuell**: Startmenue "Backup jetzt" oder `backup.bat` -> ruft
  `zettelwirtschaft-backend.exe --config <config.toml> --backup` (Datenbank, als ZIP in
  `<DataDir>\data\backups`). `backup.bat /full` schliesst die Dokumente mit ein
  (`--full`). Offline ueber die Exe, nicht ueber HTTP — PIN-by-default wuerde die API
  blocken. Zusaetzlich laeuft der in-process Auto-Backup taeglich (DB-only, kein
  Windows-Scheduled-Task noetig). Hinweis: die DB-only-Backups sichern NICHT die
  archivierten Dateien — fuer ein vollstaendiges Backup `/full` nutzen oder den
  Datenordner separat sichern (NAS/Cloud-Sync).
- **Restore**: `restore-backup.bat <backup.zip>` (Admin) -> Dienst stoppen, `-wal/-shm`
  bereinigen, DB (+ optional Dokumente bei `--full`-Backups) aus dem ZIP zurueckspielen,
  Dienst starten. Danach ggf. Vektor-Index neu aufbauen (Einstellungen -> Wartung).
- **Backup vor Deinstallation**: der Uninstaller erstellt automatisch ein Sicherungs-Backup
  nach `%USERPROFILE%\Documents\Zettelwirtschaft-Backups` (via `--backup --out-dir`,
  bewusst AUSSERHALB des Datenordners — sonst loescht das optionale "Daten loeschen?" das
  Backup gleich wieder), bevor er Dienst und Programmdateien entfernt.

**Netzwerkordner / Scanner-Freigabe** (Native, ab v1.7, `planung/13-netzwerk-ordner.md`):

- **(A) NAS als Watch-/Export-Ordner**: In der Web-UI `\\server\freigabe\ordner` mit Benutzer
  und Passwort der Freigabe eintragen. Das Backend verbindet sich selbst
  (`core/network_share.py`: `WNetAddConnection2W`, `CONNECT_TEMPORARY`, ohne Laufwerksbuchstaben).
  Das Passwort liegt als Fernet-Token in `SystemSetting`. Der Watch-Ordner wird per
  `PollingObserver` überwacht und verbindet sich mit Backoff neu (30 s bis 15 min). Die
  Erreichbarkeit steht in Health unter `watch_folder`/`export_folder`. Netzlaufwerke (`V:`)
  werden mit UNC-Vorschlag abgelehnt, `subst`-Laufwerke auf ihr Ziel umgeschrieben.
  `ALLOW_NETWORK_FOLDERS` ist nur noch der Experten-Override für UNC ohne Zugangsdaten.
- **(B) Scanner-Freigabe**: Checkbox im Setup oder Startmenü „Scanner-Freigabe einrichten“
  (`freigabe-einrichten.bat/.ps1`, Admin). Legt an: lokales Konto `zw-scanner`, SMB-Freigabe
  `Zettelwirtschaft-Eingang` (nur Ändern für dieses Konto), eingeschränkte NTFS-Rechte und eine
  Firewall-Regel (TCP 445, LocalSubnet, Privat). Entfernen mit `-Remove`, das ruft auch der
  Uninstaller auf. Getestet nur per `-DryRun` in `scripts/test-scripts.ps1`; nie echt auf dem
  Entwickler-PC ausführen.
- Tests: Die WNet-/Registry-API ist in allen Backend-Tests gefälscht (`tests/fake_share.py`,
  autouse in `conftest.py`). Kein Test baut echte SMB-Verbindungen auf.

## Memory Files — Read Before Working on a Topic

| File | Read when working on… |
|---|---|
| [`memory/backend-patterns.md`](C:/Users/manue/.claude/projects/E--claude-zettelwirtschaft/memory/backend-patterns.md) | Python/FastAPI, SQLAlchemy, OCR pipeline, LLM calls, API endpoints, tests |
| [`memory/data-models.md`](C:/Users/manue/.claude/projects/E--claude-zettelwirtschaft/memory/data-models.md) | DB models, migrations, enums, archive structure, version tracking |
| [`memory/frontend-patterns.md`](C:/Users/manue/.claude/projects/E--claude-zettelwirtschaft/memory/frontend-patterns.md) | Vue.js components, views, TailwindCSS, API client, PWA |
| [`memory/release-deployment.md`](C:/Users/manue/.claude/projects/E--claude-zettelwirtschaft/memory/release-deployment.md) | Releases, Docker, Windows installer, CI/CD, known deployment bugs |
| [`memory/e2e-tests.md`](C:/Users/manue/.claude/projects/E--claude-zettelwirtschaft/memory/e2e-tests.md) | Playwright E2E tests, mocking strategy, CI setup |

---

## Technologie-Stack

- **Backend:** Python 3.13 (Docker `python:3.13-slim` = Native-Build-venv) / FastAPI 0.143 /
  Starlette 1.8 / SQLAlchemy 2.1 async / Pydantic v2 (2.14) / pydantic-settings 2.16
- **Datenbank:** SQLite (via SQLAlchemy + Alembic, Migrationen via `migrate.py`)
- **OCR:** Tesseract OCR + pdf2image + pdfplumber (digitale PDFs), Pillow 13.x
  (Decompression-Bomb-Limit zentral in `app/core/image_safety.py:open_image`)
  - Pre-Processing: Auto-Upscale 2x bei kleinen Bildern, MedianFilter Denoising,
    aggressives Autocontrast (cutoff=2)
- **KI-Analyse:** Ollama (>= 0.30.3, getestet/Docker-Pin 0.40.5) mit genau einem Modell
  `OLLAMA_MODEL` (Default `gemma4:e2b-it-qat`, 4,3 GB, CPU ~40 s/Dok.; die Installer schlagen bei
  NVIDIA-GPU mit >= 10 GB VRAM `gemma4:12b-it-qat` vor — es gibt KEIN Laufzeit-Fallback-Modell).
  gemma4 "denkt" ohne `think:false` — `llm_service._chat` sendet `think` (=`OLLAMA_THINK`, Default
  false) an alle Modelle mit `thinking`-Capability bzw. unbekannten Capabilities.
  Hardware-Stufen der Installer: EINE Quelle `scripts/model-tiers.ps1` (dot-gesourct von
  install.ps1, install-gui.ps1, New-NativeConfig.ps1, Convert-Env-To-Config.ps1; ausgeliefert
  ueber release-manifest.txt bzw. build-native.ps1 + setup-native.nsi). VRAM per `nvidia-smi`
  (`Win32_VideoController.AdapterRAM` endet bei 4 GB), unbekannt -> e2b.
  - JSON-Schema-constrained Generation (Ollama 0.5+)
  - Few-Shot aus CorrectionMappings
  - Optional: LLM-Reranker + Verifier-Pass (config-flags)
- **Frontend:** Vue.js 3.5+ (Composition API, `<script setup>`) + Vite 8 + Tailwind v4
  + Pinia 4 (+ `@vue/devtools-api` 8 als Pflicht-Peer) + vue-router 5
- **App-Version im UI:** einzige Quelle ist die Repo-Datei `VERSION` (`vite.config.js` liest
  `../VERSION`; `frontend/package.json` hat bewusst kein `version`-Feld). Docker-Build bekommt
  `VERSION` ueber den benannten Build-Context `repo` (compose `additional_contexts`,
  release.yml `build-contexts`, ci-local `--build-context repo=.`) → braucht Compose ≥ 2.17/BuildKit.
- **Deployment:**
  - **Docker** (Stand v1.2.x, Standard heute): Backend, Frontend/Nginx, Ollama, ChromaDB 1.x als Container
    - Container-Hardening: cap_drop ALL, no-new-privileges
    - Frontend nginx als non-root, Listen 8080
    - Backend nur intern erreichbar (expose, kein host-port-binding)
    - ChromaDB in dediziertem internem Netz (`chromadb-net`, `internal: true`)
  - **Native Windows** (ab v1.3): Setup.exe → NSSM-Service `ZettelwirtschaftBackend`
    - PyInstaller-Onedir-Bundle, Frontend via FastAPI `StaticFiles`
    - **ChromaDB embedded** (`PersistentClient`, kein HTTP-Service)
    - Tesseract + poppler gebundled in `<install>/bin/`
    - Ollama separat installiert (offizieller Installer = Tray-App, kein Dienst; siehe Native-Service-Ops)
    - Konfiguration in `config.toml` statt `.env` (Pfad via `ZETTELWIRTSCHAFT_CONFIG`)
- **Smartphone:** PWA (Progressive Web App, vite-plugin-pwa 2.x)
- **Vektor-Suche:** ChromaDB 1.5.x + Ollama-Embeddings (Default `qwen3-embedding:4b`, 2560 dim;
  bei < 12 GB RAM schlagen die Installer `qwen3-embedding:0.6b` vor) + Hybrid Search FTS5+Vector
  mit RRF. qwen3-embedding* braucht fuer ANFRAGEN einen Instruct-Prefix (Dokumente nicht):
  einzige Stelle `embedding_service.query_prefix`/`embed_query` (RAG-Retrieval).
- **Rate-Limiting:** globale FastAPI-Dependency `core/rate_limit.global_rate_limit` auf Basis
  von `limits` (200/min pro IP; die fruehere slowapi-Middleware griff ab FastAPI 0.143 nicht mehr).
  X-Real-IP nur im Docker-Betrieb und nur von `TRUSTED_PROXIES` (Default `172.16.0.0/12`;
  docker-compose setzt das feste Proxy-Netz `ZW_PROXY_SUBNET`, Standard `172.31.250.0/24`)
- **Betriebsart:** `settings.deployment_mode` (`native` | `docker` | `dev`) — EINZIGE Ableitung
  in `config.py` (native = frozen Bundle oder `FRONTEND_DIST_DIR` vorhanden, docker =
  `/.dockerenv`, sonst dev). Ausgeliefert in `GET /api/system/health` und den Ordner-Settings;
  Trusted-Proxies, Ordner-Settings und UI-Hinweise richten sich danach.
- **CI:** Lokal via `scripts/ci-local.{ps1,sh}`, GitHub-Actions deaktiviert

## Projektstruktur

```
zettelwirtschaft/
  backend/
    app/
      main.py                    # FastAPI-App, CORS, Startup, FTS5-Init
      config.py                  # Pydantic Settings (ENV > config.toml > .env), deployment_mode
      database.py                # SQLAlchemy Engine, Session, Base
      models/
        document.py              # Document, Tag, DocumentTag, Enums (DocumentType, DocumentStatus, ReviewStatus, TaxCategory)
        filing_scope.py          # FilingScope (Ablagebereiche: Privat, Praxis etc.)
        processing_job.py        # ProcessingJob + JobStatus/JobSource Enums
        warranty_info.py         # WarrantyInfo + WarrantyType Enum
        review_question.py       # ReviewQuestion (erweitert: question_type, explanation, suggested_answers, priority)
        audit_log.py             # AuditLog + AuditAction Enum
        saved_search.py          # SavedSearch
        notification.py          # Notification + NotificationType Enum
        correction_mapping.py    # CorrectionMapping (Lerneffekt aus Korrekturen)
        chat_message.py          # ChatMessage (RAG-Chat-Verlauf)
        email_account.py         # EmailAccount + ScheduleType Enum, ProcessedEmail-Import
        processed_email.py       # ProcessedEmail + EmailStatus Enum
        background_task.py       # BackgroundTask + TaskStatus (lang laufende Vorgaenge, 202 + Polling)
      schemas/
        document.py              # DocumentResponse, DocumentListItem, DocumentUpdate, TagResponse, DashboardStats etc.
        filing_scope.py          # FilingScopeCreate, FilingScopeUpdate, FilingScopeResponse
        processing_job.py        # JobStatusResponse, UploadResponse
        search.py                # SearchResponse, SearchResultItem, SearchFacets, SuggestResponse, SavedSearchResponse
        tax.py                   # TaxYearSummary, TaxExportRequest, TaxExportValidation
        warranty.py              # WarrantyListItem, WarrantyUpdate, WarrantyStats
        notification.py          # NotificationResponse, NotificationCount
        chat.py                  # ChatRequest, ChatResponse, ChatMessage schemas
        email.py                 # EmailAccountCreate/Update/Response, EmailTestResult, EmailFetchResult, ProcessedEmailResponse, EmailStatsResponse
        background_task.py       # TaskResponse (202-Antwort + GET /api/tasks/{id})
      api/
        auth.py                  # PIN-Login, Session-Status, Logout (in-memory Sessions)
        documents.py             # CRUD + Upload + Tags + Stats + Thumbnails
        filing_scopes.py         # Ablagebereich-CRUD (Privat, Praxis etc.)
        search.py                # Volltextsuche + Autocomplete + SavedSearch
        health.py                # Health-Check
        jobs.py                  # Processing-Job-Status (kommagetrennte Status-Filter)
        tax.py                   # Steuerpaket-Export + Summary + Validation
        warranties.py            # Garantie-Liste + Stats + Update
        notifications.py         # Benachrichtigungen + Mark-Read
        review.py                # Erweitertes Rueckfrage-System + Approve + Stats
        system.py                # System-Health + Backup + Wartung + Vektor-Index Rebuild
        chat.py                  # RAG-Chat (POST /api/chat, GET/DELETE /api/chat/history)
        email.py                 # E-Mail-Konten CRUD + Test + Fetch + History + Stats
        tasks.py                 # GET /api/tasks/{id}, /api/tasks — Status der Background-Tasks
      services/
        upload_service.py        # Datei-Upload-Verarbeitung
        file_validation_service.py # Dateityp- und Magic-Byte-Validierung
        queue_worker_service.py  # Queue-Worker (PENDING -> PROCESSING -> COMPLETED/NEEDS_REVIEW/FAILED)
        thumbnail_service.py     # Thumbnail-Generierung (Pillow/pdf2image)
        watch_folder_service.py  # Watch-Ordner-Ueberwachung (watchdog)
        ocr_service.py           # OCR: pdfplumber (digital) + Tesseract (Scans/Bilder)
        llm_service.py           # Ollama /api/chat: JSON-Schema-Analyse (chat_json) + Freitext (chat_text), Prompt-Templates
        analysis_service.py      # Dokumentenanalyse-Pipeline (OCR -> LLM -> AnalysisResult)
        archive_service.py       # Datei-Archivierung + DB-Eintrag + Tags + FTS-Index + Scope-Zuweisung
        search_service.py        # FTS5 Volltextsuche + Facetten + Autocomplete
        tax_export_service.py    # Steuerpaket-Export (ZIP + PDF via reportlab + CSV)
        warranty_reminder_service.py # Garantie-Erinnerungen (90/30/0 Tage)
        backup_service.py        # Backup-Service (DB, optional + Dokumente `--full`; Auto-Backup taeglich, DB-only)
        embedding_service.py     # Ollama /api/embed (Batch) ueber ollama_client.post_json
        vectorize_service.py     # Chunking (Satzgrenzen), Vektorisierung bei Archivierung
        rag_service.py           # RAG-Pipeline (Embed -> ChromaDB-Retrieve -> LLM-Generate)
        crypto_service.py        # Fernet-Verschluesselung fuer E-Mail-Passwoerter
        email_relevance_service.py # LLM-basierte E-Mail-Relevanzpruefung
        email_fetch_service.py   # IMAP-Abruf, E-Mail-Parsing, Job-Erstellung
        email_scheduler_service.py # Background-Task: CRON/IDLE-Scheduling fuer E-Mail-Abruf
        ollama_client.py         # Gemeinsamer Ollama-HTTP-Client, Modell-Check/-Pull, typisierte Fehler
        task_service.py          # start_task/TaskContext: Background-Tasks (Re-Analyse, Mail-Abruf, Backup, Vektor-Rebuild)
        correction_service.py    # CorrectionMapping zaehlen + auto_apply (ab 3x) nach der LLM-Analyse
        notification_service.py  # REVIEW_NEEDED / SYSTEM-Benachrichtigungen aus dem Worker
        reanalysis_service.py    # Re-Analyse ueber die Haupt-Pipeline, user-editierte Felder bleiben
        review_answers.py        # Antworten auf Rueckfragen parsen + anwenden (AnswerError -> 422, Frage bleibt offen)
        filing_scope_service.py  # gemeinsame Ablagebereich-Logik (API + Review-Antwort "NEU: ...")
        file_relocation.py       # Archivdateien bei Scope-Wechsel verschieben (copy -> commit -> delete)
        tax_year.py              # Steuerjahr ableiten, wenn tax_relevant gesetzt ist
      core/
        file_utils.py            # Dateinamen-Sanitizing, Dateityp-Registry (Magic-Bytes, MIME, tif->tiff), UUID-Prefix
        db_types.py              # UTCDateTime: alle Zeitstempel naiv-UTC gespeichert, aware-UTC gelesen (API mit Offset)
        date_utils.py            # parse_date_lenient (ISO/deutsche Formate), add_months
        normalize.py             # Enum-Sets aus den Models, Steuerkategorie-/Waehrungs-Normalisierung
        query_params.py          # gemeinsame Query-Parameter-Parser (Dokumenttypen, leere Werte)
        rate_limit.py            # globales Rate-Limit (limits) + trusted_client_ip (Proxy nur im Docker-Betrieb)
        request_limits.py        # ASGI-Body-Limit pro Route (413): Upload MAX_UPLOAD_SIZE_MB + 5 MB, sonst 256 KiB, /api/auth/* 4 KiB
        install_dir.py           # native_install_dir(): <install> aus sys.executable (nur PyInstaller-Bundle)
        sqlite_migration.py      # Idempotenz-Helfer fuer Rebuild-Migrationen
        text_utils.py            # Slugs + Dateinamen mit deutscher Transliteration (ä -> ae)
      prompts/                   # LLM-Prompt-Templates (Textdateien, inkl. rag_answer.txt, email_relevance.txt)
    alembic/                     # DB-Migrationen (siehe ### Alembic-Migrationen)
    requirements.txt
    Dockerfile
  frontend/
    src/
      components/
        layout/                  # AppLayout, Sidebar, AppHeader, BottomNav (Mobile)
        common/                  # StatCard, Pagination, ConfirmDialog, DocTypeBadge, ToastContainer
        email/                   # EmailAccountForm, EmailAccountList (E-Mail-Konten-Verwaltung)
        settings/                # SystemHealth, MaintenanceActions, FilingScopeManager, FolderSettings, EmailSettings
      constants/
        documentTypes.js         # DocumentType Enum -> Label Mapping
      utils/
        formatters.js            # formatDate, formatCurrency Utilities
      views/
        PinLoginView.vue         # PIN-Eingabe bei aktiviertem PIN-Schutz
        DashboardView.vue        # Statistik-Karten, letzte Dokumente, Quick-Upload
        DocumentsView.vue        # Tabellarische Liste mit Filtern und Sortierung
        DocumentDetailView.vue   # Zwei-Spalten-Layout: Vorschau + Metadaten-Formular
        UploadView.vue           # Drag-and-Drop Upload mit Fortschritt
        ReviewView.vue           # Erweitertes Review mit Wizard-Cards und Auto-Update
        SearchView.vue           # Volltextsuche mit Facetten und gespeicherten Suchen
        TaxView.vue              # Steuerbelege-Dashboard mit Kategorien + ZIP-Export
        WarrantyView.vue         # Garantie-Dashboard mit Status-Filter + Fortschrittsbalken
        ScanView.vue             # Kamera-Scan mit Aufnahme + Vorschau + Upload
        SettingsView.vue         # System-Health + Backup + Wartung + Ablagebereiche + E-Mail-Konten
        ChatView.vue             # RAG-Chat mit Verlauf, Scope-Filter, Beispielfragen
      services/api.js            # Zentraler API-Client (Axios)
      router/index.js            # Vue Router
      stores/                    # Pinia Stores (notifications, auth) — documents-Store entfernt in Phase 5 (toter Code)
      composables/               # useFilingScopes (shared cache + inflight-Token), useBackgroundTask (202 + Polling)
    vite.config.js
    tailwind.config.js
    nginx.conf                   # SPA-Routing + API-Proxy
    Dockerfile                   # Multi-Stage: Node Build + Nginx
  e2e/
    tests/                       # NN-<bereich>.spec.ts (Smoke, Auth, Upload, Documents, Detail, Review, Search, Tax, Warranty, Chat, Settings, Responsive, Scan, Hilfe/Onboarding, ...)
    helpers/mock.helpers.ts      # Zentrale Mock-Daten + Setup-Funktionen (API-Catch-all, PIN-401-Gate)
    helpers/api-types.ts         # Typen der Backend-Antworten, gegen die die Mocks geprueft werden
    fixtures/test-files/         # Testdateien (PDF, PNG, TIFF)
    playwright.config.ts         # 2 Projekte: chromium (Desktop) + mobile (Pixel 5), SW blockiert, eigener Preview-Port
  docker-compose.yml
  .env.example
  install.bat                    # Windows-Installer Einstiegspunkt (GUI bevorzugt, CLI als Fallback)
  install-gui.ps1                # Grafischer Windows-Installer (Windows Forms, 4-Schritt-Wizard)
  install.ps1                    # PowerShell-CLI-Installationsassistent (Fallback)
  generate-mounts.ps1             # Generiert docker-compose.override.yml aus .host-mounts.json
  start.bat                      # System starten (ruft generate-mounts.ps1) + Browser oeffnen
  stop.bat                       # System stoppen
  update.bat                     # System updaten (mit Backup)
  uninstall.bat                  # System deinstallieren
  .github/
    workflows/
      ci.yml.disabled            # deaktiviert — CI laeuft lokal (scripts/ci-local.*)
      release.yml                # Release: Tag v* -> GitHub Release + GHCR Images
  planung/                       # Anforderungsdokumente und Prompts
```

## Architektur-Entscheidungen
> Read `memory/backend-patterns.md` + `memory/data-models.md` before working on backend/architecture topics.

- **Kein externer Message-Broker:** Verarbeitungs-Queue ist datenbankbasiert (SQLite). Kein Redis/RabbitMQ noetig.
- **Optionaler PIN-Schutz:** `PIN_ENABLED=true` + `PIN_CODE=xxxx` in `.env`. Sessions in-memory (dict), kein DB-Schema. Middleware in `main.py` prueft Cookie, Whitelist: `/api/health`, `/api/auth/*`. Frontend: Router-Guard + 401-Interceptor redirecten zu `/pin`.
- **SQLite FTS5** fuer Volltextsuche statt Elasticsearch.
- **LLM-Prompts als Textdateien** unter `backend/app/prompts/`, nicht hardcoded.
- **Synchrone Verarbeitung:** Ein Dokument gleichzeitig (Heim-Hardware).
- **Soft-Delete** fuer Dokumente (Status `DELETED`, nicht physisch loeschen). Kein
  Papierkorb/Restore: die UI verspricht keine Wiederherstellung. Der Duplikat-Check
  ignoriert DELETED — ein erneuter Upload derselben Datei legt ein NEUES Dokument an
  (Eindeutigkeit von `file_hash` nur unter nicht geloeschten Dokumenten).
- **Archiv-Ordnerstruktur:** `data/archive/{scope_slug}/{jahr}/{monat}/{document_type}/`
- **Dateinamen im Archiv:** UUID-Prefix + bereinigter Originalname.
- **Kombinierter LLM-Prompt als Primaerstrategie:** Ein Ollama-Aufruf fuer Klassifikation + Metadaten + Steuer + Garantie. 4 Einzel-Prompts als Fallback bei JSON-Parse-Fehler.
- **OCR-Strategie fuer PDFs:** Zuerst pdfplumber (digitaler Text, Konfidenz 1.0), dann Tesseract (Scans via pdf2image).
- **Wasserfall-Degradation:** OCR-Fehler -> NEEDS_REVIEW | LLM-Fehler -> NEEDS_REVIEW mit OCR-Text | Niedrige Konfidenz -> NEEDS_REVIEW mit allen Daten | Erfolg -> COMPLETED.
- **FTS5 Standalone-Tabelle:** Eigene FTS5-Tabelle mit `doc_id` statt content-sync (zuverlaessiger mit async SQLAlchemy). Index wird bei Archivierung aktualisiert.
- **Tag-Zuweisung via Junction-Table:** Tags werden ueber DocumentTag-Eintraege zugewiesen (nicht ueber Relationship-Assignment), um MissingGreenlet in async-Kontext zu vermeiden.
- **Frontend: TailwindCSS:** Utility-first CSS ohne Component-Library-Overhead. Custom `btn`, `input`, `badge`, `card` Klassen.
- **Ablagebereiche (Filing Scopes):** Dokumente werden Ablagebereichen zugeordnet. Nur "Privat" als Default-Scope. Weitere Scopes werden vom Benutzer in den Einstellungen angelegt. Zuweisung: 1. Keyword-Match im OCR-Text (Prioritaet), 2. LLM-Zuweisung (Konfidenz >= 0.7), 3. Default-Scope Fallback. Bei unsicherer Zuordnung: ReviewQuestion mit field_affected="filing_scope".
- **TaxCategory Enum-Storage:** `values_callable` fuer SQLAlchemy Enum, damit Enum-Values (z.B. "Werbungskosten") statt Names (z.B. "WERBUNGSKOSTEN") in SQLite gespeichert werden. LLM-Compound-Werte (z.B. "A | B") werden auf den ersten gueltigen Wert reduziert.
- **Frontend-Port konfigurierbar:** `${FRONTEND_PORT:-8080}` in docker-compose.yml. Default 8080 statt 80.
- **GPU-Support optional:** `docker-compose.yml` hat KEINE GPU-Section. Erkennt der Installer eine NVIDIA-GPU, schreibt er eine `docker-compose.override.yml` mit der NVIDIA-Reservation fuer den Ollama-Container.
- **Update mit Backup:** `update.bat` erstellt lokale Sicherheitskopie der DB + .env vor jedem Update. Bricht ab wenn DB-Backup fehlschlaegt.
- **RAG-Pipeline:** Dokumente werden bei Archivierung vektorisiert (Chunking mit Satzgrenzen-Erkennung, Metadaten-Chunks). Vektorisierungsfehler blockieren Archivierung nicht (graceful degradation). Retrieval per Cosine-Similarity aus ChromaDB.
- **call_llm_text():** Freitext-LLM-Antworten ohne JSON-Format (fuer RAG-Antwortgenerierung).
- **ChromaDB als Vektor-Store:** Docker: separater Service; Native: embedded. EINE Collection pro Embedding-Modell (`documents_<modell>`, z.B. `documents_qwen3-embedding_4b`), Ablagebereich als Metadata-Filter (`filing_scope_id`). Modellwechsel: neue Collection ist leer -> `run_initial_vectorize` baut den Index beim naechsten Start automatisch neu auf (Hintergrund-Vorgang `rebuild_vectors`, Test `TestEmbeddingModelSwitch`) — erst wenn das Embedding-Modell installiert ist (`wait_for_embedding_model`: der Auto-Pull laeuft parallel; ohne Warten schluege jedes Dokument fehl und ein danach archiviertes Dokument machte die Collection nicht-leer -> Altbestand fehlte dauerhaft). Die Collection des alten Modells bleibt liegen (Speicherplatz, kein Fehler) — es gibt keine Auto-Bereinigung. Achtung beim Zurueckwechseln: die alte Collection ist nicht leer -> KEIN Auto-Rebuild, zwischenzeitlich archivierte Dokumente fehlen darin -> Vektor-Index in der Wartung neu aufbauen.
- **Modell-Defaults vs. Bestandsinstallationen (v1.6):** Neue Defaults gelten nur fuer Neuinstallationen. Native-`config.toml` und Installer-`.env` (seit 2026-05) enthalten die Modelle explizit. Docker-`.env` von vor 2026-05 hatten kein `EMBEDDING_MODEL` -> `Add-ZwLegacyModelDefaults` (`scripts/model-tiers.ps1`) traegt fehlende `OLLAMA_MODEL`/`EMBEDDING_MODEL` mit den alten Defaults ein, aufgerufen von `install-gui.ps1` (Update, Phase-Backup) und `update.bat` — jeweils VOR `docker compose up` (env_file wird nur beim Erstellen des Containers gelesen), nur bei installierter Version < 1.6.0 bzw. unbekannter Version (`data/.version`). `install.ps1` schlaegt bei vorhandener `.env` deren Modelle vor. Nicht abgedeckt: reines `git pull` + `docker compose up` ohne `update.bat`.
- **Ollama-Versionscheck:** `ollama_client.ollama_health` fragt `/api/version` ab; aelter als `OLLAMA_MIN_VERSION` (0.30.3) -> Health-`warning` "Ollama <v> ist älter als <min> — bitte aktualisieren" (nie `error`), Version + Mindestversion im System-Health (`components.ollama.version/min_version`). Dev-Builds ("0.0.0") und fehlende Version -> keine Warnung. Gleicher Wert in `scripts/model-tiers.ps1` und `setup-native.nsi` (Test `test_ollama_min_version_is_consistent`). Quelle: GitHub-Release-Notes (gemma4 ab v0.20.0, gemma4:12b ab v0.30.3); fuer die QAT-Tags und qwen3-embedding nennt keine Release-Note eine Version.
- **E-Mail-Integration (Issue #18):** IMAP-Polling-Service als Backend Background-Task. Importiert werden bewusst nur UNGELESENE Mails (`UNSEEN`) — so steht es auch in den E-Mail-Einstellungen der UI. LLM entscheidet ueber E-Mail-Relevanz. Relevante Anhaenge + Body werden als ProcessingJobs in bestehende Pipeline eingespeist. Passwoerter Fernet-verschluesselt (AES-128-CBC, `EMAIL_ENCRYPTION_KEY` in `.env`). Scheduling: CRON (via croniter, ausgewertet in der lokalen Zeitzone `TIMEZONE`, Default `Europe/Berlin`, `tzdata` fuer Windows), MANUAL (API-Trigger), IDLE (alle 5 Minuten). Verarbeitete E-Mails werden in IMAP-Ordner verschoben. Konfiguration ueber Web-UI (SettingsView).
- **Watch-Ordner:** Originale werden nur im app-verwalteten Standard-Watch-Ordner nach der Verarbeitung geloescht. Bei einem vom Benutzer gewaehlten Ordner werden sie in den Unterordner `verarbeitet` verschoben (wird angelegt) — Benutzerdateien werden nie geloescht. Abgelehnte Dateien landen in `<watch_dir>/rejected`; Temp-/Systemdateien (`.tmp`, `.part`, `.crdownload`, `~$…`, versteckte) werden ignoriert.
- **Lang laufende Vorgaenge** (Re-Analyse `reanalyze`, manueller E-Mail-Abruf `email_fetch`, Backup `backup`, Vektor-Index-Rebuild `rebuild_vectors` — auch die initiale Vektorisierung beim Start): HTTP 202 + `TaskResponse`, Status per `GET /api/tasks/{id}`; Frontend pollt ueber `useBackgroundTask`. Pro (kind, key) hoechstens ein aktiver Task (409 mit dem laufenden Task). `work()` oeffnet eigene kurze Sessions — nie eine Schreib-Transaktion ueber OCR/LLM/IMAP halten.
- **SQLite-Locking:** `busy_timeout` pro Connection (`database.SQLITE_BUSY_TIMEOUT_MS`), kein WAL. Der Worker-Heartbeat committet in eigener kurzer Transaktion.
- **Review-Aktionen:** "Später" aendert den Review-Status nicht (Zurueckstellen nur clientseitig); "So übernehmen" akzeptiert das KI-Ergebnis (REVIEWED/OK); "Freigeben" ist auch mit offenen Fragen erlaubt (explizite Annahme).
- **Re-Analyse:** nutzt die Haupt-Pipeline (inkl. OCR-Sanitizer + Verifier). Vom Benutzer editierte Felder (AuditLog UPDATED) bleiben erhalten, alle anderen KI-Felder werden komplett ersetzt (Auto-Tags, Garantie, Scope sofern nicht user-gesetzt); unbeteiligte offene Fragen bleiben stehen.
- **PWA Service Worker Caching:** `navigateFallbackDenylist: [/^\/api\//]` verhindert dass der SW API-Requests mit `index.html` beantwortet. File/Thumbnail-Endpunkte sind `NetworkOnly` (Binaerdaten duerfen nicht gecacht werden, da sonst iframes die App statt das Dokument laden). API-JSON-Responses sind `NetworkFirst` mit 5min Cache.
- **Lizenz:** AGPL-3.0 (verhindert proprietaere SaaS-Forks).
- **PIN Rate-Limiting:** In-Memory Per-IP-Lockout mit exponentiellem Backoff (30 s, 60 s, ... max. 1 h; Counter erst 24 h nach Lockout-Ende vergessen) + globales Budget (30 Fehlversuche/h ueber alle IPs -> Login fuer Nicht-Loopback gesperrt + SYSTEM-Notification). Host-/Origin-Pruefung gegen DNS-Rebinding/CSRF: `core/host_guard.py`. Docker Desktop kann die Quell-IP verlieren (dann ein gemeinsamer Lockout fuer alle Geraete) -> einmalige Log-Warnung. `secrets.compare_digest` fuer constant-time Vergleich. Client-IP ueber `core/rate_limit.trusted_client_ip` — `X-Real-IP` von nginx nur im Docker-Betrieb aus `TRUSTED_PROXIES`, Native/Dev nutzen die Socket-Adresse.
- **CORS:** bewusst KEINE CORSMiddleware (BR-03) — die SPA ist immer same-origin (nginx-Proxy / Native-StaticFiles / Vite-Dev-Proxy). Die alte Regex `localhost:*`+RFC1918 mit `allow_credentials` gab anderen Port-Origins auf demselben Rechner (same-site -> bekommen das SameSite=strict-Cookie) Lesezugriff. CSRF: `core/host_guard.py` lehnt POST/PUT/PATCH/DELETE mit `Sec-Fetch-Site: cross-site|same-site` oder fremdem `Origin` ab (auch bei PIN aus).
- **DoS-Grenzen (AVAIL-01..07):** Body-Limit pro Route VOR dem JSON-Parsen (`core/request_limits.py`, nginx spiegelt: `/api/` 256k, eigene `location = /api/documents/upload` 55M — neue Upload-Routen in BEIDEN eintragen, Drift-Test `tests/core/test_request_limits.py`). Analysen (Queue-Worker + Re-Analyse) teilen einen prozessweiten Slot (`analysis_service.ANALYSIS_CONCURRENCY=1`), max. 3 aktive Re-Analysen (429, `start_task(max_active=...)`). VACUUM/FTS-Rebuild exklusiv (409). Backups pruefen freien Platz (+512 MB Reserve) und rotieren auch manuell. `/api/health`-Ollama-Probe single-flight, DB-Verbindung vor der Probe freigegeben. E-Mail-Parsing in `to_thread`, max. 20 Anhaenge/1000 MIME-Teile/1 MB HTML+Body pro Mail. Geteilter NAT-Schluessel (Docker Desktop) -> Per-Key-Lockout max. 60 s.
- **Security-Header Native:** `core/security_headers.py` setzt CSP/X-Frame-Options/nosniff/Referrer-Policy nur bei `deployment_mode == "native"` (Docker: nginx). Werte identisch mit `frontend/nginx-common.conf` — Drift-Test `tests/core/test_browser_hardening.py`.
- **Nginx Security-Headers:** CSP, X-Frame-Options SAMEORIGIN, X-Content-Type-Options nosniff, server_tokens off. Locations mit eigenem `add_header` (Static-Assets, `/sw.js`) brauchen `add_header_inherit merge;` (nginx >= 1.29.3), sonst entfallen dort alle server-weiten Security-Header (nginx-Vererbungsregel).
- **App-Version im Backend:** einzige Quelle `app/core/version.py:read_app_version()` (CWD-`VERSION` > `data/.version` > Native `<install>/VERSION` relativ zur Exe — NSSM-CWD ist `<install>/backend`).
- **Backup-Sicherheit:** `.env` wird nicht mehr in Backup-ZIPs aufgenommen (enthaelt Secrets). Path-Traversal-Schutz bei Backup-Download via `resolve()` + `is_relative_to()`.
- **E-Mail-Encryption-Key:** `EMAIL_ENCRYPTION_KEY` muss in `.env` gesetzt sein. Leerer Key -> HTTP 503 bei Account-Erstellung (keine stille Key-Generierung).
- **IMAP async:** Alle blockierenden IMAP-Operationen in `asyncio.to_thread()` gewrappt.
- **LLM-Service Deduplizierung:** `chat_json()`/`chat_text()` (und ihre Best-Effort-Varianten `call_llm()`/`call_llm_text()`) laufen ueber den gemeinsamen `llm_service._chat()`; HTTP-Zugriff fuer LLM und Embeddings einheitlich ueber `ollama_client.post_json()` (typisierte `OllamaError`).
- **Archive copy+delete:** Dateien werden kopiert und erst nach erfolgreichem DB-Commit geloescht (statt move, das nicht rollback-sicher ist).
- **SettingsView Sub-Components:** Aufgeteilt in SystemHealth, MaintenanceActions, FilingScopeManager, FolderSettings, EmailSettings.
- **Frontend Shared Utilities:** `constants/documentTypes.js` (Enum -> Label Mapping), `utils/formatters.js` (formatDate, formatCurrency).

## Wichtige Datenmodelle
> Read `memory/data-models.md` before working on models or migrations.

- `ProcessingJob` - Verarbeitungs-Queue (PENDING -> PROCESSING -> COMPLETED/NEEDS_REVIEW/FAILED). Felder: `ocr_text`, `ocr_confidence`, `analysis_result` (JSON), `email_account_id` (nullable FK). JobSource: UPLOAD, WATCH_FOLDER, EMAIL
- `Document` - Kerntabelle: Datei-Infos + KI-Metadaten (Typ, Titel, Datum, Betrag, Aussteller) + OCR-Text + Steuer + Status + filing_scope_id. Relationships: tags, warranty_info, review_questions, filing_scope (alle lazy="selectin")
- `FilingScope` - Ablagebereiche: name (unique), slug (unique), description, keywords (JSON-Liste), is_default, color (Hex). Slug auto-generiert (Umlaute -> ae/oe/ue/ss)
- `Tag` / `DocumentTag` - Schlagwort-System (automatisch + manuell), Many-to-Many ueber Junction-Table
- `WarrantyInfo` - Garantie-Informationen: Produkt, Kaufdatum, Ablaufdatum, Typ (LEGAL/MANUFACTURER/EXTENDED), Haendler
- `ReviewQuestion` - KI-Rueckfragen: Frage, Antwort, Feld, beantwortet-Status
- `AuditLog` - Aenderungsprotokoll (CREATED/UPDATED/DELETED/EXPORTED/TAG_ADDED/TAG_REMOVED/REVIEWED)
- `SavedSearch` - Gespeicherte Suchanfragen (Name + JSON-Parameter)
- `documents_fts` - FTS5 Virtual Table (title, ocr_text, issuer, summary, tags)
- `Notification` - Benachrichtigungen (WARRANTY_EXPIRING, WARRANTY_EXPIRED, REVIEW_NEEDED, SYSTEM). REVIEW_NEEDED/SYSTEM (Job fehlgeschlagen) erzeugt der Queue-Worker; PROCESSING_DONE wurde entfernt (nie gespeichert, VARCHAR ohne CHECK)
- `CorrectionMapping` - Lerneffekt aus Benutzer-Korrekturen: ab 2x Few-Shot-Beispiel im Prompt, ab 3x `auto_apply` (deterministisch nach der LLM-Analyse, vor der Archivierung; `correction_service.apply_auto_corrections`)
- `BackgroundTask` - lang laufende Vorgaenge: kind, key, status (PENDING/RUNNING/SUCCEEDED/FAILED), progress, result/error; Neustart markiert offene Tasks als FAILED
- `ChatMessage` - RAG-Chat-Verlauf (question, answer, sources JSON, scope_filter, created_at)
- `EmailAccount` - E-Mail-Konten: name, imap_host/port, use_ssl, username, encrypted_password, folder_inbox/processed, schedule_type (CRON/MANUAL/IDLE), cron_expression, is_active, filing_scope_id (FK)
- `ProcessedEmail` - Verarbeitete E-Mails: email_account_id (FK), message_id (UNIQUE pro Account), subject, sender, received_at, status (RELEVANT/IRRELEVANT/FAILED), processing_job_id (FK)

## Dokumenttypen (Enum)

```
RECHNUNG, QUITTUNG, KAUFVERTRAG, GARANTIESCHEIN, VERSICHERUNGSPOLICE,
KONTOAUSZUG, LOHNABRECHNUNG, STEUERBESCHEID, MIETVERTRAG,
HANDWERKER_RECHNUNG, ARZTRECHNUNG, REZEPT, AMTLICHES_SCHREIBEN,
BEDIENUNGSANLEITUNG, SONSTIGES
```

## Verarbeitungs-Pipeline

```
Dokument-Eingang (Upload, Watch-Ordner oder E-Mail-Import)
  -> Validierung (Dateityp, Groesse, Magic-Bytes)
  -> Queue-Eintrag (PENDING)
  -> Thumbnail-Generierung (Pillow/pdf2image)
  -> OCR
     - PDF digital: pdfplumber (Konfidenz 1.0)
     - PDF Scan / Bild: _preprocess_for_ocr (Auto-Upscale 2x bei <1500 px,
       MedianFilter Denoising, Autocontrast cutoff=2) -> Tesseract deu+eng
  -> Text kuerzen (max 4000 Zeichen: erste 2000 + letzte 2000)
  -> Few-Shot-Builder
     - Top-8 haeufigste User-Korrekturen aus CorrectionMappings
       (occurrence_count >= 2) als Beispiele in den Prompt injizieren
  -> LLM-Analyse (Ollama /api/chat)
     1. Kombinierter Prompt mit JSON-Schema-constrained Generation
        (format=<schema>, garantiert konformer Output)
     2. Fallback: 4 sequentielle Einzel-Prompts (klassifizieren / Metadaten /
        Steuer / Garantie) — feuert nur noch bei JSON-Parse-Fehlern
     3. Optional Verifier-Pass (LLM_USE_VERIFIER=true): zweiter LLM-Call
        validiert die Felder bei confidence <= LLM_VERIFIER_THRESHOLD
  -> Sanity-Checks (analysis_service)
     - Amount > 50.000 EUR -> NEEDS_REVIEW (Anti-Prompt-Injection)
     - tax_category Whitelist (Pipe-Werte vom LLM aufgesplittet)
  -> Ergebnisse in ProcessingJob speichern (ocr_text, ocr_confidence, analysis_result)
  -> Konfidenz-Check gegen CONFIDENCE_THRESHOLD (0.7)
  -> Archivierung:
     - SHA-256 Hash berechnen + Duplikat-Check (FAILED bei Duplikat; die
       Upload-Kopie wird geloescht — sie ist identisch mit dem Archivdokument)
     - Filing Scope bestimmen (Keyword-Match > LLM (>=0.7) > Default)
     - Datei nach archive/{scope_slug}/{jahr}/{monat}/{typ}/ kopieren,
       erst nach DB-Commit aus Upload-Ordner loeschen (rollback-sicher)
     - Document-Eintrag + Tags + WarrantyInfo + ReviewQuestions + AuditLog erstellen
     - FTS5-Index aktualisieren
     - Vektorisierung mit EMBEDDING_MODEL (Default qwen3-embedding:4b, 2560 dim,
       ohne Prefix) -> ChromaDB-Collection documents_<modell>,
       filing_scope_id als Metadata
  -> Status: COMPLETED | NEEDS_REVIEW (+ review_questions) | FAILED
```

**RAG-Chat-Pipeline (Frage -> Antwort):**

```
User-Frage
  -> embed_query (EMBEDDING_MODEL; qwen3-embedding* mit Instruct-Prefix)
     + parallel _fts_top_doc_ids (FTS5 mit Scope-Filter)
  -> Vector-Search (ChromaDB top_k * 3, filing_scope_id-Where-Filter serverseitig)
  -> Reciprocal Rank Fusion (_apply_rrf): score = 1/(k+vec_rank) + 1/(k+fts_rank)
  -> Optional LLM-Reranker (RAG_USE_RERANKER=true): zweiter Pass mit
     Schema-Mode-Score 0-10, additiv mit RRF-Score, dann top_k schneiden
  -> Document-Filter (DELETED ausschliessen, Geister-Chunks verwerfen)
  -> Prompt mit <document_excerpts> + <user_question> Wrapping
  -> call_llm_text -> Antwort mit Quellen-Refs [Dok. N]
```

## Konventionen
> Read `memory/backend-patterns.md` (backend) and `memory/frontend-patterns.md` (frontend) before writing code.

### Backend (Python)
- Async wo sinnvoll (FastAPI async endpoints, httpx fuer Ollama)
- Type Hints durchgehend
- Pydantic fuer alle API-Schemas
- Konfiguration: `pydantic_settings` mit drei Quellen — ENV (Docker, Tests),
  `config.toml` via `ZETTELWIRTSCHAFT_CONFIG` (Native), `.env` (Dev).
  Priority: ENV > TOML > .env > secrets.
- Logging: Plaintext via `logging.basicConfig` (`%(asctime)s [%(levelname)s] %(name)s: %(message)s`), kein JSON
- Fehlerbehandlung: Graceful Degradation (Ollama nicht erreichbar -> NEEDS_REVIEW, nicht Absturz)
- LLM/Embedding: shared `httpx.AsyncClient` Singleton (`ollama_client._get_client`).
  Timeout pro Request, exponential Backoff (2/4/8s) nur bei Verbindungsfehlern/5xx
  (nicht bei Lese-Timeout/4xx).
- Decimal fuer Geldbetraege (`Document.amount`, Tax-Aggregation). Pydantic-Schemas
  serialisieren mit `@field_serializer` als float zurueck (API-Stabilitaet).

### Frontend (Vue.js)
- Composition API mit `<script setup>` (keine Options API)
- Pinia fuer State Management
- Alle UI-Texte auf Deutsch
- Responsive: Desktop (>1024px Sidebar), Tablet (768-1024px), Mobile (<768px Bottom-Nav)

### API-Design
- RESTful unter `/api/`
- Paginierung fuer Listen-Endpoints
- Konsistente Fehler-Responses
- Health-Check unter `/api/health`

### Docker
- Multi-Stage Dockerfiles (Build + Runtime)
- Non-root User im Container
- Healthchecks fuer alle Services
- Restart-Policy: `unless-stopped`
- Volumes: `./data` fuer Dokumente, benanntes Volume fuer Ollama-Modelle
- `chromadb-net` mit `internal: true` — ChromaDB nur fuer Backend-Container erreichbar

### Native-Windows (ab v1.3)
- **Build:** `pwsh scripts/build-native.ps1` (Voraussetzung: `tools/{tesseract,poppler,nssm}` vorbereitet, NSIS im PATH)
- **Tools-Bootstrap:** `pwsh scripts/fetch-build-tools.ps1` laed poppler+NSSM+Tesseract idempotent in `tools/`.
  Versionen, URLs und SHA-256 sind in `$BuildToolPins` fest gepinnt (INFRA-04: kein `latest`, keine Mirror-Aufloesung,
  tessdata vom Tag `4.1.0`); Tesseract-Setup wird per **7-Zip** entpackt (7-Zip muss vorhanden sein). `build-native.ps1`
  prueft `tools/` per `fetch-build-tools.ps1 -VerifyOnly` gegen die Pins und bricht bei Abweichung ab
  (`-AllowUnpinnedTools` nur lokal). Versions-Update = Pins + Hashes anpassen.
- **Programmordner-Rechte (INFRA-03):** `scripts/secure-install-dir.ps1` setzt `<install>` auf SYSTEM/Administratoren F,
  Benutzer RX (Setup vor dem Entpacken aus `$PLUGINSDIR`, Update-Wizard vor dem Backup aus dem Update-Paket). Setup
  installiert ausserhalb von `$PROGRAMFILES64` nur in neue/leere Ordner.
- **PyInstaller-Spec:** `backend/zettelwirtschaft.spec` (Onedir, kein UPX, kein onefile-Mode wegen Antivirus + Startup-Zeit). Bundle ist ~187 MB / 1842 Files.
- **Entrypoint:** `backend/app/entrypoint.py` mit `--config`, `--migrate-only`, `--version`. Bei `frozen` Bundle Alembic via Public-API (`alembic.command.upgrade`), kein subprocess.
- **bin_paths.py** wird in `main.py` SEHR FRUEH importiert (vor pdf2image/pytesseract), setzt PATH+TESSDATA_PREFIX+pytesseract.tesseract_cmd auf gebundeltes `<install>/bin/`. No-op im Dev/Docker.
- **ChromaDB embedded:** `_get_chroma_client` schaltet via `CHROMADB_MODE` zwischen `PersistentClient(path=...)` (native) und `HttpClient` (docker).
- **Frontend:** `app.mount("/assets")` + Catch-All-Route `@app.get("/{full_path:path}")` mit `FileResponse(index.html)` als Fallback (SPA-Routing fuer `/dokumente` usw.). Im Bundle IMMER `<install>/frontend` (`core.install_dir.frontend_dist_dir`, INFRA-01 — `FRONTEND_DIST_DIR` aus der config.toml wird dort ignoriert, sonst Dateiserver ohne PIN); Dev: `FRONTEND_DIST_DIR` gesetzt + existiert.
- **Service:** NSSM-Wrapper (`scripts/service-install.bat`), Log-Rotation 10 MB, `DependOnService Ollama` (nur wenn ein Dienst `Ollama` existiert), AutoStart beim Systemstart.
- **Migration aus Docker:** `scripts/Convert-Env-To-Config.ps1` mapt `.env`-Keys auf TOML.

**Kritische Build-Gotchas:**
- **chromadb vs chromadb-client:** PyInstaller braucht `chromadb` (full mit `PersistentClient`), NICHT `chromadb-client` (HTTP-only). Beide nutzen den `chromadb`-Namespace — Konflikt. `build-native.ps1` deinstalliert chromadb-client und installiert `chromadb` aus `requirements-build.txt`. Symptom bei Fehler: `RuntimeError: Chroma is running in http-only client mode`.
- **PRAGMA foreign_keys=ON:** siehe Alembic-Sektion oben. Ohne den Event-Listener sind FK-Constraints zur Laufzeit wirkungslos.
- **Fresh-DB-Init:** Migration 001 macht ALTER ohne CREATE. Docker (`migrate.py:main`) und Native (`entrypoint.py:_run_migrations`) nutzen dieselben Helfer aus `migrate.py`: `is_fresh_db` → `create_fresh_schema` (create_all) + Stamp auf head; danach `ensure_default_filing_scope` (seedet "Privat", das sonst nur Migration 005 anlegt).
- **App-Import im Bundle:** uvicorn startet per Import-String `"app.main:app"` — `app.main` steht deshalb explizit in den `hiddenimports` der Spec, sonst fehlen App + Router im Bundle.
- **SPA-Routing:** `StaticFiles(html=True)` deckt nur Ordner-Index ab. `/dokumente` wuerde sonst 404 geben — der Catch-All-Route ist notwendig.
- **ChromaDB Client/Server-Version synchron halten:** Bei einem `chromadb-client`-Bump (requirements.txt) MUSS das docker-compose-Server-Image `chromadb/chroma:<x>` mitgezogen werden (aktuell 1.5.9 ↔ client 1.5, seit v1.3.1). Weder Backend-Tests (volles/embedded chromadb im venv) noch E2E (API gemockt) fangen einen Skew — er trifft nur den Docker-Pfad zur Laufzeit.

## Qualitaetsprinzipien

1. **Einfachheit:** Technisch nicht versierte Nutzer muessen es bedienen koennen.
2. **Datenschutz:** Alle Daten lokal. Keine Cloud, keine Telemetrie, keine externen Aufrufe.
3. **Robustheit:** Fehlerhafte Scans blockieren nicht das System. Graceful Degradation.
4. **Performance:** Dokumentenanalyse unter 30 Sekunden auf 8 GB RAM Hardware.
5. **Keine Ueberentwicklung:** Nur implementieren was in den Anforderungen steht.

## Implementierungsstatus

Alle 11 urspruenglichen Spec-Prompts (Setup -> Rueckfrage-System) sind umgesetzt
und in `planung/*.md` dokumentiert. Darueber hinaus aktiv:

- **Filing Scopes** (Ablagebereiche): Keyword-Match > LLM (>=0.7) > Default
- **Windows-Installer** (`install.bat` + `install-gui.ps1` GUI-Wizard +
  `install.ps1` CLI-Fallback) inkl. Versionserkennung + Reparatur-Modus
- **PIN-Schutz** (opt-in) mit globalem Rate-Limit + Per-IP-Lockout + Trusted-Proxy-Filter
- **RAG-Assistent** mit Hybrid-Search FTS5+Vector + RRF + optional LLM-Reranker
- **E-Mail-Anbindung** (IMAP-Polling, LLM-Relevanzpruefung, Fernet-verschluesselte Passwoerter)
- **Auto-Migration** beim Start (`migrate.py` + Alembic-Chain, kein init_db mehr)
- **CI lokal** statt GitHub-Actions (`scripts/ci-local.{ps1,sh}`)
- **In-App-Update-Pruefung** (opt-in, v1.4.1): Manifest-Check + Banner
  (`update_service.py`, `system.py`, `UpdateCheck.vue`)
- **HTTPS** (v1.4.1): selbstsigniertes Zertifikat fuer Smartphone-Scan im LAN
  (`ssl/generate-self-signed-cert.py`, `docker-compose.ssl.yml`)
- **Native Update/Backup/Restore** (v1.4.1): GUI-Update-Wizard, `backup.bat`/`/full`,
  `restore-backup.bat`, Backup-before-uninstall (siehe `## Native-Service-Ops`)

**Abgeschlossene Review-/Hardening-Phasen** (aus PRs #24-#27 + Phasen 1-6 + Re-Review):

- 3 BLOCKER + 13 HIGH + 11 MEDIUM aus initialem Code-Review gefixt
- 7 von 8 SECURITY-Findings (NEW-001..009) aus alten Audits gefixt
- **Phasen 1-6 (internes Code-Review):** Top-7 Blocker + 12 High + 7 Mid +
  alle Re-Review-Findings bis Mid (R-01 PDF-Bug, K-2 PRAGMA foreign_keys=ON,
  K-3 Decimal-Serializer, R-06 Email-FAILED-Pfad, ChromaDB-Scope-Fallback,
  RAG-Prompt-Injection-Sanitizer, Heartbeat fuer Stuck-Job-Recovery,
  LLM-Service-Singleton-Client + Backoff, useFilingScopes-Composable,
  Migration 013 FK+Indexes, Decimal-Umstellung).
- **PIN-Schutz**: Default-PIN beim Install (auto-generierter 6-stelliger Code),
  UI-Banner via `pin_warning`-Flag wenn deaktiviert, Cookie `Secure`+`SameSite=strict`
- LLM-Pipeline: gemma4 + qwen3-embedding (v1.6, vorher qwen2.5 + bge-m3), JSON-Schema-Mode, Few-Shot, Hybrid-RRF, OCR-Preprocess,
  optional Reranker + Verifier
- Lib-Stand (Frontend/E2E aktualisiert in v1.6.0): Vite 8.3, vite-plugin-pwa 2.0,
  Vue 3.5.44, Pinia 4.1, vue-router 5.4, Tailwind 4.3.3, axios 1.20, Playwright 1.64,
  TypeScript 7 (e2e, nur `npm run typecheck`), Node 24 (Docker `node:24-alpine`,
  nginx `1.30-alpine`); Backend (v1.6.0): Python 3.13, FastAPI 0.143, Starlette 1.8,
  uvicorn 0.54, SQLAlchemy 2.1.5, alembic 1.20, pydantic-settings 2.16, Pillow 13.0,
  reportlab 5.0, cryptography 50, ChromaDB 1.5.9 (Client+Server), pyinstaller 6.22,
  pytest 9.1 / pytest-asyncio 1.4 (nur `requirements-dev.txt`), limits 5.x (statt slowapi),
  Ollama-Image gepinnt `0.40.5`.
  SQLAlchemy-2.1-Gotchas: `noload` deprecated (-> `raiseload`/`lazy="raise"`);
  Autoflush jetzt auch vor `text()`/Core-Statements -> nach Flush expired
  `onupdate`-Spalten (updated_at) vor Response-Validierung `refresh()`en, sonst
  MissingGreenlet.

**Native-Windows (Phasen 1+2 abgeschlossen + Migrations-Pfad verifiziert, ab v1.3):**

Foundation (siehe `### Native-Windows`-Sektion oben und
`planung/native-windows-konzept.md`):
- Settings via TOML, ChromaDB embedded-Modus, Frontend StaticFiles + SPA-Catch-All,
  bin_paths.py fuer Tesseract+poppler, programmatischer uvicorn-Start,
  Fresh-DB-create_all, PRAGMA foreign_keys=ON Event-Listener.

Build + Install:
- `scripts/build-native.ps1` end-to-end getestet: 187 MB Bundle, alle Endpoints OK.
- `scripts/fetch-build-tools.ps1` idempotenter Tool-Download (poppler, NSSM, Tesseract).
- `setup-native.nsi` NSIS-Installer mit Datenordner-Wahl, Auto-PIN, Firewall-Rule.
- Service-Install via `scripts/service-install.bat` (NSSM, Admin) — registriert
  `ZettelwirtschaftBackend` mit AutoStart + Log-Rotation (+ Dependency auf einen
  `Ollama`-Dienst, falls vorhanden).

Migration aus Docker (auf echter Installation mit 35 Dokumenten + 29 MB Archive
durchgespielt): siehe `## Native-Service-Ops` oben.

Naechste Phasen (3-4 laut Konzept): Migrations-Wizard im NSIS-Installer integrieren
(aktuell manueller PowerShell-Flow), Tray-Icon, Code-Signing (Authenticode-Cert).

### Architektur-Details: Version-Tracking
> Read `memory/release-deployment.md` before working on releases, installer, or Docker.
- `VERSION` - Datei im Projekt-Root (vom Installer/Release gelesen)
- `data/.version` - Geschrieben vom Installer nach erfolgreichem Deploy; gelesen vom Backend (via `/api/system/health → app_version`); via `./data:/app/data` Volume im Container sichtbar
- Frontend: Vite `define.__APP_VERSION__` bettet Version zur Build-Zeit ein (liest `../VERSION`)

### Alembic-Migrationen
- `001_add_ocr_analysis` - OCR- und Analyse-Spalten auf ProcessingJob
- `002_add_document_models` - Document, Tags, DocumentTags, WarrantyInfo, ReviewQuestions, AuditLog Tabellen
- `003_add_fts5_saved` - FTS5 Virtual Table + SavedSearch Tabelle
- `004_notifications_corrections` - Notification, CorrectionMapping Tabellen + ReviewQuestion-Erweiterungen
- `005_add_filing_scopes` - FilingScope-Tabelle + filing_scope_id auf Documents + Default-Scopes
- `006_add_chat_messages` - ChatMessage-Tabelle fuer RAG-Chat-Verlauf
- `007_add_system_settings` - SystemSetting-Tabelle fuer UI-konfigurierbare Einstellungen
- `008_add_warranty_reminder_flags` - Separate Reminder-Flags (90d/30d/0d) auf WarrantyInfo
- `009_add_email_accounts` - EmailAccount + ProcessedEmail Tabellen, email_account_id auf ProcessingJob
- `010_fix_email_filing_scope_fk_type` - FK-Typ-Fix EmailAccount.filing_scope_id Integer -> String(36)
- `011_add_performance_indexes` - Indizes auf Notification.is_read, ProcessingJob.created_at/status, ReviewQuestion.is_answered (idempotent: `CREATE INDEX IF NOT EXISTS`)
- `012_add_processing_started_at` - Heartbeat-Spalte fuer Stuck-Job-Recovery (B5 Re-Review)
- `013_fk_indexes_consistency` - chat_messages.filing_scope_id FK (ON DELETE SET NULL) + 4 Indizes auf email_accounts.filing_scope_id, processing_jobs.email_account_id, processed_emails.processing_job_id (H-ARCH-1/3/4)
- `014_background_tasks` - `background_tasks`-Tabelle + Partial Unique Index (hoechstens ein aktiver Vorgang pro kind+key)
- `015_job_document_id` - processing_jobs.document_id (FK ON DELETE SET NULL, Upload-/Dashboard-Link) + error_code (z.B. `DUPLICATE`)
- `016_schema_consistency` - Partial Unique Index `ux_documents_file_hash_active` (WHERE status != 'DELETED', Re-Upload geloeschter Dateien legt neues Dokument an), fehlende 006/011-Indizes in create_all-DBs, FK processing_jobs.email_account_id, review_questions.priority NOT NULL
- `017_review_data_fixes` - Datenmigration: leere Scope-Slugs, UUID-Scope-Korrekturen -> Namen, Steuerjahr fuer steuerrelevante Dokumente ohne tax_year

`migrate.py` ruft `init_db()` nicht mehr auf. Frische (leere) DBs: Schema via
`create_fresh_schema` (create_all) + Stamp auf head — die Alembic-Chain allein kann
eine leere DB nicht aufbauen (001 = ALTER ohne CREATE). Bestands-DBs: Alembic-Chain.
`detect_stamp` erkennt Legacy-DBs ohne `alembic_version`
auch fuer 010 (FK-Typ), 011 (Index-Existenz), 012 (column_type), 013 (FK-Existenz auf chat_messages — nicht nur Index, sonst stempelt zufaellig vorhandener Index falsch auf 013), 014 (Tabelle background_tasks), 015 (processing_jobs.document_id), 016 (Index ux_documents_file_hash_active); 017 ist reine idempotente Datenmigration ohne Merkmal.
Die Kette ist linear (Guard-Test `test_revision_chain_is_linear`): neue Migrationen immer auf den aktuellen Head setzen, parallel entstandene vor dem Merge verketten. `migrate.py` liest den DB-Pfad aus den Settings (`settings.sqlite_db_path`, wie Native).

**Native-Pfad** (`app/entrypoint.py`) wie Docker: Bei leerer DB → `Base.metadata.create_all`
+ Stamp auf head (+ Default-Scope-Seed). Migration 001 ist `ALTER TABLE processing_jobs` ohne
vorheriges `CREATE` — sie war historisch angewiesen auf `init_db()`. Ohne den
Fresh-DB-Pfad crasht der erste Native-Start.

**SQLite FK-Enforcement** (`app/database.py:configure_sqlite_engine`): Event-Listener
setzt `PRAGMA foreign_keys=ON` (+ `busy_timeout`) pro Connection der App. **Ohne ihn
sind ALLE `ondelete=SET NULL/CASCADE`-Constraints zur Laufzeit wirkungslos** —
SQLite-Default ist OFF. **Migrationen dagegen laufen mit `foreign_keys=OFF`**
(`alembic/env.py`): `batch_alter_table` baut Tabellen per DROP + RENAME um, mit aktiven
FKs loeste der DROP einer Eltern-Tabelle die ON-DELETE-Aktionen der Kinder aus
(Migration 010 hat so frueher alle processed_emails geloescht). env.py prueft vor und
nach der Migration `PRAGMA foreign_key_check`; nur NEUE Verletzungen brechen ab.
Rebuild-Migrationen brauchen keinen eigenen FK-Schalter — `app/core/sqlite_migration.py`
liefert nur Idempotenz-Helfer (UNIQUE-/FK-/Nullable-Abfragen).

### Tests
> Read `memory/e2e-tests.md` before writing or running E2E tests.

Keine festen Testzahlen hier (veralten mit jedem Test) — aktuell:
`cd backend && python -m pytest --collect-only -q` bzw. `cd e2e && npx playwright test --list`.

- **Backend:** API-, Service-, Model-, Core- und Migrations-Tests (1 skipped ohne Tesseract).
  Gemeinsame Factories in `tests/factories.py` (`make_document`, UUID-`file_hash`), HTTP-Client
  ueber `tests/conftest.make_test_client(settings, session_factory)` — Varianten bauen nur
  andere Settings (`test_settings.model_copy(update=...)`). Ollama/ChromaDB werden immer gemockt.
- **E2E** (Playwright + TypeScript), zwei Projekte: chromium (Desktop) + mobile (Pixel 5),
  beide laufen in `ci-local`. API-Response-Mocking via `page.route()` — kein Backend noetig.
  - **Ziel ist immer ein eigener Preview-Server** des aktuellen Builds: ohne `BASE_URL`
    startet Playwright per `webServer` `npm run build && vite preview --strictPort` auf
    `E2E_PORT` (Standard 4181). Port 8080 (echte Installation) wird abgelehnt.
  - **Service-Worker blockiert** (`serviceWorkers: 'block'`): SW-Requests sehen
    `page.route()` nicht und gingen ins echte Netz. Nur `17-pwa-service-worker.spec.ts`
    erlaubt den SW und prueft die Caching-Regeln ueber `context.route()`.
  - **Catch-all**: `installApiFallback` (in `setupBaseMocks`/`setupPinAuthMocks`)
    beantwortet ungemockte `/api`-Aufrufe mit 501. In Handlern `route.fallback()` statt
    `route.continue()` verwenden.
  - **Mocks typgeprueft** gegen `e2e/helpers/api-types.ts` (Spiegel der Backend-Schemas):
    `cd e2e && npm run typecheck` meldet Schema-Drift. Bei Backend-Schema-Aenderung dort
    nachziehen.
  - PIN-Flows: `setupPinAuthMocks(page, { dataMocks })` — ohne Sitzung liefert jede
    geschuetzte Route 401 wie die echte Middleware.
  - Warten auf Requests per `page.waitForRequest`/`expect.poll`, Polling per `page.clock`
    — keine festen `waitForTimeout`-Pausen.
- **Test-Zugangsdaten** (PIN, Dummy-IMAP-Passwoerter, Fernet-Key): **nie als Literal in Tests**
  (GitGuardian meldet sonst "Generic Password"). Je Stack genau eine Quelle, Reihenfolge
  Umgebungsvariable > lokale `.env.test` (gitignored) > pro Lauf zufaellig erzeugt:
  - Backend: `from tests.credentials import TEST_PIN, TEST_WRONG_PIN, TEST_IMAP_PASSWORD,
    TEST_IMAP_PASSWORD_2, TEST_EMAIL_ENCRYPTION_KEY` (liest `backend/.env.test`).
  - E2E: `e2e/helpers/credentials.ts` (`TEST_PIN`, `TEST_WRONG_PIN`, `TEST_IMAP_PASSWORD(_2)`,
    liest `e2e/.env.test`); `playwright.config.ts` ruft `ensureTestCredentials()` im
    Hauptprozess, die Worker-Prozesse erben die Werte ueber `process.env`.
  - Skripte: `scripts/test-scripts.ps1` (`$TestPin`, `$TestEmailKey`, liest `scripts/.env.test`).
  - Vorlagen: `*/.env.test.example`. Absichtlich ungueltige Eingaben (zu kurzer PIN, leerer
    PIN, falscher Key) bleiben sprechende Literale.
- **CI lokal**: GitHub-Actions deaktiviert (`.github/workflows/ci.yml.disabled`),
  Lauf via `pwsh scripts/ci-local.ps1` bzw. `bash scripts/ci-local.sh`.

### Optional Settings (.env / config.toml)

Settings-Quellen: ENV > `config.toml` (via `ZETTELWIRTSCHAFT_CONFIG`) > `.env` > secrets.

| Variable | Default | Effekt |
|---|---|---|
| `OLLAMA_MODEL` | `gemma4:e2b-it-qat` | LLM fuer Analyse + Rerank + Verifier (exakter Tag, kein Fallback-Modell; fehlt es: Auto-Pull beim Start, Health meldet es als fehlend). GPU-Stufe `gemma4:12b-it-qat`, bisheriger Default `qwen2.5:7b-instruct` |
| `EMBEDDING_MODEL` | `qwen3-embedding:4b` | 2560 dim, Anfragen mit Instruct-Prefix. Lean-Stufe (< 12 GB RAM) `qwen3-embedding:0.6b`, bisheriger Default `bge-m3` (1024 dim). Collection pro Modell, Wechsel = automatischer Neuaufbau beim Start |
| `RAG_USE_RERANKER` | `false` | LLM-Score-Reranker zwischen Hybrid-Retrieval und Antwort |
| `LLM_USE_VERIFIER` | `false` | Zweiter LLM-Pass validiert Erst-Pass-Felder bei niedriger Konfidenz |
| `LLM_VERIFIER_THRESHOLD` | `0.5` | Confidence-Schwelle (`<=`) ab der der Verifier feuert |
| `PIN_ENABLED` | `false` | Optionaler Web-PIN-Schutz; Backend warnt im Startup-Log + UI-Banner wenn aus |
| `OLLAMA_AUTO_PULL` | `true` | Fehlende Modelle beim Start aus der Ollama-Registry laden (einzige automatische Internet-Verbindung); `false` = nur melden |
| `EMAIL_ENCRYPTION_KEY` | (Pflicht fuer E-Mail) | Fernet-Key, vom Installer auto-generiert; verschluesselt auch die Passwoerter der Netzwerkordner |
| `ALLOW_NETWORK_FOLDERS` | `false` | Experten-Override: UNC OHNE Zugangsdaten (Computerkonto). Normalfall: UNC + Benutzer/Passwort in der UI |
| `WATCH_POLL_INTERVAL` | `30` | Pruefintervall (s, 10-600) fuer Netzwerk-Watch-Ordner, falls in der UI nichts gespeichert |
| `WATCH_FORCE_POLLING` | `false` | Auch lokale Watch-Ordner per Polling (z.B. Docker mit CIFS-Volume) |
| `ALLOWED_HOSTS` | `""` | Zusaetzliche Host-Namen fuer den DNS-Rebinding-Schutz (`app/core/host_guard.py`); IPs, localhost, PC-Name, `*.local`/`*.lan`/`*.home.arpa`/`*.internal`/`*.fritz.box` immer erlaubt, `*` = aus |
| `ENABLE_API_DOCS` | `false` | `/docs`, `/redoc`, `/openapi.json` ausliefern (ungeschuetzt durch PIN!) |
| `TIMEZONE` | `Europe/Berlin` | Zeitzone fuer Cron-Zeitplaene der E-Mail-Konten (ungueltig -> UTC + Warnung) |
| `TRUSTED_PROXIES` | `172.16.0.0/12` | Netze (kommagetrennte CIDRs), deren `X-Real-IP` uebernommen wird — nur im Docker-Betrieb wirksam; docker-compose setzt das Proxy-Netz `ZW_PROXY_SUBNET` |
| `OLLAMA_TIMEOUT` / `OLLAMA_ANALYSIS_TIMEOUT` | `300` / `120` | Timeout (s) fuer Ollama-Aufrufe allgemein / fuer die JSON-Analyse |
| `OLLAMA_NUM_CTX` / `OLLAMA_NUM_PREDICT` | `8192` / `1024` | Kontextfenster bzw. max. Antwort-Tokens der Analyse (`options` an Ollama) |
| `OLLAMA_KEEP_ALIVE` | `30m` | Wie lange Ollama das Modell nach einem Aufruf geladen haelt |
| `OLLAMA_THINK` | `false` | `think`-Parameter — nur gesendet, wenn das Modell laut `/api/show` "thinking" kann |
| `IMAP_TIMEOUT` | `30` | Socket-Timeout (s) fuer IMAP-Verbindungen |
| `EMAIL_MAX_PER_RUN` | `25` | Max. E-Mails pro Abruf (aelteste zuerst, Rest beim naechsten Scheduler-Tick) |

### Native-only Settings (config.toml)

| Variable | Default | Effekt |
|---|---|---|
| `SERVER_HOST` | `0.0.0.0` | HTTP-Bind. `127.0.0.1` = nur lokal (Setup-Checkbox "Nur von diesem PC", keine Firewall-Regel), `0.0.0.0` = LAN (ohne `SERVER_SSL_*` unverschluesselt, INFRA-07) |
| `SERVER_PORT` | `8080` | HTTP-Port (Native uvicorn). Docker nutzt `FRONTEND_PORT` |
| `CHROMADB_MODE` | `http` | `embedded` = `PersistentClient(path=...)`, `http` = Docker-Container |
| `CHROMADB_PATH` | (auto: `<ARCHIVE_DIR>/../chromadb`) | Pfad fuer Embedded-Mode |
| `FRONTEND_DIST_DIR` | `""` | Dev: wenn gesetzt + existiert, mountet das Backend `dist/` als `/`. Im Native-Bundle ignoriert (immer `<install>/frontend`, INFRA-01). Leer = Docker-Pfad |
| `ZETTELWIRTSCHAFT_CONFIG` (Env) | (none) | Pfad zur `config.toml` — von NSSM-Service in AppEnvironmentExtra gesetzt |

## Planungsdokumente

Detaillierte Anforderungen und Prompts liegen unter `planung/`:

| Datei | Inhalt |
|---|---|
| `roadmap.md` | Gesamtuebersicht, Phasen, Abhaengigkeiten |
| `01-projekt-setup.md` | Grundarchitektur, FastAPI, Docker, Config |
| `02-dokumenten-import.md` | Upload, Watch-Ordner, Queue, Thumbnails |
| `03-ki-dokumentenanalyse.md` | OCR, LLM-Analyse, Metadaten-Extraktion |
| `04-datenmodell-archiv.md` | SQLAlchemy-Modelle, Archivierung, CRUD-API |
| `05-web-oberflaeche.md` | Vue.js Dashboard, Dokumentenansicht, Upload-UI |
| `06-such-und-filtersystem.md` | FTS5 Volltextsuche, Facetten, Autocomplete |
| `07-steuerpaket-export.md` | Steuerkategorien, ZIP-Export, CSV, PDF |
| `08-garantie-tracker.md` | Garantie-Dashboard, Erinnerungen, Schadensfall |
| `09-smartphone-integration.md` | PWA, Kamera-Scan, mDNS, Bottom-Nav |
| `10-installation-deployment.md` | Docker Compose, Installer, Backup, Updates |
| `11-rueckfrage-system.md` | Interaktiver KI-Dialog bei unklaren Dokumenten |

Die Prompts sind sequenziell zu verwenden. Jeder Prompt hat Akzeptanzkriterien die erfuellt sein muessen bevor der naechste begonnen wird.

## PindeX – Codebase Navigation

Dieses Projekt ist mit PindeX indexiert.

**PFLICHT-WORKFLOW** – bei jeder Codebase-Aufgabe:
1. **Unbekannte Datei?** → `mcp__pindex__get_file_summary` ZUERST, dann ggf. `get_context`
2. **Symbol suchen?** → `mcp__pindex__search_symbols` oder `find_symbol`
3. **Abhängigkeiten?** → `mcp__pindex__get_dependencies`
4. **Wo wird etwas verwendet?** → `mcp__pindex__find_usages`
5. **Projekt-Überblick?** → `mcp__pindex__get_project_overview`

**VERBOTEN** (solange PindeX verfügbar):
- `Read` auf Quellcode-Dateien ohne vorherigen `get_file_summary`-Aufruf
- `Glob`/`Grep` zur Symbol-Suche statt `search_symbols`

**Kontext auslagern:**
- Wichtige Entscheidungen / Muster → `mcp__pindex__save_context` speichern
- Zu Sessionbeginn → `mcp__pindex__search_docs` für gespeicherten Kontext

**Fallback:** Falls ein Tool `null` zurückgibt → `Read`/`Grep` als Fallback.
<!-- pindex -->
