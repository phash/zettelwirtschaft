# Zettelwirtschaft

## Projektuebersicht

Lokales Dokumentenmanagementsystem fuer Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI (Ollama/lokales LLM) analysiert, kategorisiert und durchsuchbar archiviert. Laeuft ausschliesslich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

## Technologie-Stack

- **Backend:** Python 3.12+ / FastAPI
- **Datenbank:** SQLite (via SQLAlchemy + Alembic)
- **OCR:** Tesseract OCR + pdf2image + pdfplumber (digitale PDFs)
- **KI-Analyse:** Ollama + lokales LLM (Llama 3.2 / Mistral)
- **Frontend:** Vue.js 3 (Composition API, `<script setup>`) + Vite
- **Deployment:** Docker Compose (Backend, Frontend/Nginx, Ollama)
- **Smartphone:** PWA (Progressive Web App)

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
      schemas/
        document.py              # DocumentResponse, DocumentListItem, DocumentUpdate, TagResponse, DashboardStats etc.
        filing_scope.py          # FilingScopeCreate, FilingScopeUpdate, FilingScopeResponse
        processing_job.py        # JobStatusResponse, UploadResponse
        search.py                # SearchResponse, SearchResultItem, SearchFacets, SuggestResponse, SavedSearchResponse
        tax.py                   # TaxYearSummary, TaxExportRequest, TaxExportValidation
        warranty.py              # WarrantyListItem, WarrantyUpdate, WarrantyStats
        notification.py          # NotificationResponse, NotificationCount
      api/
        documents.py             # CRUD + Upload + Tags + Stats + Thumbnails
        filing_scopes.py         # Ablagebereich-CRUD (Privat, Praxis etc.)
        search.py                # Volltextsuche + Autocomplete + SavedSearch
        health.py                # Health-Check
        jobs.py                  # Processing-Job-Status (kommagetrennte Status-Filter)
        tax.py                   # Steuerpaket-Export + Summary + Validation
        warranties.py            # Garantie-Liste + Stats + Update
        notifications.py         # Benachrichtigungen + Mark-Read
        review.py                # Erweitertes Rueckfrage-System + Approve + Stats
        system.py                # System-Health + Backup + Wartung
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
      core/
        file_utils.py            # Dateinamen-Sanitizing, Magic-Bytes, UUID-Prefix
      prompts/                   # LLM-Prompt-Templates (Textdateien)
    alembic/                     # DB-Migrationen (001-005)
    requirements.txt
    Dockerfile
  frontend/
    src/
      components/
        layout/                  # AppLayout, Sidebar, AppHeader, BottomNav (Mobile)
        common/                  # StatCard, Pagination, ConfirmDialog, DocTypeBadge, ToastContainer
      views/
        DashboardView.vue        # Statistik-Karten, letzte Dokumente, Quick-Upload
        DocumentsView.vue        # Tabellarische Liste mit Filtern und Sortierung
        DocumentDetailView.vue   # Zwei-Spalten-Layout: Vorschau + Metadaten-Formular
        UploadView.vue           # Drag-and-Drop Upload mit Fortschritt
        ReviewView.vue           # Erweitertes Review mit Wizard-Cards und Auto-Update
        SearchView.vue           # Volltextsuche mit Facetten und gespeicherten Suchen
        TaxView.vue              # Steuerbelege-Dashboard mit Kategorien + ZIP-Export
        WarrantyView.vue         # Garantie-Dashboard mit Status-Filter + Fortschrittsbalken
        ScanView.vue             # Kamera-Scan mit Aufnahme + Vorschau + Upload
        SettingsView.vue         # System-Health + Backup + Wartung + Ablagebereiche
      services/api.js            # Zentraler API-Client (Axios)
      router/index.js            # Vue Router
      stores/                    # Pinia Stores (documents, notifications)
    vite.config.js
    tailwind.config.js
    nginx.conf                   # SPA-Routing + API-Proxy
    Dockerfile                   # Multi-Stage: Node Build + Nginx
  docker-compose.yml
  .env.example
  planung/                       # Anforderungsdokumente und Prompts
```

## Architektur-Entscheidungen

- **Kein externer Message-Broker:** Verarbeitungs-Queue ist datenbankbasiert (SQLite). Kein Redis/RabbitMQ noetig.
- **Kein Authentifizierungssystem:** Privates LAN, optional PIN-Schutz.
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
- **Ablagebereiche (Filing Scopes):** Dokumente werden Ablagebereichen zugeordnet (z.B. "Privat", "Praxis Dr. Klotz-Roedig"). Zuweisung: 1. Keyword-Match im OCR-Text (Prioritaet), 2. LLM-Zuweisung (Konfidenz >= 0.7), 3. Default-Scope Fallback. Bei unsicherer Zuordnung: ReviewQuestion mit field_affected="filing_scope".
- **TaxCategory Enum-Storage:** `values_callable` fuer SQLAlchemy Enum, damit Enum-Values (z.B. "Werbungskosten") statt Names (z.B. "WERBUNGSKOSTEN") in SQLite gespeichert werden. LLM-Compound-Werte (z.B. "A | B") werden auf den ersten gueltigen Wert reduziert.

## Wichtige Datenmodelle

- `ProcessingJob` - Verarbeitungs-Queue (PENDING -> PROCESSING -> COMPLETED/NEEDS_REVIEW/FAILED). Felder: `ocr_text`, `ocr_confidence`, `analysis_result` (JSON)
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

## Dokumenttypen (Enum)

```
RECHNUNG, QUITTUNG, KAUFVERTRAG, GARANTIESCHEIN, VERSICHERUNGSPOLICE,
KONTOAUSZUG, LOHNABRECHNUNG, STEUERBESCHEID, MIETVERTRAG,
HANDWERKER_RECHNUNG, ARZTRECHNUNG, REZEPT, AMTLICHES_SCHREIBEN,
BEDIENUNGSANLEITUNG, SONSTIGES
```

## Verarbeitungs-Pipeline

```
Dokument-Eingang (Upload oder Watch-Ordner)
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
  -> Status: COMPLETED | NEEDS_REVIEW (+ review_questions) | FAILED
```

## Konventionen

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

### Alembic-Migrationen
- `001_add_ocr_analysis` - OCR- und Analyse-Spalten auf ProcessingJob
- `002_add_document_models` - Document, Tags, DocumentTags, WarrantyInfo, ReviewQuestions, AuditLog Tabellen
- `003_add_fts5_saved` - FTS5 Virtual Table + SavedSearch Tabelle
- `004_notifications_corrections` - Notification, CorrectionMapping Tabellen + ReviewQuestion-Erweiterungen
- `005_add_filing_scopes` - FilingScope-Tabelle + filing_scope_id auf Documents + Default-Scopes

### Tests
- 175 Tests gesamt (1 skipped fuer Tesseract)
- Backend: API-Tests (documents, upload, jobs, search, tax, warranties, notifications, review, system, filing_scopes), Service-Tests (archive, analysis, OCR, LLM, search, queue, upload, thumbnails, validation, tax_export, warranty_reminder, backup), Core-Tests (file_utils)

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
