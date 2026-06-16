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
# Backend (Python 3.12, virtualenv empfohlen)
cd backend
pip install -r requirements.txt
python -m alembic upgrade head      # DB-Migrationen
uvicorn app.main:app --reload       # Port 8000

# Frontend (Node 22+)
cd frontend
npm install
npm run dev                          # Vite Dev-Server, Port 3000

# Tests
cd backend && python -m pytest -q   # 374 Tests / 1 skipped (Tesseract)
cd e2e && npx playwright test --project=chromium  # 145 Tests
```

**Native-Build** (Setup.exe fuer Endkunden):

```powershell
# Voraussetzungen am Build-Host:
#   - Python 3.12, Node 22, NSIS 3.x (makensis im PATH)
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
sc query Ollama                      # gleiches fuer LLM-Backend
sc start ZettelwirtschaftBackend     # braucht Admin
sc stop ZettelwirtschaftBackend      # braucht Admin
# GUI: services.msc -> "Zettelwirtschaft" / "Ollama"
```

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
6. **Service installieren** (Admin): `scripts/service-install.bat <install-dir> <config> <log-dir>`.
7. **Ollama-Modelle** migrieren (gleicher C:\Temp-Trick) oder per `ollama pull bge-m3` + `ollama pull qwen2.5:7b` neu ziehen.

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

- **Backend:** Python 3.12 / FastAPI 0.137+ / SQLAlchemy 2.0 async / Pydantic v2
- **Datenbank:** SQLite (via SQLAlchemy + Alembic, Migrationen via `migrate.py`)
- **OCR:** Tesseract OCR + pdf2image + pdfplumber (digitale PDFs), Pillow 12.x
  - Pre-Processing: Auto-Upscale 2x bei kleinen Bildern, MedianFilter Denoising,
    aggressives Autocontrast (cutoff=2)
- **KI-Analyse:** Ollama mit qwen2.5:7b-instruct (Default) / llama3.2 (Fallback)
  - JSON-Schema-constrained Generation (Ollama 0.5+)
  - Few-Shot aus CorrectionMappings
  - Optional: LLM-Reranker + Verifier-Pass (config-flags)
- **Frontend:** Vue.js 3.5+ (Composition API, `<script setup>`) + Vite 8 + Tailwind v4
  + Pinia 3 + vue-router 5
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
    - Ollama als nativer Windows-Service (vom Ollama-Installer)
    - Konfiguration in `config.toml` statt `.env` (Pfad via `ZETTELWIRTSCHAFT_CONFIG`)
- **Smartphone:** PWA (Progressive Web App, vite-plugin-pwa 1.x)
- **Vektor-Suche:** ChromaDB 1.0.x + Ollama bge-m3-Embeddings + Hybrid Search FTS5+Vector mit RRF
- **Rate-Limiting:** slowapi (200/min default, X-Real-IP-Trust nur aus Docker-Net)
- **CI:** Lokal via `scripts/ci-local.{ps1,sh}`, GitHub-Actions deaktiviert

## Projektstruktur

```
zettelwirtschaft/
  backend/
    app/
      main.py                    # FastAPI-App, CORS, Startup, FTS5-Init
      config.py                  # Pydantic Settings (alle Config via .env)
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
      services/
        upload_service.py        # Datei-Upload-Verarbeitung
        file_validation_service.py # Dateityp- und Magic-Byte-Validierung
        queue_worker_service.py  # Queue-Worker (PENDING -> PROCESSING -> COMPLETED/NEEDS_REVIEW/FAILED)
        thumbnail_service.py     # Thumbnail-Generierung (Pillow/pdf2image)
        watch_folder_service.py  # Watch-Ordner-Ueberwachung (watchdog)
        ocr_service.py           # OCR: pdfplumber (digital) + Tesseract (Scans/Bilder)
        llm_service.py           # Ollama /api/chat mit JSON-Format, Retry-Logik
        analysis_service.py      # Dokumentenanalyse-Pipeline (OCR -> LLM -> AnalysisResult)
        archive_service.py       # Datei-Archivierung + DB-Eintrag + Tags + FTS-Index + Scope-Zuweisung
        search_service.py        # FTS5 Volltextsuche + Facetten + Autocomplete
        tax_export_service.py    # Steuerpaket-Export (ZIP + PDF via reportlab + CSV)
        warranty_reminder_service.py # Garantie-Erinnerungen (90/30/0 Tage)
        backup_service.py        # Backup-Service (DB + Config, Auto-Backup taeglich)
        embedding_service.py     # Ollama /api/embed mit Retry-Logik
        vectorize_service.py     # Chunking (Satzgrenzen), Vektorisierung bei Archivierung
        rag_service.py           # RAG-Pipeline (Embed -> ChromaDB-Retrieve -> LLM-Generate)
        crypto_service.py        # Fernet-Verschluesselung fuer E-Mail-Passwoerter
        email_relevance_service.py # LLM-basierte E-Mail-Relevanzpruefung
        email_fetch_service.py   # IMAP-Abruf, E-Mail-Parsing, Job-Erstellung
        email_scheduler_service.py # Background-Task: CRON/IDLE-Scheduling fuer E-Mail-Abruf
      core/
        file_utils.py            # Dateinamen-Sanitizing, Magic-Bytes, UUID-Prefix
      prompts/                   # LLM-Prompt-Templates (Textdateien, inkl. rag_answer.txt, email_relevance.txt)
    alembic/                     # DB-Migrationen (001-013)
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
      composables/               # useFilingScopes (shared cache + inflight-Token, ersetzt 7 duplizierte Loads)
    vite.config.js
    tailwind.config.js
    nginx.conf                   # SPA-Routing + API-Proxy
    Dockerfile                   # Multi-Stage: Node Build + Nginx
  e2e/
    tests/                       # 13 Testdateien (Smoke, Auth, Upload, Documents, Detail, Review, Search, Tax, Warranty, Chat, Settings, Responsive, Scan)
    helpers/mock.helpers.ts      # Zentrale Mock-Daten + Setup-Funktionen
    fixtures/test-files/         # Testdateien (PDF, PNG, TIFF)
    playwright.config.ts         # 2 Projekte: chromium (Desktop) + mobile (Pixel 5)
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
      ci.yml                     # CI: Tests + Build auf Push/PR
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
- **Soft-Delete** fuer Dokumente (Status `DELETED`, nicht physisch loeschen).
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
- **GPU-Support optional:** NVIDIA GPU deploy-Section in docker-compose.yml. Installer generiert `docker-compose.override.yml` ohne GPU fuer CPU-only Systeme.
- **Update mit Backup:** `update.bat` erstellt lokale Sicherheitskopie der DB + .env vor jedem Update. Bricht ab wenn DB-Backup fehlschlaegt.
- **RAG-Pipeline:** Dokumente werden bei Archivierung vektorisiert (Chunking mit Satzgrenzen-Erkennung, Metadaten-Chunks). Vektorisierungsfehler blockieren Archivierung nicht (graceful degradation). Retrieval per Cosine-Similarity aus ChromaDB.
- **call_llm_text():** Freitext-LLM-Antworten ohne JSON-Format (fuer RAG-Antwortgenerierung).
- **ChromaDB als Vektor-Store:** Separater Docker-Service. Collection pro Ablagebereich. Vektor-Index kann in System-Wartung neu aufgebaut werden.
- **E-Mail-Integration (Issue #18):** IMAP-Polling-Service als Backend Background-Task. LLM entscheidet ueber E-Mail-Relevanz. Relevante Anhaenge + Body werden als ProcessingJobs in bestehende Pipeline eingespeist. Passwoerter Fernet-verschluesselt (AES-128-CBC, `EMAIL_ENCRYPTION_KEY` in `.env`). Scheduling: CRON (via croniter), MANUAL (API-Trigger), IDLE (alle 5 Minuten). Verarbeitete E-Mails werden in IMAP-Ordner verschoben. Konfiguration ueber Web-UI (SettingsView).
- **PWA Service Worker Caching:** `navigateFallbackDenylist: [/^\/api\//]` verhindert dass der SW API-Requests mit `index.html` beantwortet. File/Thumbnail-Endpunkte sind `NetworkOnly` (Binaerdaten duerfen nicht gecacht werden, da sonst iframes die App statt das Dokument laden). API-JSON-Responses sind `NetworkFirst` mit 5min Cache.
- **Lizenz:** AGPL-3.0 (verhindert proprietaere SaaS-Forks).
- **PIN Rate-Limiting:** In-Memory Per-IP Tracking (5 Versuche, 30s Lockout). `secrets.compare_digest` fuer constant-time Vergleich. `X-Real-IP` Header von Nginx.
- **CORS:** Explizite Origins + LAN-Regex (`192.168.*`, `10.*`, `172.16-31.*`) statt `["*"]`.
- **Nginx Security-Headers:** CSP, X-Frame-Options SAMEORIGIN, X-Content-Type-Options nosniff, server_tokens off.
- **Backup-Sicherheit:** `.env` wird nicht mehr in Backup-ZIPs aufgenommen (enthaelt Secrets). Path-Traversal-Schutz bei Backup-Download via `resolve()` + `is_relative_to()`.
- **E-Mail-Encryption-Key:** `EMAIL_ENCRYPTION_KEY` muss in `.env` gesetzt sein. Leerer Key -> HTTP 503 bei Account-Erstellung (keine stille Key-Generierung).
- **IMAP async:** Alle blockierenden IMAP-Operationen in `asyncio.to_thread()` gewrappt.
- **LLM-Service Deduplizierung:** `call_llm()` und `call_llm_text()` nutzen shared `_call_ollama()` Helper.
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
- `Notification` - Benachrichtigungen (WARRANTY_EXPIRING, WARRANTY_EXPIRED, REVIEW_NEEDED, PROCESSING_DONE, SYSTEM)
- `CorrectionMapping` - Lerneffekt aus Benutzer-Korrekturen (auto_apply nach 3x gleicher Korrektur)
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
     - SHA-256 Hash berechnen + Duplikat-Check (FAILED bei Duplikat,
       Quelldatei bleibt fuer User-Inspektion stehen)
     - Filing Scope bestimmen (Keyword-Match > LLM (>=0.7) > Default)
     - Datei nach archive/{scope_slug}/{jahr}/{monat}/{typ}/ kopieren,
       erst nach DB-Commit aus Upload-Ordner loeschen (rollback-sicher)
     - Document-Eintrag + Tags + WarrantyInfo + ReviewQuestions + AuditLog erstellen
     - FTS5-Index aktualisieren
     - Vektorisierung mit bge-m3 (1024 dim) -> ChromaDB-Collection
       documents_bge-m3, filing_scope_id als Metadata
  -> Status: COMPLETED | NEEDS_REVIEW (+ review_questions) | FAILED
```

**RAG-Chat-Pipeline (Frage -> Antwort):**

```
User-Frage
  -> embed_text (bge-m3) + parallel _fts_top_doc_ids (FTS5 mit Scope-Filter)
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
- Logging: strukturiert, JSON-Format
- Fehlerbehandlung: Graceful Degradation (Ollama nicht erreichbar -> NEEDS_REVIEW, nicht Absturz)
- LLM/Embedding: shared `httpx.AsyncClient` Singleton (`llm_service._get_client`).
  Timeout pro Request, exponential Backoff (2/4/8s).
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
- **PyInstaller-Spec:** `backend/zettelwirtschaft.spec` (Onedir, kein UPX, kein onefile-Mode wegen Antivirus + Startup-Zeit). Bundle ist ~187 MB / 1842 Files.
- **Entrypoint:** `backend/app/entrypoint.py` mit `--config`, `--migrate-only`, `--version`. Bei `frozen` Bundle Alembic via Public-API (`alembic.command.upgrade`), kein subprocess.
- **bin_paths.py** wird in `main.py` SEHR FRUEH importiert (vor pdf2image/pytesseract), setzt PATH+TESSDATA_PREFIX+pytesseract.tesseract_cmd auf gebundeltes `<install>/bin/`. No-op im Dev/Docker.
- **ChromaDB embedded:** `_get_chroma_client` schaltet via `CHROMADB_MODE` zwischen `PersistentClient(path=...)` (native) und `HttpClient` (docker).
- **Frontend:** `app.mount("/assets")` + Catch-All-Route `@app.get("/{full_path:path}")` mit `FileResponse(index.html)` als Fallback (SPA-Routing fuer `/dokumente` usw.). Nur aktiv wenn `FRONTEND_DIST_DIR` gesetzt + existiert.
- **Service:** NSSM-Wrapper (`scripts/service-install.bat`), Log-Rotation 10 MB, `DependOnService Ollama` (wenn vorhanden), AutoStart.
- **Migration aus Docker:** `scripts/Convert-Env-To-Config.ps1` mapt `.env`-Keys auf TOML.

**Kritische Build-Gotchas:**
- **chromadb vs chromadb-client:** PyInstaller braucht `chromadb` (full mit `PersistentClient`), NICHT `chromadb-client` (HTTP-only). Beide nutzen den `chromadb`-Namespace — Konflikt. `build-native.ps1` deinstalliert chromadb-client und installiert `chromadb` aus `requirements-build.txt`. Symptom bei Fehler: `RuntimeError: Chroma is running in http-only client mode`.
- **PRAGMA foreign_keys=ON:** siehe Alembic-Sektion oben. Ohne den Event-Listener sind FK-Constraints zur Laufzeit wirkungslos.
- **Fresh-DB-Init:** Migration 001 macht ALTER ohne CREATE. `entrypoint.py:_run_migrations` macht `Base.metadata.create_all` bei leerer DB + Stamp auf head (Migrationen sind dann no-op).
- **SPA-Routing:** `StaticFiles(html=True)` deckt nur Ordner-Index ab. `/dokumente` wuerde sonst 404 geben — der Catch-All-Route ist notwendig.

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
- **PIN-Schutz** (opt-in) mit slowapi-Rate-Limiting + Trusted-Proxy-Filter
- **RAG-Assistent** mit Hybrid-Search FTS5+Vector + RRF + optional LLM-Reranker
- **E-Mail-Anbindung** (IMAP-Polling, LLM-Relevanzpruefung, Fernet-verschluesselte Passwoerter)
- **Auto-Migration** beim Start (`migrate.py` + Alembic-Chain, kein init_db mehr)
- **CI lokal** statt GitHub-Actions (`scripts/ci-local.{ps1,sh}`)

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
- LLM-Pipeline: bge-m3, JSON-Schema-Mode, Few-Shot, Hybrid-RRF, OCR-Preprocess,
  optional Reranker + Verifier
- Lib-Stand (v1.3.0, aggressive Migration): Vite 8, TypeScript 6, Tailwind v4,
  Pinia 3, vue-router 5.1, Node 22/24, ChromaDB 1.5, FastAPI 0.137, uvicorn 0.49,
  cryptography 49, SQLAlchemy 2.0.51, pytest 9 / pytest-asyncio 1.x, Pillow 12.2,
  axios 1.18, slowapi 0.1.10

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
  `ZettelwirtschaftBackend` mit AutoStart + Log-Rotation + Dependency auf Ollama.

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

`migrate.py` setzt `init_db()` nicht mehr aufruft — Schema kommt komplett aus
der Alembic-Chain. `detect_stamp` erkennt Legacy-DBs ohne `alembic_version`
auch fuer 010 (FK-Typ), 011 (Index-Existenz), 012 (column_type), 013 (FK-Existenz auf chat_messages — nicht nur Index, sonst stempelt zufaellig vorhandener Index falsch auf 013).

**Native-Pfad** (`app/entrypoint.py`): Bei leerer DB → `Base.metadata.create_all`
+ Stamp auf head. Migration 001 ist `ALTER TABLE processing_jobs` ohne
vorheriges `CREATE` — sie war historisch angewiesen auf `init_db()`. Ohne den
Fresh-DB-Pfad crasht der erste Native-Start.

**SQLite FK-Enforcement** (`app/database.py` + `alembic/env.py`): Event-Listener
setzt `PRAGMA foreign_keys=ON` pro Connection. **Ohne ihn sind ALLE
`ondelete=SET NULL/CASCADE`-Constraints zur Laufzeit wirkungslos** — SQLite-Default
ist OFF. Migration 013 verlaesst sich darauf.

### Tests
> Read `memory/e2e-tests.md` before writing or running E2E tests.

- **Backend: 374 Tests** (1 skipped fuer Tesseract). API-Tests + Service-Tests
  + Model-Tests + Core-Tests + Phase-8-Tests fuer Reranker, Verifier,
  Trusted-Client-IP + Re-Review-Tests (PIN-Lockout, Sanitizer, Scope-Fallback,
  Heartbeat, Email-FAILED-Record).
- **E2E: 145 Tests** (Playwright + TypeScript), 13 Testdateien. API-Response-Mocking
  via `page.route()` — Frontend-only-Run ohne Backend moeglich.
- **CI lokal**: GitHub-Actions deaktiviert (`.github/workflows/ci.yml.disabled`),
  Lauf via `pwsh scripts/ci-local.ps1` bzw. `bash scripts/ci-local.sh`.

### Optional Settings (.env / config.toml)

Settings-Quellen: ENV > `config.toml` (via `ZETTELWIRTSCHAFT_CONFIG`) > `.env` > secrets.

| Variable | Default | Effekt |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | LLM fuer Analyse + Rerank + Verifier (Fallback `llama3.2`) |
| `EMBEDDING_MODEL` | `bge-m3` | Multilingual, 1024 dim. ChromaDB-Collection ist modell-abhaengig |
| `RAG_USE_RERANKER` | `false` | LLM-Score-Reranker zwischen Hybrid-Retrieval und Antwort |
| `LLM_USE_VERIFIER` | `false` | Zweiter LLM-Pass validiert Erst-Pass-Felder bei niedriger Konfidenz |
| `LLM_VERIFIER_THRESHOLD` | `0.5` | Confidence-Schwelle (`<=`) ab der der Verifier feuert |
| `PIN_ENABLED` | `false` | Optionaler Web-PIN-Schutz; Backend warnt im Startup-Log + UI-Banner wenn aus |
| `EMAIL_ENCRYPTION_KEY` | (Pflicht fuer E-Mail) | Fernet-Key, vom Installer auto-generiert |

### Native-only Settings (config.toml)

| Variable | Default | Effekt |
|---|---|---|
| `SERVER_HOST` | `0.0.0.0` | HTTP-Bind. `127.0.0.1` = nur lokal, `0.0.0.0` = LAN |
| `SERVER_PORT` | `8080` | HTTP-Port (Native uvicorn). Docker nutzt `FRONTEND_PORT` |
| `CHROMADB_MODE` | `http` | `embedded` = `PersistentClient(path=...)`, `http` = Docker-Container |
| `CHROMADB_PATH` | (auto: `<ARCHIVE_DIR>/../chromadb`) | Pfad fuer Embedded-Mode |
| `FRONTEND_DIST_DIR` | `""` | Wenn gesetzt + existiert: Backend mountet `dist/` als `/` (Native, kein nginx). Leer = Docker-Pfad |
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
