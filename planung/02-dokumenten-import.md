# Prompt 02 - Dokumenten-Import-Pipeline

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem fuer Privathaushalte. Das Projekt-Setup aus Prompt 01 ist abgeschlossen: FastAPI-Backend, SQLite-Datenbank, Docker Compose laufen. Die Konfiguration (`config.py`) definiert bereits `UPLOAD_DIR`, `WATCH_DIR` und `ARCHIVE_DIR`.

## Aufgabe

Implementiere die Dokumenten-Import-Pipeline. Dokumente koennen auf drei Wegen ins System gelangen:

1. **HTTP-Upload** ueber die REST-API (fuer Smartphone-App und Web-UI)
2. **Watch-Ordner** der automatisch ueberwacht wird (fuer Netzwerk-Scanner)
3. **Netzlaufwerk-Integration** (SMB-Share als Watch-Ordner mountbar)

## Anforderungen

### 1. Upload-Endpoint

`POST /api/documents/upload`

- Akzeptiert eine oder mehrere Dateien (Multipart-Upload)
- Validierung:
  - Dateityp gegen `ALLOWED_FILE_TYPES` aus Config pruefen
  - Dateigroesse gegen `MAX_UPLOAD_SIZE_MB` pruefen
  - Grundlegende Datei-Integritaet (z.B. ist die PDF tatsaechlich eine PDF?)
- Speichert Originaldatei in `UPLOAD_DIR` mit eindeutigem Dateinamen (UUID + Originalname)
- Erzeugt einen Eintrag in der Verarbeitungs-Queue mit Status `PENDING`
- Gibt sofort eine Antwort mit Dokument-ID und Status zurueck (nicht blockierend)

Response-Schema:
```json
{
  "document_id": "uuid",
  "original_filename": "rechnung_2024.pdf",
  "status": "PENDING",
  "message": "Dokument wurde zum Verarbeiten eingereicht."
}
```

### 2. Watch-Ordner-Service

Ein Hintergrund-Service der den konfigurierten `WATCH_DIR` ueberwacht:

- Nutze `watchdog` (Python-Bibliothek) fuer Dateisystem-Events
- Reagiert auf neue Dateien (CREATE-Event)
- Wartet kurz (2 Sekunden), bis die Datei vollstaendig geschrieben ist (wichtig fuer Scanner und Netzlaufwerke)
- Fuehrt dieselbe Validierung wie der Upload-Endpoint durch
- Verschiebt gueltige Dateien nach `UPLOAD_DIR`
- Erstellt Queue-Eintrag mit Status `PENDING`
- Loggt ungueltige Dateien und verschiebt sie in einen `WATCH_DIR/rejected/`-Unterordner
- Laeuft als Background-Task im FastAPI-Startup

### 3. Verarbeitungs-Queue

Eine einfache, datenbankbasierte Queue (kein externer Message-Broker):

SQLAlchemy-Modell `ProcessingJob`:
```
id: UUID (Primary Key)
original_filename: String
stored_filename: String (UUID-basiert)
file_path: String (relativer Pfad in UPLOAD_DIR)
file_type: String (pdf, jpg, etc.)
file_size_bytes: Integer
source: Enum (UPLOAD, WATCH_FOLDER)
status: Enum (PENDING, PROCESSING, COMPLETED, FAILED, NEEDS_REVIEW)
error_message: String (nullable)
created_at: DateTime
updated_at: DateTime
```

Alembic-Migration fuer dieses Modell erstellen.

### 4. Queue-Worker

Ein Hintergrund-Worker der die Queue abarbeitet:

- Pollt alle 5 Sekunden nach `PENDING`-Jobs (konfigurierbar)
- Setzt Status auf `PROCESSING`
- Fuehrt die Verarbeitungs-Pipeline aus (in Prompt 03 implementiert - vorerst Platzhalter):
  1. OCR ausfuehren
  2. KI-Analyse starten
  3. Dokument archivieren
- Bei Erfolg: Status auf `COMPLETED`
- Bei Fehler: Status auf `FAILED`, Fehler loggen, bis zu 3 Retries
- Verarbeitet ein Dokument gleichzeitig (kein Parallelismus noetig auf Heim-Hardware)

### 5. Status-Endpoint

`GET /api/documents/{document_id}/status`

- Gibt aktuellen Verarbeitungsstatus zurueck
- Bei `COMPLETED`: Zusaetzlich Metadaten des analysierten Dokuments

`GET /api/jobs`

- Liste aller Verarbeitungsjobs mit Paginierung
- Filterbar nach Status

### 6. Datei-Management

- Originaldateien behalten ihren Inhalt, bekommen aber UUID-Dateinamen
- Mapping Original-Name zu UUID-Name in der Datenbank
- Thumbnails fuer die spaetere Anzeige im Frontend generieren (fuer PDFs: erste Seite als PNG, fuer Bilder: verkleinerte Version)
- Nutze `Pillow` fuer Bildverarbeitung und `pdf2image` fuer PDF-Thumbnails

## Technische Details

### Neue Dependencies (requirements.txt ergaenzen):
- `watchdog` (Dateisystem-Ueberwachung)
- `python-multipart` (Datei-Upload in FastAPI)
- `Pillow` (Bildverarbeitung)
- `pdf2image` (PDF zu Bild - benoetigt poppler im Docker-Container)
- `python-magic` (MIME-Type-Erkennung)

### Docker-Anpassung:
- `poppler-utils` im Backend-Dockerfile installieren (fuer pdf2image)
- `libmagic` installieren (fuer python-magic)

## Akzeptanzkriterien

- [ ] PDF-Upload ueber `/api/documents/upload` speichert Datei und erstellt Queue-Eintrag
- [ ] JPG/PNG-Upload funktioniert analog
- [ ] Datei in `WATCH_DIR` legen erzeugt automatisch einen Queue-Eintrag
- [ ] Ungueltige Dateien (z.B. .exe) werden abgelehnt mit klarer Fehlermeldung
- [ ] Zu grosse Dateien werden abgelehnt
- [ ] Thumbnails werden fuer PDFs und Bilder generiert
- [ ] Queue-Worker nimmt `PENDING`-Jobs auf und setzt Status auf `PROCESSING`
- [ ] `/api/documents/{id}/status` zeigt korrekten Status
- [ ] `/api/jobs` zeigt paginierte Job-Liste
- [ ] Watch-Ordner funktioniert auch ueber ein gemountetes Netzlaufwerk (SMB)
- [ ] Alle Operationen sind ausreichend geloggt

## Nicht-Ziele dieses Prompts

- Keine OCR-Verarbeitung (kommt in Prompt 03)
- Keine KI-Analyse (kommt in Prompt 03)
- Keine Archivierung nach Analyse (kommt in Prompt 04)
- Kein Frontend (kommt in Prompt 05)
