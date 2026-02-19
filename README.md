# Zettelwirtschaft

Lokales Dokumentenmanagementsystem fuer Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI analysiert, kategorisiert und durchsuchbar archiviert.

Laeuft ausschliesslich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

## Installation

### Voraussetzungen

- [Docker](https://docs.docker.com/get-docker/) und Docker Compose

### Einrichtung

1. Repository klonen:
   ```bash
   git clone <repository-url>
   cd zettelwirtschaft
   ```

2. Konfiguration erstellen:
   ```bash
   cp .env.example .env
   ```

3. Anwendung starten:
   ```bash
   docker compose up -d
   ```

4. Warten bis alle Services bereit sind:
   ```bash
   docker compose ps
   ```

5. Im Browser oeffnen:
   - **Anwendung:** http://localhost
   - **API-Dokumentation:** http://localhost:8000/docs
   - **Health-Check:** http://localhost:8000/api/health

## Verzeichnisstruktur

Nach dem ersten Start werden folgende Verzeichnisse unter `data/` angelegt:

| Verzeichnis | Zweck |
|---|---|
| `data/uploads` | Hochgeladene Originaldokumente |
| `data/watch` | Scanner-Eingabeordner (automatische Erkennung) |
| `data/archive` | Archivierte, verarbeitete Dokumente |

## Technologie-Stack

- **Backend:** Python 3.12 / FastAPI
- **Datenbank:** SQLite (SQLAlchemy + Alembic)
- **KI:** Ollama mit lokalem LLM
- **Frontend:** Vue.js 3 (in Entwicklung)
- **Deployment:** Docker Compose
