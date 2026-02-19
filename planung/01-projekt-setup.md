# Prompt 01 - Projekt-Setup und Grundarchitektur

## Kontext

Ich entwickle "Zettelwirtschaft", ein lokales Dokumentenmanagementsystem fuer Privathaushalte. Es laeuft ausschliesslich on-premise im Heim-WLAN. Nutzer scannen Rechnungen, Belege und Dokumente ein; eine KI erkennt und kategorisiert den Inhalt automatisch. Das System macht Dokumente durchsuchbar und exportierbar (z.B. Steuerunterlagen, Garantiebelege).

## Aufgabe

Erstelle die Grundstruktur des Projekts mit folgendem Technologie-Stack:

- **Backend:** Python 3.12+ mit FastAPI
- **Datenbank:** SQLite (ueber SQLAlchemy mit Alembic fuer Migrationen)
- **Frontend:** Vue.js 3 mit Vite (wird spaeter entwickelt)
- **Deployment:** Docker Compose

## Anforderungen

### 1. Projektstruktur anlegen

```
zettelwirtschaft/
  backend/
    app/
      __init__.py
      main.py              # FastAPI-App, CORS, Startup
      config.py             # Zentrale Konfiguration (Pydantic Settings)
      database.py           # SQLAlchemy Engine, Session, Base
      models/               # SQLAlchemy-Modelle (spaeter befuellt)
        __init__.py
      schemas/              # Pydantic-Schemas fuer API
        __init__.py
      api/                  # API-Router
        __init__.py
        health.py           # Health-Check-Endpoint
      services/             # Business-Logik
        __init__.py
      core/                 # Hilfsfunktionen
        __init__.py
    alembic/                # Datenbank-Migrationen
    requirements.txt
    Dockerfile
  frontend/                 # Vue.js (wird in Prompt 05 befuellt)
    .gitkeep
  docker-compose.yml
  .env.example
  README.md
```

### 2. Konfiguration (config.py)

Alle Einstellungen ueber Umgebungsvariablen oder `.env`-Datei:

- `DATABASE_URL` (Default: `sqlite:///./data/zettelwirtschaft.db`)
- `UPLOAD_DIR` (Default: `./data/uploads`) - Wo hochgeladene Originaldokumente liegen
- `WATCH_DIR` (Default: `./data/watch`) - Ordner, der auf neue Dateien ueberwacht wird (Scanner-Input)
- `ARCHIVE_DIR` (Default: `./data/archive`) - Archivierte, verarbeitete Dokumente
- `OLLAMA_BASE_URL` (Default: `http://localhost:11434`) - Lokaler LLM-Server
- `OLLAMA_MODEL` (Default: `llama3.2`) - Zu verwendendes Modell
- `MAX_UPLOAD_SIZE_MB` (Default: `50`)
- `ALLOWED_FILE_TYPES` (Default: `pdf,jpg,jpeg,png,tiff,bmp`)
- `LOG_LEVEL` (Default: `INFO`)

### 3. FastAPI-App (main.py)

- CORS-Middleware (fuer lokales Netzwerk: alle Origins erlauben da nur LAN)
- Startup-Event: Datenverzeichnisse erstellen, falls nicht vorhanden
- Health-Check-Endpoint unter `/api/health` der folgendes prueft:
  - API erreichbar
  - Datenbank erreichbar
  - Speicherplatz ausreichend (Warnung unter 1 GB)
  - Ollama-Verbindung (optional, Warnung wenn nicht erreichbar)
- OpenAPI-Docs unter `/docs`

### 4. Datenbank-Setup (database.py)

- SQLAlchemy Async Engine mit SQLite
- Session-Factory
- Base-Klasse fuer Modelle
- Alembic-Konfiguration fuer Migrationen

### 5. Docker Compose (docker-compose.yml)

Drei Services:
- `backend`: Python/FastAPI (Port 8000)
- `ollama`: Ollama LLM-Server (Port 11434), Volume fuer Modelle
- `frontend`: Nginx mit Vue.js Build (Port 80) - vorerst Platzhalter

Gemeinsame Volumes:
- `./data` gemountet in Backend-Container
- Ollama-Modelle persistent

### 6. README.md

Kurze, klare Installationsanleitung:
1. Docker installieren
2. Repository klonen
3. `.env` aus `.env.example` kopieren
4. `docker compose up -d` ausfuehren
5. Im Browser `http://localhost` oeffnen

## Akzeptanzkriterien

- [ ] `docker compose up` startet ohne Fehler
- [ ] `GET /api/health` liefert Status aller Komponenten als JSON
- [ ] `GET /docs` zeigt Swagger-UI
- [ ] Datenverzeichnisse (`uploads`, `watch`, `archive`) werden beim Start automatisch erstellt
- [ ] Alle Konfigurationswerte sind ueber `.env` ueberschreibbar
- [ ] Alembic ist konfiguriert und `alembic revision --autogenerate` funktioniert
- [ ] Keine Abhaengigkeit zu externen Cloud-Diensten

## Nicht-Ziele dieses Prompts

- Keine Dokumenten-Modelle (kommt in Prompt 04)
- Kein Upload-Endpoint (kommt in Prompt 02)
- Kein Frontend (kommt in Prompt 05)
- Keine KI-Integration (kommt in Prompt 03)
