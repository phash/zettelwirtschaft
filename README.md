# Zettelwirtschaft

Lokales Dokumentenmanagementsystem für Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI analysiert, kategorisiert und durchsuchbar archiviert.

Läuft ausschließlich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

## Features

- **KI-Dokumentenanalyse** - Automatische Erkennung von Typ, Datum, Betrag, Aussteller via Ollama/LLM
- **OCR** - Text aus Scans und PDFs extrahieren (Tesseract + pdfplumber)
- **Volltextsuche** - SQLite FTS5 mit Facetten und Autocomplete
- **Steuerpaket-Export** - Belege nach Steuerkategorien filtern und als ZIP exportieren
- **Garantie-Tracker** - Ablaufdaten im Blick mit automatischen Erinnerungen
- **Smartphone-Scan** - Dokumente per Kamera erfassen (PWA)
- **KI-Assistent (RAG)** - Fragen zu eigenen Dokumenten in natürlicher Sprache stellen
- **Ablagebereiche** - Dokumente nach Bereichen organisieren (z.B. Privat, Praxis)
- **Rückfrage-System** - KI fragt bei unklaren Dokumenten gezielt nach
- **Steuerrelevanz** - Direkt in der Dokumentenliste sichtbar und per Klick änderbar

## Installation (Windows)

Es gibt zwei Installationspfade:

| Pfad | Wann | Voraussetzungen |
|---|---|---|
| **Native** (empfohlen ab v1.3) | Setup.exe, Hintergrunddienst, kein Docker | Windows 10/11, mind. 8 GB RAM, 10 GB Plattenplatz |
| **Docker** | Headless-Server, mehrere Plattformen | Docker Desktop, 8 GB RAM, 10 GB Plattenplatz |

### Pfad A — Native (Setup.exe)

Die Setup.exe installiert Zettelwirtschaft als Windows-Dienst. Browser oeffnet sich
am Ende, im Hintergrund laeuft der Service ohne Konsolenfenster. Aus dem LAN ist
das System unter `http://<rechnername>:8080` erreichbar.

1. [Neuestes Release herunterladen](https://github.com/phash/zettelwirtschaft/releases/latest) → `Zettelwirtschaft-<version>-Native-Setup.exe`
2. Doppelklick. Wizard fragt: Programmordner, Datenordner (frei waehlbar — NAS,
   externe SSD, OneDrive-Sync moeglich), Port, PIN-Auto-Generierung.
3. Setup installiert Tesseract+poppler (gebundled), registriert den Backend-Service
   und erlaubt den Port in der Windows-Firewall (Profil "Privat").
4. Ollama-Installer wird im Anschluss angeboten (optional, ~600 MB Download).
   LLM-Modell (qwen2.5:7b-instruct, ~4.5 GB) wird beim ersten Start gepullt.

Der Service heisst `ZettelwirtschaftBackend` und startet automatisch beim Login.
Steuerbar via `services.msc` oder `net start/stop ZettelwirtschaftBackend`.

Migration aus einer bestehenden Docker-Installation: Der Wizard erkennt die alte
`.env` + Docker-Volumes und bietet eine Konvertierung an. ChromaDB-Volume wird
beim ersten Start re-indexiert (~10 Min bei 1000 Dokumenten).

Details: [`planung/native-windows-konzept.md`](planung/native-windows-konzept.md).

### Pfad B — Docker (klassisch)

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/) installieren und starten
2. Release-Archiv entpacken
3. `install.bat` doppelklicken — Wizard prueft Docker, konfiguriert Ports und LLM-Modell.

