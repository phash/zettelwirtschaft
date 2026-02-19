# Prompt 10 - Installation, Deployment und Wartung

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Alle Features sind implementiert (Import, KI-Analyse, Web-UI, Suche, Steuer, Garantien, Smartphone). Das System muss jetzt so verpackt werden, dass ein technisch nicht versierter Privatanwender es installieren und warten kann. Zielplattform: Ein handelsruebelicher PC oder NAS im Heimnetzwerk.

## Aufgabe

Erstelle eine produktionsreife Docker-Compose-Konfiguration, einen Installations-Assistenten und Wartungswerkzeuge. Die Installation soll so einfach wie moeglich sein - idealerweise ein einziger Befehl.

## Anforderungen

### 1. Docker Compose (Produktionsreif)

`docker-compose.yml` mit folgenden Services:

```yaml
services:
  backend:
    # Python/FastAPI
    # Haengt von ollama ab
    # Volumes: ./data (Dokumente), ./config (Konfiguration)
    # Port: 8000 (nur intern, Nginx macht den Proxy)
    # Healthcheck konfiguriert
    # Restart: unless-stopped
    # Ressourcen-Limits: memory, cpu

  frontend:
    # Nginx mit Vue.js Build
    # Ports: 80, 443
    # Haengt von backend ab
    # Selbst-signiertes TLS
    # mDNS (zettelwirtschaft.local)
    # Restart: unless-stopped

  ollama:
    # Ollama LLM Server
    # Port: 11434 (nur intern)
    # Volume: ollama-models (persistent)
    # GPU-Passthrough falls verfuegbar (nvidia)
    # Restart: unless-stopped
    # Healthcheck konfiguriert

volumes:
  ollama-models:
  app-data:
```

### 2. Installations-Skript (`install.sh` / `install.bat`)

Ein interaktives Installationsskript fuer Linux/Mac und Windows:

```bash
#!/bin/bash
# Zettelwirtschaft - Installationsassistent

echo "=== Zettelwirtschaft Installationsassistent ==="
echo ""

# 1. Voraussetzungen pruefen
check_docker
check_disk_space  # Mindestens 10 GB frei (LLM-Modell ist gross)
check_memory      # Mindestens 8 GB RAM empfohlen

# 2. Konfiguration abfragen
ask_data_directory        # Wo sollen Dokumente gespeichert werden?
ask_watch_folder          # Soll ein Watch-Ordner eingerichtet werden? Pfad?
ask_network_share         # Netzlaufwerk einbinden? (SMB-Pfad)
ask_language_model        # Welches LLM? (Empfehlung: llama3.2 fuer < 16GB RAM, llama3.1 fuer >= 16GB)
ask_port                  # Port aendern? (Default: 80/443)

# 3. Konfiguration generieren
generate_env_file
generate_docker_compose

# 4. System starten
docker compose pull
docker compose up -d

# 5. LLM-Modell herunterladen
echo "Lade KI-Modell herunter (dies kann einige Minuten dauern)..."
docker compose exec ollama ollama pull $MODEL

# 6. Abschluss
echo ""
echo "Installation abgeschlossen!"
echo "Oeffne im Browser: http://zettelwirtschaft.local"
echo "Oder: http://$(hostname -I | awk '{print $1}')"
```

### 3. Ersteinrichtungs-Assistent (Web)

Beim allerersten Start zeigt die Web-Oberflaeche einen Setup-Wizard:

**Schritt 1: Willkommen**
- Kurze Erklaerung was Zettelwirtschaft macht
- Sprachauswahl (vorerst nur Deutsch)

**Schritt 2: KI-Status pruefen**
- Prueft ob Ollama laeuft und ein Modell geladen ist
- Falls nicht: Anleitung zum Modell-Download mit Fortschrittsanzeige
- Test: Ein Beispieltext analysieren lassen

**Schritt 3: Speicher konfigurieren**
- Wo werden Dokumente gespeichert? (Anzeige des konfigurierten Pfads)
- Speicherplatz-Anzeige
- Optional: Netzlaufwerk testen

**Schritt 4: Watch-Ordner einrichten**
- Erklaerung was der Watch-Ordner ist
- Pfad anzeigen/aendern
- Hinweis: "Konfigurieren Sie Ihren Scanner so, dass er in diesen Ordner scannt"

**Schritt 5: Test-Dokument**
- Ein mitgeliefertes Beispiel-Dokument verarbeiten lassen
- Zeigen wie die KI es analysiert
- Benutzer sieht das Ergebnis und versteht den Workflow

**Schritt 6: Smartphone verbinden**
- QR-Code anzeigen
- Anleitung: "Scannen Sie diesen Code mit Ihrem Smartphone"
- Anleitung: Zum Homescreen hinzufuegen

**Schritt 7: Fertig**
- Zusammenfassung der Konfiguration
- Link zum Dashboard
- Hinweis auf Hilfe/Dokumentation

### 4. Gesundheits-Dashboard (`/einstellungen/system`)

Eine System-Statusseite fuer den Benutzer:

- **System-Status:**
  - Backend: OK/Fehler
  - Datenbank: OK/Fehler + Groesse
  - Ollama/KI: OK/Fehler + Modellname
  - Speicherplatz: Verfuegbar / Warnung wenn < 2 GB
- **Statistiken:**
  - Gesamtzahl Dokumente
  - Datenbankgroesse
  - Speicherplatzverbrauch (Dokumente, Thumbnails, Datenbank)
  - Uptime
- **Wartungsaktionen:**
  - "Datenbank optimieren" (SQLite VACUUM)
  - "Thumbnails neu generieren"
  - "Suchindex neu aufbauen"
  - "Logs herunterladen" (fuer Support)

