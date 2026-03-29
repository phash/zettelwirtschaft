# Zettelwirtschaft

## Projektuebersicht

Lokales Dokumentenmanagementsystem fuer Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI (Ollama/lokales LLM) analysiert, kategorisiert und durchsuchbar archiviert. Laeuft ausschliesslich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

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

- **Backend:** Python 3.12+ / FastAPI
- **Datenbank:** SQLite (via SQLAlchemy + Alembic)
- **OCR:** Tesseract OCR + pdf2image + pdfplumber (digitale PDFs)
- **KI-Analyse:** Ollama + lokales LLM (Llama 3.2 / Mistral)
- **Frontend:** Vue.js 3 (Composition API, `<script setup>`) + Vite
- **Deployment:** Docker Compose (Backend, Frontend/Nginx, Ollama, ChromaDB)
- **Smartphone:** PWA (Progressive Web App)
- **Vektor-Suche:** ChromaDB + Ollama Embeddings (nomic-embed-text) fuer RAG

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
    alembic/                     # DB-Migrationen (001-009)
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
      stores/                    # Pinia Stores (documents, notifications, auth)
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
  -> OCR (PDF: pdfplumber -> Tesseract Fallback | Bilder: Tesseract mit Vorverarbeitung)
  -> Text kuerzen (max 4000 Zeichen: erste 2000 + letzte 2000)
  -> LLM-Analyse (Ollama /api/chat, format: json, temperature: 0.1)
     1. Kombinierter Prompt (ein Aufruf fuer alles)
     2. Fallback: 4 sequentielle Einzel-Prompts
     3. Fallback: Minimal-Ergebnis mit needs_review=True
  -> Ergebnisse in ProcessingJob speichern (ocr_text, ocr_confidence, analysis_result)
  -> Konfidenz-Check gegen CONFIDENCE_THRESHOLD (0.7)
  -> Archivierung:
     - SHA-256 Hash berechnen + Duplikat-Check
     - Filing Scope bestimmen (Keyword-Match > LLM > Default)
     - Datei nach archive/{scope_slug}/{jahr}/{monat}/{typ}/ verschieben
     - Document-Eintrag + Tags + WarrantyInfo + ReviewQuestions + AuditLog erstellen
     - FTS5-Index aktualisieren
     - Vektorisierung (Chunking -> Ollama Embedding -> ChromaDB) - non-blocking
  -> Status: COMPLETED | NEEDS_REVIEW (+ review_questions) | FAILED
