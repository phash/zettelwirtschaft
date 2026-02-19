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
      main.py                    # FastAPI-App, CORS, Startup
      config.py                  # Pydantic Settings (alle Config via .env)
      database.py                # SQLAlchemy Engine, Session, Base
      models/                    # SQLAlchemy-Modelle
      schemas/                   # Pydantic-Schemas (API Request/Response)
      api/                       # FastAPI-Router
      services/                  # Business-Logik
        upload_service.py        # Datei-Upload-Verarbeitung
        file_validation_service.py # Dateityp- und Magic-Byte-Validierung
        queue_worker_service.py  # Queue-Worker (PENDING -> PROCESSING -> COMPLETED/NEEDS_REVIEW/FAILED)
        thumbnail_service.py     # Thumbnail-Generierung (Pillow/pdf2image)
        watch_folder_service.py  # Watch-Ordner-Ueberwachung (watchdog)
        ocr_service.py           # OCR: pdfplumber (digital) + Tesseract (Scans/Bilder)
        llm_service.py           # Ollama /api/chat mit JSON-Format, Retry-Logik
        analysis_service.py      # Dokumentenanalyse-Pipeline (OCR -> LLM -> AnalysisResult)
      core/                      # Hilfsfunktionen
        file_utils.py            # Dateinamen-Sanitizing, Magic-Bytes, UUID-Prefix
      prompts/                   # LLM-Prompt-Templates (Textdateien)
        analyze_document.txt     # Kombinierter Prompt (primaer): Typ + Metadaten + Steuer + Garantie
        classify_document.txt    # Fallback: Nur Dokumenttyp
        extract_metadata.txt     # Fallback: Nur Metadaten
        assess_tax_relevance.txt # Fallback: Nur Steuerrelevanz
        extract_warranty_info.txt # Fallback: Nur Garantie-Info
    alembic/                     # DB-Migrationen
    requirements.txt
    Dockerfile
  frontend/
    src/
      components/
      views/
      services/api.js            # Zentraler API-Client (Axios)
      router/
      stores/                    # Pinia Stores
    vite.config.js
    Dockerfile
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
- **Archiv-Ordnerstruktur:** `data/archive/{jahr}/{monat}/{document_type}/`
- **Dateinamen im Archiv:** UUID-Prefix + bereinigter Originalname.
- **Kombinierter LLM-Prompt als Primaerstrategie:** Ein Ollama-Aufruf fuer Klassifikation + Metadaten + Steuer + Garantie. 4 Einzel-Prompts als Fallback bei JSON-Parse-Fehler.
- **OCR-Strategie fuer PDFs:** Zuerst pdfplumber (digitaler Text, Konfidenz 1.0), dann Tesseract (Scans via pdf2image).
- **Wasserfall-Degradation:** OCR-Fehler -> NEEDS_REVIEW | LLM-Fehler -> NEEDS_REVIEW mit OCR-Text | Niedrige Konfidenz -> NEEDS_REVIEW mit allen Daten | Erfolg -> COMPLETED.

## Wichtige Datenmodelle

- `ProcessingJob` - Verarbeitungs-Queue (PENDING -> PROCESSING -> COMPLETED/NEEDS_REVIEW/FAILED). Felder: `ocr_text` (extrahierter Text), `ocr_confidence` (0.0-1.0), `analysis_result` (JSON mit Typ, Metadaten, Steuer, Garantie)
- `Document` - Kerntabelle mit allen KI-extrahierten Metadaten + OCR-Text
- `Tag` / `DocumentTag` - Schlagwort-System (automatisch + manuell)
- `WarrantyInfo` - Garantie-Informationen zu Kaufbelegen
- `ReviewQuestion` - KI-Rueckfragen bei unklaren Dokumenten
- `Notification` - Benachrichtigungen (Garantie-Ablauf etc.)
- `SavedSearch` - Gespeicherte Suchanfragen
- `CorrectionMapping` - Lerneffekt aus Benutzer-Korrekturen
- `AuditLog` - Aenderungsprotokoll

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
  -> Status: COMPLETED | NEEDS_REVIEW (+ review_questions) | FAILED
  -> [Prompt 04] Archivierung (Datei verschieben, Document-Eintrag, Tags)
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
- [ ] Prompt 04 - Datenmodell und Archiv-Datenbank

### Alembic-Migrationen
- `001_add_ocr_analysis` - OCR- und Analyse-Spalten auf ProcessingJob (ocr_text, ocr_confidence, analysis_result)

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