### 5. Backup und Wiederherstellung

#### Backup-Service (`services/backup_service.py`):

- **Automatisches Backup:**
  - Taeglich um 03:00 Uhr (konfigurierbar)
  - Sichert: SQLite-Datenbank + Konfiguration
  - NICHT die Dokument-Dateien (die liegen bereits auf dem Dateisystem/NAS)
  - Rotation: Letzte 7 Tages-Backups + 4 Wochen-Backups behalten
  - Backup-Verzeichnis konfigurierbar (z.B. auf NAS)

- **Manuelles Backup:**
  - Button im System-Dashboard
  - Option: Komplett-Backup inkl. aller Dokumente (als grosses ZIP)

- **Wiederherstellung:**
  - Upload einer Backup-Datei ueber die Web-UI
  - Oder: Backup-Datei in Backup-Verzeichnis legen und Befehl ausfuehren

#### API:
```
POST   /api/system/backup              # Manuelles Backup erstellen
GET    /api/system/backups             # Liste vorhandener Backups
POST   /api/system/restore/{backup_id} # Backup wiederherstellen
GET    /api/system/backup/download/{id} # Backup herunterladen
```

### 6. Update-Mechanismus

Einfaches Update ohne Datenverlust:

```bash
# Update-Befehl (in README dokumentiert)
docker compose pull
docker compose up -d
```

- Backend fuehrt Alembic-Migrationen automatisch beim Start aus
- Frontend wird automatisch mit dem neuen Image aktualisiert
- Ollama-Modelle bleiben in ihrem Volume erhalten
- Vor Update: Automatisches Backup der Datenbank

### 7. Logging und Fehlerbehebung

- Strukturiertes Logging (JSON-Format) in Dateien
- Log-Rotation (max. 100 MB, 5 Dateien)
- Log-Level ueber Umgebungsvariable steuerbar
- "Diagnose-Paket erstellen"-Button:
  - Sammelt: Logs (letzte 24h), Systeminfo, Konfiguration (ohne sensible Daten)
  - Als ZIP zum Herunterladen

### 8. Ressourcen-Management

Konfiguration fuer verschiedene Hardware:

```yaml
# .env Profile
# Profil: Minimal (Raspberry Pi 4, 4GB RAM)
OLLAMA_MODEL=phi3:mini
WORKER_CONCURRENCY=1
THUMBNAIL_QUALITY=60

# Profil: Standard (PC/NAS, 8GB RAM)
OLLAMA_MODEL=llama3.2
WORKER_CONCURRENCY=1
THUMBNAIL_QUALITY=80

# Profil: Leistung (PC, 16+ GB RAM, GPU)
OLLAMA_MODEL=llama3.1
WORKER_CONCURRENCY=2
THUMBNAIL_QUALITY=85
```

Das Installationsskript fragt die Hardware ab und waehlt das passende Profil.

### 9. Sicherheit

- Selbst-signiertes TLS-Zertifikat wird beim ersten Start automatisch generiert
- Optional: PIN-Schutz fuer die Web-Oberflaeche (in Einstellungen aktivierbar)
- Keine offenen Ports nach aussen (nur LAN)
- SQLite-Datenbank wird nicht ueber das Netzwerk exponiert
- Ollama laeuft nur intern (kein externer Port-Binding)

## Technische Details

### Dockerfile Backend (Multi-Stage):
```dockerfile
# Stage 1: Dependencies
FROM python:3.12-slim as builder
# Install system deps, pip install

# Stage 2: Runtime
FROM python:3.12-slim
# Copy from builder, install runtime deps (tesseract, poppler)
# Non-root user
# Healthcheck
```

### Dockerfile Frontend (Multi-Stage):
```dockerfile
# Stage 1: Build
FROM node:20-alpine as builder
# npm install, npm run build

# Stage 2: Serve
FROM nginx:alpine
# Copy build output, nginx config, TLS cert generation script
```

### Nginx-Konfiguration:
- HTTP -> HTTPS Redirect
- SPA-Routing (try_files)
- API-Proxy: `/api/*` -> `backend:8000`
- Gzip-Kompression
- Security-Headers (X-Frame-Options, etc.)
- Max Upload Size: 50 MB

## Akzeptanzkriterien

- [ ] `install.sh` fuehrt durch die Installation ohne manuelle Docker-Kenntnisse
- [ ] System startet mit `docker compose up -d` ohne Fehler
- [ ] Ersteinrichtungs-Assistent fuehrt neuen Benutzer durch Setup
- [ ] Test-Dokument wird im Wizard erfolgreich verarbeitet
- [ ] System-Dashboard zeigt Gesundheitsstatus aller Komponenten
- [ ] Automatisches Backup laeuft taeglich
- [ ] Manuelles Backup und Wiederherstellung funktionieren
- [ ] Update per `docker compose pull && docker compose up -d` funktioniert ohne Datenverlust
- [ ] Alembic-Migrationen laufen automatisch beim Backend-Start
- [ ] System laeuft stabil auf einem PC mit 8 GB RAM
- [ ] `http://zettelwirtschaft.local` ist im LAN erreichbar
- [ ] HTTPS mit selbst-signiertem Zertifikat funktioniert
- [ ] Diagnose-Paket kann heruntergeladen werden

## Nicht-Ziele dieses Prompts

- Kein App-Store-Deployment
- Kein automatisches Update (bewusste Entscheidung - Benutzer kontrolliert Updates)
- Keine Multi-User-Verwaltung (ein Haushalt = eine Instanz)
- Kein Remote-Zugriff von ausserhalb des LANs