> **Hinweis:** Windows zeigt beim ersten Start moeglicherweise eine SmartScreen-Warnung ("Der Computer wurde durch Windows geschuetzt"), da der Installer nicht digital signiert ist. Das ist bei Open-Source-Software normal. Zum Fortfahren: **"Weitere Informationen"** klicken → **"Trotzdem ausfuehren"**. Der vollstaendige Quellcode ist [auf GitHub](https://github.com/phash/zettelwirtschaft) einsehbar.

### Bedienung

| Skript | Beschreibung |
|---|---|
| `start.bat` | System starten und Browser öffnen |
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

5. Im Browser öffnen:
   - **Anwendung:** http://localhost:8080
   - **API-Dokumentation:** http://localhost:8000/docs
   - **Health-Check:** http://localhost:8000/api/health

### Konfiguration

Die Konfiguration erfolgt über die `.env`-Datei. Wichtige Einstellungen:

| Variable | Default | Beschreibung |
|---|---|---|
| `FRONTEND_PORT` | `8080` | Port für die Web-Oberfläche |
| `OLLAMA_MODEL` | `llama3.2` | LLM-Modell (llama3.2 für <=16GB RAM, llama3.1 für >16GB) |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama-Server URL |
| `OCR_LANGUAGES` | `deu+eng` | OCR-Sprachen |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embedding-Modell für RAG-Vektorisierung |
| `RAG_TOP_K` | `5` | Anzahl der relevantesten Textpassagen für RAG-Antworten |

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

### v1.3.0
- **Native-Windows-Modus (Foundation):** Setup.exe → NSSM-Service `ZettelwirtschaftBackend`, PyInstaller-Onedir-Bundle, Frontend über FastAPI `StaticFiles`, ChromaDB embedded (`PersistentClient`), Tesseract + poppler gebündelt, Konfiguration über `config.toml`. Migrationspfad Docker → Native dokumentiert und getestet.
- **Aggressive Abhängigkeits-Migration auf neueste Stable-Versionen:** Frontend **Vite 7 → 8** und **TypeScript 5 → 6** (Major), vue-router 5.1, Pinia 3.0.4, Vue 3.5.38, Tailwind 4.3; Backend FastAPI 0.137, uvicorn 0.49, cryptography 49, SQLAlchemy 2.0.51, pytest 9 / pytest-asyncio 1.x, chromadb 1.5. Plugin-Peer-Kompatibilität für Vite 8 vorab verifiziert (kein `--legacy-peer-deps`).
- **Code-Review-Hardening (Blocker → Low):**
  - Fix (Blocker): `migrate.detect_stamp()` lieferte drei nicht existierende Alembic-Revisions-IDs (001/003/004) → `alembic upgrade head` brach bei alten Legacy-DBs ab und die App startete nicht. Korrigiert + Guard-Test.
  - Fix (High): System-Health prüfte ChromaDB im Native/embedded-Modus fälschlich per HTTP (Status dauerhaft „degraded"); Re-Analyse aktualisiert jetzt FTS-Index + Vektoren (Suche/Chat waren veraltet); Garantie-Update ohne riskanten `session.refresh()` auf einer `lazy="raise"`-Relationship.
  - Fix (Medium): PIN-Lockout mit exponentiellem Backoff statt Reset alle 30 s; statische Frontend-Shell im Native-Modus auch bei aktivem PIN erreichbar (Login-Seite ladbar); Watch-Ordner kopiert und löscht die Quelldatei erst nach DB-Commit (rollback-sicher); Decimal-Konvertierung am Schreib-Rand von `Document.amount`; Vektor-Rebuild-Task mit starker Referenz.
  - Fix (Low): Pillow-Decompression-Bomb-Schutz, gehärtete IMAP-Test-Fehlermeldung, `/api/health` gibt keine DB-Exception-Details mehr preis; Frontend: PWA-Runtime-Caching griff nie (Match gegen `url.href` statt `pathname`), Header-Suche aktualisiert Ergebnisse auch auf der Suchseite, StatCard-Icons in Steuer/Garantie, statischer Auth-/Router-Import (Vite-8-Build ohne Warnungen) und weitere Robustheits-/Hygiene-Fixes.
- Tests: Backend **374** (1 übersprungen), E2E **145** (chromium) grün.

### v1.2.2
- Feature: „Erneut analysieren"-Button bei fehlgeschlagener LLM-Analyse (z.B. wenn Ollama beim Dokumentimport nicht erreichbar war)
- Neuer Endpunkt `POST /api/review/documents/{id}/reanalyze` — führt LLM-Analyse mit vorhandenem OCR-Text erneut durch
- Fix: `entrypoint.sh` CRLF-Zeilenenden korrigiert (lokaler Docker-Build schlug fehl)

### v1.2.1
- Fix: PDF-Vorschau in Dokumentendetails zeigte die App statt das Dokument (Service Worker fing `/api/.../file` Requests ab und lieferte `index.html` als Fallback)
- Fix: `navigateFallbackDenylist` für `/api/` in PWA-Konfiguration
- Fix: Datei- und Thumbnail-Endpunkte auf `NetworkOnly` (keine SW-Cache-Interferenz)
- AGPL-3.0 Lizenz hinzugefügt
- SmartScreen-Hinweis in Installationsanleitung

### v1.2.0
- **ReviewView: Kontext für KI-Rückfragen** — Kontext-Cards unter jeder Rückfrage zeigen betroffenes Feld und erkannten KI-Wert an. Erkannte-Daten-Block hebt das betroffene Feld der aktiven Frage visuell hervor (amber Highlighting).
- **Umlaute im gesamten Frontend** — Alle sichtbaren UI-Texte von ASCII-Ersetzungen (ue/oe/ae) auf echte deutsche Umlaute (ü/ö/ä) umgestellt. Betrifft ReviewView, Sidebar, Dashboard, DocumentDetail, Documents, Search, Chat, Settings, Tax und E2E-Tests.

### v1.1.1
- Fix (kritisch): Datenbankmigrationen laufen jetzt automatisch beim Container-Start (`alembic upgrade head` via `entrypoint.sh`)
- Fix: Legacy-Installationen ohne `alembic_version`-Tracking werden automatisch erkannt und korrekt gestempelt (betrifft Updates von v1.0.x auf v1.1.x)

### v1.1.0
- **Umfassender Audit + Bugfix-Release (43 Fixes)**
- Fix (kritisch): Datei wird erst nach DB-Flush verschoben (verhindert Datenverlust bei DB-Fehler)
- Fix (kritisch): SQLite-Backup via `sqlite3.backup()` API statt Dateikopie (konsistente Sicherung bei laufenden Schreibvorgängen)
- Fix (kritisch): `WarrantyInfo.document` Relationship mit `lazy="selectin"` (verhindert N+1 Queries und MissingGreenlet)
- Fix (kritisch): Login mit falschem PIN gibt HTTP 401 statt 200 zurück
- Fix: Garantie-Erinnerungen für30-Tage und Ablauf-Schwelle (separate Flags pro Schwelle statt einzelnes Boolean)
- Fix: ChromaDB-Suche blockiert nicht mehr den Event Loop (`asyncio.to_thread`)
- Fix: Tesseract-OCR läuft nur noch einmal pro Bild (doppelter Aufruf eliminiert, Performanceverbesserung)
- Fix: XSS-Risiko bei Suchergebnis-Highlights behoben (`v-html` durch sichere Text-Interpolation ersetzt)
- Fix: Chat-Eingabe auf Mobilgeräten nicht mehr hinter BottomNav verborgen
- Fix: Settings-Seite flasht nicht mehr alle 10 Sekunden beim Health-Polling
- Fix: Division-durch-Null in Garantie-Fortschrittsbalken und Steuer-Kategoriebalken
- Fix: Doppelte API-Calls bei Filterwechsel in Dokumentenliste eliminiert
- Fix: Sortierungswechsel in Suche setzt Seite auf 1 zurück
- Fix: Retry in Dashboard startet Polling und aktualisiert Jobansicht
- Fix: UploadView räumt Polling-Interval bei Navigation auf (Memory-Leak behoben)
- Fix: PIN-Schutz bleibt bei Backend-Netzwerkfehler aktiv (sicherer Default)
- Fix: ReviewQuestionResponse-Schema um 4 fehlende Felder erweitert (question_type, explanation, suggested_answers, priority)
- Fix: Tag.documents Relationship auf `lazy="noload"` (unnützes Laden aller Dokumente pro Tag verhindert)
- Fix: Atomare Job-Übernahme im Queue-Worker (optimistic locking statt TOCTOU)
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
- Fix: POST /saved-searches gibt HTTP 201 statt 200 zurück
- Fix: 404-Catch-All-Route im Frontend-Router (unbekannte URLs leiten zum Dashboard)
- Fix: Touch-Event-Support fürBild-Pan/Zoom in ReviewView (Smartphone-Unterstützung)
- Fix: Review-Skip setzt review_status auf OK statt No-Op
- Fix: App-Version dynamisch aus VERSION-Datei statt hardcodiert
- Fix: Notifications-Endpoint mit Paginierung (limit/offset statt hardcodiertem limit 50)
- Fix: sort_by/sort_order Query-Parameter mit Regex-Validierung
- Fix: models/__init__.py um SystemSetting und ChatMessage Imports ergänzt
- Migration: 008_add_warranty_reminder_flags (separate 90d/30d/0d Reminder-Flags)
- Tests: 236 Tests (angepasst an neue HTTP-Statuscodes)

### v1.0.9
- Feature: Host-Ordner fürWatch/Export via Docker-Volume-Mount (Windows-Pfade wie `V:\Zettelwirtschaft` werden als Docker-Volume gemountet)
- Feature: Installationspfad in Settings anzeigen mit "Ordner öffnen"-Button (kopiert Explorer-Befehl)
- Feature: Settings Auto-Refresh (Health-Status alle 10 Sekunden aktualisiert)
- Fix: Ablagebereich-Dropdown immer sichtbar + Schnell-Anlegen per "+"-Button (#15)
- Fix: Fehlgeschlagene Jobs mit Retry-Button, selektierbarer Fehlermeldung und Zeitstempel (#16)
- Fix: ChromaDB-Image auf 0.6.3 gepinnt (Kompatibilität mit chromadb-client 0.6.x)
- Fix: Bessere Fehlermeldungen bei leeren Exceptions im Queue-Worker
- Tests: 237 Tests (Host-Mount-Settings, Retry-Endpoint)

### v1.0.8
- Fix: Dashboard zeigte keine Statistiken (falscher API-Endpunkt `/stats` statt korrektem Pfad)
- Fix: Installer schrieb Version zirkulär aus API (las `data/.version`, schrieb sie zurück) - jetzt direkt aus Installer-Paket
- Fix: Windows-Pfade (z.B. `V:\Ordner`) in Watch/Export-Ordner werden erkannt und mit Warnung markiert (Docker kann nur Container-Pfade lesen)
- Fix: ChromaDB-Fehler zeigt jetzt Hilfetext mit Diagnosebefehlen statt nur "HTTP 404"
- Verbesserung: Ordner-Einstellungen mit Standard-Button und Pfad-Hinweisen
- Tests: 232 Tests (System-Settings Roundtrip, Dashboard-Stats-Struktur)

### v1.0.7
- Fix: Installer-Log zeigte Docker-Fortschrittszeilen hunderte Male (Downloading-Spam durch \r-Output)
- Fix: Progressbar zeigt jetzt animierten Marquee während Docker-Images geladen werden statt einzufrieren

### v1.0.6
- Fix: Setup.exe enthielt keine VERSION-Datei - Installer zeigte immer "v1.0.3" als neue Version
- Fix: Fallback-Version im Installer von "1.0.3" auf "unbekannt" geändert

### v1.0.5
- Feature: Installierte Version im Sidebar-Footer und in Einstellungen → System angezeigt
- Feature: `/api/system/health` liefert jetzt `app_version` aus `data/.version`
- Fix: Installer schreibt nach Install tatsächliche Backend-Version (nicht mehr aus VERSION-Datei)
- Fix: Installer-Erkennung einer bestehenden Installation robuster (version-Datei allein reicht)
- Fix: Versionsvergleich im Installer - Downgrade wird als Warnung angezeigt
- Fix: Ollama-Modell-Download wird übersprungen wenn Modell bereits vorhanden
- Feature: Dashboard zeigt fehlgeschlagene Jobs mit "Kopieren"-Button fürClaude Code Fehler-Report
- Feature: Dashboard Queue-Pause/Fortsetzen-Button
- Feature: Dashboard Auto-Polling wenn aktive Jobs vorhanden
- Feature: ReviewView - Zoom (Mausrad + Buttons), Pan, Download, In-neuem-Tab-öffnen

### v1.0.4
- Fix: GUI-Installer - Erkennung bestehender Installation und Migrations-Dialog
- Fix: GUI-Installer - semantischer Versionsvergleich (Downgrade-Warnung)

### v1.0.3
- Fix: GUI-Installer (`install-gui.ps1`) - Absturz beim Klick auf Watch-Ordner-Checkbox behoben (`$btnBrowse` war als lokale Variable nicht im Event-Handler-Scope verfügbar)

### v1.0.2
- Fix: `app`-Namenskonflikt in der `lifespan`-Funktion behoben
- Feature: Watch-Ordner Startup-Scan, UI-konfigurierbare Ordner, Export-Ordner

### v1.0.1
- Fix: Docker Health-Checks auf curl-freie Alternativen umgestellt, ChromaDB Health-Check hinzugefügt

### v1.0.0
- Erstveröffentlichung mit vollem Feature-Set (RAG-Assistent, PIN-Schutz, Ablagebereiche, Steuerrelevanz-Checkbox)

## Entwicklung

### Tests ausführen

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

### Native-Build (Setup.exe)

Lokaler Build der `Zettelwirtschaft-<version>-Native-Setup.exe`:

```powershell
# Voraussetzungen am Build-Host:
# - Python 3.12, Node 22, NSIS 3.x (makensis im PATH)
# - tools/tesseract/, tools/poppler/, tools/nssm/win64/nssm.exe vorbereitet
#   (manueller Download — siehe scripts/build-native.ps1 Header)

# Build-Dependencies installieren:
pip install -r backend/requirements.txt -r backend/requirements-build.txt

# Full Build (Backend + Frontend + Installer):
pwsh scripts/build-native.ps1

# Output: Zettelwirtschaft-<version>-Native-Setup.exe im Repo-Root
# plus dist/native/ mit dem entpackten Bundle.
```

Komponenten des Native-Builds:
- **Backend:** PyInstaller-Onedir-Bundle (`backend/zettelwirtschaft.spec`)
- **Frontend:** Vite-Static-Build, vom Backend ueber `FRONTEND_DIST_DIR` serviert
- **Tesseract + poppler:** gebundled aus `tools/`
- **NSSM:** Windows-Service-Wrapper
- **ChromaDB:** embedded (`PersistentClient`, kein separater HTTP-Service)
- **Konfiguration:** `config.toml` (vom Installer generiert, vom Backend via `ZETTELWIRTSCHAFT_CONFIG` env gelesen)

## Lizenz

Dieses Projekt steht unter der [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

Copyright (C) 2026 Manuel Rödig