```

## Konventionen
> Read `memory/backend-patterns.md` (backend) and `memory/frontend-patterns.md` (frontend) before writing code.

### Backend (Python)
- Async wo sinnvoll (FastAPI async endpoints, httpx fuer Ollama)
- Type Hints durchgehend
- Pydantic fuer alle API-Schemas
- Konfiguration ausschliesslich ueber Umgebungsvariablen / `.env`
- Logging: strukturiert, JSON-Format
- Fehlerbehandlung: Graceful Degradation (Ollama nicht erreichbar -> NEEDS_REVIEW, nicht Absturz)

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

## Qualitaetsprinzipien

1. **Einfachheit:** Technisch nicht versierte Nutzer muessen es bedienen koennen.
2. **Datenschutz:** Alle Daten lokal. Keine Cloud, keine Telemetrie, keine externen Aufrufe.
3. **Robustheit:** Fehlerhafte Scans blockieren nicht das System. Graceful Degradation.
4. **Performance:** Dokumentenanalyse unter 30 Sekunden auf 8 GB RAM Hardware.
5. **Keine Ueberentwicklung:** Nur implementieren was in den Anforderungen steht.

## Implementierungsstatus

- [x] Prompt 01 - Projekt-Setup (FastAPI, SQLAlchemy, Docker, Config)
- [x] Prompt 02 - Dokumenten-Import (Upload, Watch-Ordner, Queue-Worker, Thumbnails)
- [x] Prompt 03 - KI-Dokumentenanalyse (OCR via pdfplumber/Tesseract, LLM via Ollama, Wasserfall-Degradation)
- [x] Prompt 04 - Datenmodell und Archiv-Datenbank (Document-Modell, Archive-Service, CRUD-API, Tags, Review, Dashboard-Stats)
- [x] Prompt 05 - Web-Oberflaeche (Vue.js 3, TailwindCSS, Dashboard, Dokumentenliste, Detail, Upload, Review)
- [x] Prompt 06 - Such- und Filtersystem (FTS5, Facetten, Autocomplete, Gespeicherte Suchen)
- [x] Prompt 07 - Steuerpaket-Export (ZIP + PDF via reportlab + CSV, Kategorien, Validierung)
- [x] Prompt 08 - Garantie-Tracker (Notification-Modell, Reminder-Service, Dashboard, Benachrichtigungsglocke)
- [x] Prompt 09 - Smartphone-Integration (PWA via vite-plugin-pwa, Kamera-Scan, BottomNav)
- [x] Prompt 10 - Installation und Deployment (Backup-Service, System-Health, Wartung)
- [x] Prompt 11 - Rueckfrage-System (Erweiterte ReviewQuestion, CorrectionMapping, Wizard-Cards, Auto-Update)
- [x] Ablagebereiche (Filing Scopes) - FilingScope-Modell, CRUD-API, Keyword+LLM-Zuweisung, Scope-Filter in Dokumenten/Suche/Steuer, Frontend-Einstellungen
- [x] Windows-Installer - install.bat + install-gui.ps1 (grafischer 4-Schritt-Wizard, Windows Forms) + install.ps1 (CLI-Fallback), start/stop/update/uninstall Skripte, Desktop-Verknuepfung
  - Installer erkennt bestehende Installation: version-Datei (`data\.version`) allein reicht, oder `.env` + DB/Archiv-Ordner
  - Semantischer Versionsvergleich (`[System.Version]`): Downgrade = rote Warnung, gleiche Version = Reparatur-Modus
  - Nach Install: Backend-Version via `/api/system/health` abgefragt und in `data\.version` gespeichert
  - Ollama-Modell-Check: `ollama list` vor Download - kein Re-Download wenn bereits vorhanden
  - Bekannte Fixes: v1.0.3 - `$btnBrowse` in CheckedChanged-Handler muss `$script:`-Scope haben (Event-Handler laeuft ausserhalb Funktions-Scope)
- [x] CI/CD Pipeline - GitHub Actions: CI (Tests + Build), Release (Tag v* -> GitHub Release + GHCR Docker Images)
- [x] PIN-Schutz - Optionaler PIN-Schutz fuer Web-Oberflaeche (`.env`-Config, In-Memory Sessions, Middleware, Router-Guard)
- [x] RAG-basierter KI-Assistent - ChromaDB + nomic-embed-text Vektorisierung, natuerlichsprachige Dokumenten-Fragen, ChatView, Migration 006
- [x] Issue #11: FilePicker-Hinweis + Windows-Pfad-Warnung in SettingsView
- [x] Issue #12: ChromaDB-Fehler mit Hilfetext + Kopier-Button, Versions-Zirkelbug behoben
- [x] Issue #13: Watch-Ordner Windows-Pfad-Problem erkannt und gewarnt
- [x] Steuerrelevant-Checkbox in Dokumentenliste - Steuer-Spalte direkt in der Liste sichtbar und per Klick aenderbar
- [x] Dashboard-Verbesserungen - Auto-Polling (3s) bei aktiven Jobs, Queue-Pause/Fortsetzen, fehlgeschlagene Jobs mit Copy-for-Claude-Button
- [x] ReviewView-Verbesserungen - Zoom (Mausrad + Buttons), Drag-to-Pan, Download, In-neuem-Tab-oeffnen
- [x] Versionierung - Sidebar-Footer + Einstellungen zeigen `app_version`; `/api/system/health` liefert Version aus `data/.version`
- [x] Host-Ordner (Issue #14) - Windows-Pfade als Watch/Export via Docker-Volume-Mount, `generate-mounts.ps1` erzeugt `docker-compose.override.yml`, Restart-Banner in Settings
- [x] Ablagebereich-Wechsel (Issue #15) - Scope-Dropdown immer sichtbar, "+"-Button fuer Inline-Anlage
- [x] Fehlerbehandlung (Issue #16) - Job-Retry-Endpoint, Retry-Button in Dashboard, bessere Fehlermeldungen
- [x] ChromaDB-Pinning - Image auf 0.6.3 gepinnt (Kompatibilitaet mit chromadb-client 0.6.x)
- [x] Installationspfad - In Settings anzeigen, "Ordner oeffnen"-Button kopiert Explorer-Befehl
- [x] Settings Auto-Refresh - Health-Status Polling alle 10 Sekunden
- [x] E-Mail-Anbindung (Issue #18) - IMAP-Polling, LLM-Relevanzpruefung, Fernet-Passwortverschluesselung, CRON/MANUAL/IDLE-Scheduling, E-Mail-Konten-UI in Settings, Dashboard-Stats, Migration 009
- [x] E2E Test-Suite - Playwright mit TypeScript, 13 Testdateien (~145 Tests), API-Mocking, Desktop + Mobile Projekte, CI-Integration
- [x] Automatische DB-Migrationen (v1.1.1) - `backend/entrypoint.sh` ruft `migrate.py` vor uvicorn auf; `migrate.py` erkennt Legacy-DBs ohne alembic_version-Tracking und stempelt korrekt, dann `alembic upgrade head`
- [x] ReviewView Kontext (v1.2.0) - Kontext-Cards pro Rueckfrage (betroffenes Feld + KI-Wert), Highlighting im Erkannte-Daten-Block, Umlaute im gesamten Frontend
- [x] PWA Service Worker Fix (v1.2.1) - `navigateFallbackDenylist` fuer `/api/`, `NetworkOnly` fuer File/Thumbnail-Endpunkte
- [x] AGPL-3.0 Lizenz - Open-Source-Lizenz hinzugefuegt
- [x] Re-Analyse bei LLM-Ausfall (v1.2.2) - `POST /review/documents/{id}/reanalyze` fuehrt LLM-Analyse erneut durch mit vorhandenem OCR-Text, Frontend-Button bei "LLM nicht erreichbar"-Rueckfrage
- [x] Code-Review + Security-Haertung (post-v1.2.2) - 47 von 55 Findings behoben (8 bewusst offen: Pagination/DELETE-Inkonsistenz, SVG-Duplizierung, aria-labels, reanalyze-Duplizierung, Dockerfile-Packages). Details in `CODE_REVIEW_FINDINGS.md`
  - Security: .env aus Backups entfernt, Path-Traversal-Schutz, PIN Rate-Limiting (5 Versuche/30s), constant-time PIN-Vergleich, CORS eingeschraenkt, Nginx Security-Headers (CSP, X-Frame-Options), SQL ORDER BY Whitelist, E-Mail-Anhaenge Magic-Byte-Validierung, IMAP in asyncio.to_thread
  - Backend: FK-Typ-Fix Migration 010, Performance-Indizes Migration 011, LLM-Service dedupliziert (_call_ollama), Double-Commits entfernt, datetime.utcnow() -> datetime.now(timezone.utc), Pydantic AnswerRequest statt dict, Dead Code entfernt
  - Frontend: SettingsView in 5 Sub-Components, shared documentTypes.js + formatters.js, Route-Param-Watch Fix, Dashboard-Polling Guard, Umlaute korrigiert, Notification Outside-Click, dynamischer Seitentitel, ScanView HTTPS-Erkennung
  - Docker: ChromaDB HTTP-Healthcheck, Ollama service_healthy Dependency, init_db() entfernt (nur Alembic), .dockerignore fuer Backend + Frontend
  - E-Mail-Security: EMAIL_ENCRYPTION_KEY leer -> HTTP 503 statt stiller Key-Generierung

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
- `011_add_performance_indexes` - Indizes auf Notification.is_read, ProcessingJob.created_at/status, ReviewQuestion.is_answered

### Tests
> Read `memory/e2e-tests.md` before writing or running E2E tests.

- 268 Backend-Tests (1 skipped fuer Tesseract)
- Backend: API-Tests (auth, documents, upload, jobs, search, tax, warranties, notifications, review, system, filing_scopes, chat, email), Service-Tests (archive, analysis, OCR, LLM, search, queue, upload, thumbnails, validation, tax_export, warranty_reminder, backup, embedding, rag, vectorize, crypto, email_relevance, email_fetch, email_scheduler), Model-Tests (email_models), Core-Tests (file_utils)

### E2E Tests
- Framework: Playwright mit TypeScript
- Verzeichnis: `e2e/` (eigenes package.json)
- 13 Testdateien, ~145 Tests
- Testdaten: API-Response-Mocking via `page.route()` (kein Backend noetig)
- Projekte: `chromium` (Desktop) + `mobile` (Pixel 5, ueberspringt Sidebar-Tests)
- Ausfuehren: `cd e2e && npm test` (benoetigt laufendes Frontend auf Port 8080)
- CI: Laeuft automatisch in GitHub Actions mit Vite Dev-Server

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
