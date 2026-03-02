# Zettelwirtschaft

Lokales Dokumentenmanagementsystem fuer Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI analysiert, kategorisiert und durchsuchbar archiviert.

Laeuft ausschliesslich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

## Features

- **KI-Dokumentenanalyse** - Automatische Erkennung von Typ, Datum, Betrag, Aussteller via Ollama/LLM
- **OCR** - Text aus Scans und PDFs extrahieren (Tesseract + pdfplumber)
- **Volltextsuche** - SQLite FTS5 mit Facetten und Autocomplete
- **Steuerpaket-Export** - Belege nach Steuerkategorien filtern und als ZIP exportieren
- **Garantie-Tracker** - Ablaufdaten im Blick mit automatischen Erinnerungen
- **Smartphone-Scan** - Dokumente per Kamera erfassen (PWA)
- **KI-Assistent (RAG)** - Fragen zu eigenen Dokumenten in natuerlicher Sprache stellen
- **Ablagebereiche** - Dokumente nach Bereichen organisieren (z.B. Privat, Praxis)
- **Rueckfrage-System** - KI fragt bei unklaren Dokumenten gezielt nach
- **Steuerrelevanz** - Direkt in der Dokumentenliste sichtbar und per Klick aenderbar

## Installation (Windows)

### Voraussetzungen

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installiert und gestartet
- Mindestens 8 GB RAM (empfohlen)
- Mindestens 10 GB freier Festplattenspeicher

### Schnellstart

