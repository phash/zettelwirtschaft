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
- **Ablagebereiche** - Dokumente nach Bereichen organisieren (z.B. Privat, Praxis)
- **Rueckfrage-System** - KI fragt bei unklaren Dokumenten gezielt nach

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

4. LLM-Modell herunterladen:
   ```bash
   docker compose exec ollama ollama pull llama3.2
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
- **KI:** Ollama mit lokalem LLM (Llama 3.2 / Mistral)
- **Frontend:** Vue.js 3 + TailwindCSS (PWA)
- **Deployment:** Docker Compose

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