1. [Neuestes Release herunterladen](https://github.com/phash/zettelwirtschaft/releases/latest) und entpacken
2. `install.bat` doppelklicken
3. Den Anweisungen des Installationsassistenten folgen

Der Installer prueft automatisch Docker, konfiguriert Ports und LLM-Modell, und erstellt eine Desktop-Verknuepfung.

### Bedienung

| Skript | Beschreibung |
|---|---|
| `start.bat` | System starten und Browser oeffnen |
| `stop.bat` | System stoppen |
| `update.bat` | System aktualisieren (erstellt vorher ein Backup) |
| `uninstall.bat` | System deinstallieren |

### Manuelle Installation

1. Repository klonen:
   ```bash
   git clone https://github.com/phash/zettelwirtschaft.git
   cd zettelwirtschaft
   ```

2. Konfiguration erstellen:
   ```bash
   cp .env.example .env
   ```

3. Anwendung starten:
   ```bash
   docker compose up --build -d
   ```

4. LLM-Modelle herunterladen:
   ```bash
   docker compose exec ollama ollama pull llama3.2
   docker compose exec ollama ollama pull nomic-embed-text
   ```

5. Im Browser oeffnen:
   - **Anwendung:** http://localhost:8080
   - **API-Dokumentation:** http://localhost:8000/docs
   - **Health-Check:** http://localhost:8000/api/health

### Konfiguration

Die Konfiguration erfolgt ueber die `.env`-Datei. Wichtige Einstellungen:

| Variable | Default | Beschreibung |
|---|---|---|
| `FRONTEND_PORT` | `8080` | Port fuer die Web-Oberflaeche |
| `OLLAMA_MODEL` | `llama3.2` | LLM-Modell (llama3.2 fuer <=16GB RAM, llama3.1 fuer >16GB) |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama-Server URL |
| `OCR_LANGUAGES` | `deu+eng` | OCR-Sprachen |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embedding-Modell fuer RAG-Vektorisierung |
| `RAG_TOP_K` | `5` | Anzahl der relevantesten Textpassagen fuer RAG-Antworten |

## Verzeichnisstruktur

Nach dem ersten Start werden folgende Verzeichnisse unter `data/` angelegt:

| Verzeichnis | Zweck |
|---|---|
| `data/uploads` | Hochgeladene Originaldokumente |
| `data/watch` | Scanner-Eingabeordner (automatische Erkennung) |
| `data/archive` | Archivierte, verarbeitete Dokumente |
| `data/backups` | Automatische und manuelle Backups |

## Technologie-Stack

- **Backend:** Python 3.12 / FastAPI
- **Datenbank:** SQLite (SQLAlchemy + Alembic)
- **OCR:** Tesseract + pdfplumber
- **KI:** Ollama mit lokalem LLM (Llama 3.2 / Mistral) + Embeddings (nomic-embed-text)
- **Vektor-Suche:** ChromaDB (RAG-basierter KI-Assistent)
- **Frontend:** Vue.js 3 + TailwindCSS (PWA)
- **Deployment:** Docker Compose

## Changelog

### v1.1.0
- **Umfassender Audit + Bugfix-Release (43 Fixes)**
- Fix (kritisch): Datei wird erst nach DB-Flush verschoben (verhindert Datenverlust bei DB-Fehler)
- Fix (kritisch): SQLite-Backup via `sqlite3.backup()` API statt Dateikopie (konsistente Sicherung bei laufenden Schreibvorgaengen)
- Fix (kritisch): `WarrantyInfo.document` Relationship mit `lazy="selectin"` (verhindert N+1 Queries und MissingGreenlet)
- Fix (kritisch): Login mit falschem PIN gibt HTTP 401 statt 200 zurueck
- Fix: Garantie-Erinnerungen fuer 30-Tage und Ablauf-Schwelle (separate Flags pro Schwelle statt einzelnes Boolean)
- Fix: ChromaDB-Suche blockiert nicht mehr den Event Loop (`asyncio.to_thread`)
- Fix: Tesseract-OCR laeuft nur noch einmal pro Bild (doppelter Aufruf eliminiert, Performanceverbesserung)
- Fix: XSS-Risiko bei Suchergebnis-Highlights behoben (`v-html` durch sichere Text-Interpolation ersetzt)
- Fix: Chat-Eingabe auf Mobilgeraeten nicht mehr hinter BottomNav verborgen
- Fix: Settings-Seite flasht nicht mehr alle 10 Sekunden beim Health-Polling
- Fix: Division-durch-Null in Garantie-Fortschrittsbalken und Steuer-Kategoriebalken
- Fix: Doppelte API-Calls bei Filterwechsel in Dokumentenliste eliminiert
- Fix: Sortierungswechsel in Suche setzt Seite auf 1 zurueck
- Fix: Retry in Dashboard startet Polling und aktualisiert Jobansicht
- Fix: UploadView raeumt Polling-Interval bei Navigation auf (Memory-Leak behoben)
- Fix: PIN-Schutz bleibt bei Backend-Netzwerkfehler aktiv (sicherer Default)
- Fix: ReviewQuestionResponse-Schema um 4 fehlende Felder erweitert (question_type, explanation, suggested_answers, priority)
- Fix: Tag.documents Relationship auf `lazy="noload"` (unnuetzes Laden aller Dokumente pro Tag verhindert)
- Fix: Atomare Job-Uebernahme im Queue-Worker (optimistic locking statt TOCTOU)
- Fix: IntegrityError bei parallelen Duplikat-Uploads wird korrekt als ValueError behandelt
- Fix: FTS5-Operator-Injection verhindert (Spaltenfilter und Boolesche Operatoren werden sanitized)
- Fix: `float()` bei LLM-Antworten mit try/except abgesichert
- Fix: HTTP 5xx von Ollama wird jetzt wiederholt (Retry-Logik erweitert)
- Fix: Prompt-Template-Injection durch OCR-Text verhindert (Ersetzungsreihenfolge korrigiert)
- Fix: `str.format()` in RAG-Service durch `.replace()` ersetzt (KeyError bei geschweiften Klammern verhindert)
- Fix: SHA-256 Hash-Berechnung async via `asyncio.to_thread` (blockiert Event Loop nicht mehr)
- Fix: Backup-Erstellung async (blockiert Event Loop nicht mehr)
- Fix: Image.open mit Context Manager in OCR (Dateideskriptor-Leak behoben)
- Fix: SavedSearchResponse.query_params wird als Dict statt JSON-String geliefert
- Fix: JSON.parse bei gespeicherten Suchen mit try/catch abgesichert
- Fix: POST /saved-searches gibt HTTP 201 statt 200 zurueck
- Fix: 404-Catch-All-Route im Frontend-Router (unbekannte URLs leiten zum Dashboard)
- Fix: Touch-Event-Support fuer Bild-Pan/Zoom in ReviewView (Smartphone-Unterstuetzung)
- Fix: Review-Skip setzt review_status auf OK statt No-Op
- Fix: App-Version dynamisch aus VERSION-Datei statt hardcodiert
- Fix: Notifications-Endpoint mit Paginierung (limit/offset statt hardcodiertem limit 50)
- Fix: sort_by/sort_order Query-Parameter mit Regex-Validierung
- Fix: models/__init__.py um SystemSetting und ChatMessage Imports ergaenzt
- Migration: 008_add_warranty_reminder_flags (separate 90d/30d/0d Reminder-Flags)
- Tests: 236 Tests (angepasst an neue HTTP-Statuscodes)

### v1.0.9
- Feature: Host-Ordner fuer Watch/Export via Docker-Volume-Mount (Windows-Pfade wie `V:\Zettelwirtschaft` werden als Docker-Volume gemountet)
- Feature: Installationspfad in Settings anzeigen mit "Ordner oeffnen"-Button (kopiert Explorer-Befehl)
- Feature: Settings Auto-Refresh (Health-Status alle 10 Sekunden aktualisiert)
- Fix: Ablagebereich-Dropdown immer sichtbar + Schnell-Anlegen per "+"-Button (#15)
- Fix: Fehlgeschlagene Jobs mit Retry-Button, selektierbarer Fehlermeldung und Zeitstempel (#16)
- Fix: ChromaDB-Image auf 0.6.3 gepinnt (Kompatibilitaet mit chromadb-client 0.6.x)
- Fix: Bessere Fehlermeldungen bei leeren Exceptions im Queue-Worker
- Tests: 237 Tests (Host-Mount-Settings, Retry-Endpoint)

### v1.0.8
- Fix: Dashboard zeigte keine Statistiken (falscher API-Endpunkt `/stats` statt korrektem Pfad)
- Fix: Installer schrieb Version zirkulaer aus API (las `data/.version`, schrieb sie zurueck) - jetzt direkt aus Installer-Paket
- Fix: Windows-Pfade (z.B. `V:\Ordner`) in Watch/Export-Ordner werden erkannt und mit Warnung markiert (Docker kann nur Container-Pfade lesen)
- Fix: ChromaDB-Fehler zeigt jetzt Hilfetext mit Diagnosebefehlen statt nur "HTTP 404"
- Verbesserung: Ordner-Einstellungen mit Standard-Button und Pfad-Hinweisen
- Tests: 232 Tests (System-Settings Roundtrip, Dashboard-Stats-Struktur)

### v1.0.7
- Fix: Installer-Log zeigte Docker-Fortschrittszeilen hunderte Male (Downloading-Spam durch \r-Output)
- Fix: Progressbar zeigt jetzt animierten Marquee waehrend Docker-Images geladen werden statt einzufrieren

### v1.0.6
- Fix: Setup.exe enthielt keine VERSION-Datei - Installer zeigte immer "v1.0.3" als neue Version
- Fix: Fallback-Version im Installer von "1.0.3" auf "unbekannt" geaendert

### v1.0.5
- Feature: Installierte Version im Sidebar-Footer und in Einstellungen → System angezeigt
- Feature: `/api/system/health` liefert jetzt `app_version` aus `data/.version`
- Fix: Installer schreibt nach Install tatsaechliche Backend-Version (nicht mehr aus VERSION-Datei)
- Fix: Installer-Erkennung einer bestehenden Installation robuster (version-Datei allein reicht)
- Fix: Versionsvergleich im Installer - Downgrade wird als Warnung angezeigt
- Fix: Ollama-Modell-Download wird uebersprungen wenn Modell bereits vorhanden
- Feature: Dashboard zeigt fehlgeschlagene Jobs mit "Kopieren"-Button fuer Claude Code Fehler-Report
- Feature: Dashboard Queue-Pause/Fortsetzen-Button
- Feature: Dashboard Auto-Polling wenn aktive Jobs vorhanden
- Feature: ReviewView - Zoom (Mausrad + Buttons), Pan, Download, In-neuem-Tab-oeffnen

### v1.0.4
- Fix: GUI-Installer - Erkennung bestehender Installation und Migrations-Dialog
- Fix: GUI-Installer - semantischer Versionsvergleich (Downgrade-Warnung)

### v1.0.3
- Fix: GUI-Installer (`install-gui.ps1`) - Absturz beim Klick auf Watch-Ordner-Checkbox behoben (`$btnBrowse` war als lokale Variable nicht im Event-Handler-Scope verfuegbar)

### v1.0.2
- Fix: `app`-Namenskonflikt in der `lifespan`-Funktion behoben
- Feature: Watch-Ordner Startup-Scan, UI-konfigurierbare Ordner, Export-Ordner

### v1.0.1
- Fix: Docker Health-Checks auf curl-freie Alternativen umgestellt, ChromaDB Health-Check hinzugefuegt

### v1.0.0
- Erstveroeffentlichung mit vollem Feature-Set (RAG-Assistent, PIN-Schutz, Ablagebereiche, Steuerrelevanz-Checkbox)

## Entwicklung

### Tests ausfuehren

```bash
cd backend
pip install -r requirements.txt
python -m pytest tests/ -v
```

### Releases erstellen

Ein neues Release wird automatisch ueber GitHub Actions erstellt:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Dies erzeugt:
- GitHub Release mit Installer-Archiv (ZIP + tar.gz)
- Docker Images auf `ghcr.io/phash/zettelwirtschaft/backend` und `frontend`
