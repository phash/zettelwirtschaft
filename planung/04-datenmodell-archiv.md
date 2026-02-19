# Prompt 04 - Datenmodell und Archiv-Datenbank

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Die Import-Pipeline (Prompt 02) und KI-Analyse (Prompt 03) sind fertig. Dokumente werden importiert, per OCR ausgelesen und vom LLM analysiert. Die extrahierten Metadaten werden bisher als JSON gespeichert. Jetzt brauchen wir ein strukturiertes Datenmodell.

## Aufgabe

Entwirf und implementiere das vollstaendige Datenmodell fuer das Dokumentenarchiv. Es muss die Ergebnisse der KI-Analyse strukturiert speichern und spaetere Features (Suche, Steuerexport, Garantie-Tracking) ermoeglichen.

## Anforderungen

### 1. SQLAlchemy-Modelle

#### Document (Haupttabelle)

```python
class Document(Base):
    __tablename__ = "documents"

    id: UUID                          # Primaerschluessel
    original_filename: str            # Originaler Dateiname
    stored_filename: str              # UUID-basierter Dateiname im Archiv
    file_path: str                    # Relativer Pfad zur Datei
    thumbnail_path: str | None        # Pfad zum Thumbnail
    file_type: str                    # pdf, jpg, png, etc.
    file_size_bytes: int              # Dateigroesse
    file_hash: str                    # SHA-256 Hash (Duplikaterkennung)

    # KI-extrahierte Metadaten
    document_type: str                # Klassifizierter Dokumenttyp (Enum)
    title: str                        # Von KI generierter Titel
    document_date: date | None        # Datum des Dokuments
    amount: Decimal | None            # Betrag
    currency: str                     # Waehrung (Default: EUR)
    issuer: str | None                # Aussteller
    recipient: str | None             # Empfaenger
    reference_number: str | None      # Referenznummer
    summary: str | None               # KI-Zusammenfassung
    ocr_text: str                     # Vollstaendiger OCR-Text
    ocr_confidence: float             # OCR-Konfidenz

    # Klassifikation
    tax_relevant: bool                # Steuerlich relevant
    tax_year: int | None              # Steuerjahr
    tax_category: str | None          # Steuerkategorie

    # Status
    status: str                       # ACTIVE, ARCHIVED, DELETED
    review_status: str                # OK, NEEDS_REVIEW, REVIEWED
    ai_confidence: float              # KI-Konfidenz gesamt

    # Zeitstempel
    created_at: datetime
    updated_at: datetime
    scanned_at: datetime | None       # Wann eingescannt
```

#### Tag (Schlagwort-System)

```python
class Tag(Base):
    __tablename__ = "tags"

    id: int                           # Auto-Increment
    name: str                         # Tag-Name (unique, lowercase)
    category: str | None              # Optional: Gruppierung (z.B. "steuer", "produkt")
    is_auto_generated: bool           # Von KI oder manuell erstellt
    created_at: datetime
```

#### DocumentTag (Viele-zu-Viele)

```python
class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id: UUID                 # FK -> documents
    tag_id: int                       # FK -> tags
```

#### WarrantyInfo (Garantie-Informationen)

```python
class WarrantyInfo(Base):
    __tablename__ = "warranty_info"

    id: UUID
    document_id: UUID                 # FK -> documents
    product_name: str                 # Produktbezeichnung
    product_category: str | None      # Produktkategorie
    purchase_date: date               # Kaufdatum
    warranty_end_date: date           # Garantieende
    warranty_type: str                # LEGAL (gesetzl.), MANUFACTURER, EXTENDED
    warranty_duration_months: int     # Dauer in Monaten
    retailer: str | None              # Haendler
    is_expired: bool                  # Computed/Property
    reminder_sent: bool               # Erinnerung gesendet
    notes: str | None                 # Benutzernotizen
```

#### ReviewQuestion (Rueckfragen zu Dokumenten)

```python
class ReviewQuestion(Base):
    __tablename__ = "review_questions"

    id: UUID
    document_id: UUID                 # FK -> documents
    question: str                     # Die Frage der KI
    answer: str | None                # Antwort des Benutzers
    field_affected: str | None        # Welches Feld betrifft die Frage
    is_answered: bool
    created_at: datetime
    answered_at: datetime | None
```

#### AuditLog (Aenderungsprotokoll)

```python
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: int                           # Auto-Increment
    document_id: UUID | None          # FK -> documents (nullable fuer System-Events)
    action: str                       # CREATED, UPDATED, EXPORTED, DELETED, etc.
    details: str | None               # JSON mit Aenderungsdetails
    created_at: datetime
```

### 2. Alembic-Migrationen

- Erstelle eine initiale Migration mit allen Tabellen
- Fuege Indizes hinzu fuer:
  - `documents.document_type`
  - `documents.document_date`
  - `documents.tax_relevant`
  - `documents.tax_year`
  - `documents.issuer`
  - `documents.status`
  - `documents.review_status`
  - `documents.file_hash` (Unique)
  - `tags.name` (Unique)
  - `warranty_info.warranty_end_date`
- Volltextindex auf `documents.ocr_text` (SQLite FTS5)

### 3. Pydantic-Schemas

Erstelle Pydantic-Schemas fuer die API (Request/Response):

- `DocumentCreate` - Minimal fuer manuelles Anlegen
- `DocumentResponse` - Vollstaendige Antwort mit allen Feldern
- `DocumentListItem` - Verkuerzte Version fuer Listen (ohne ocr_text)
- `DocumentUpdate` - Felder die der Benutzer manuell korrigieren kann
- `TagResponse`, `TagCreate`
- `WarrantyInfoResponse`
- `ReviewQuestionResponse`, `ReviewQuestionAnswer`

### 4. Archivierungs-Service (`services/archive_service.py`)

Verantwortlich fuer das Verschieben und Organisieren von Dateien:

- Nach erfolgreicher Analyse: Datei von `UPLOAD_DIR` nach `ARCHIVE_DIR` verschieben
- Ordnerstruktur im Archiv: `ARCHIVE_DIR/{jahr}/{monat}/{document_type}/`
  - Beispiel: `archive/2024/03/RECHNUNG/a1b2c3d4_rechnung-telekom.pdf`
- Duplikaterkennung ueber SHA-256-Hash
  - Bei Duplikat: Benutzer informieren, nicht erneut archivieren
- Datenbank-Eintrag mit allen KI-Metadaten erstellen

### 5. CRUD-API-Endpoints

Basis-Endpoints fuer Dokumentenverwaltung:

```
GET    /api/documents                # Liste aller Dokumente (paginiert)
GET    /api/documents/{id}           # Einzelnes Dokument mit allen Metadaten
PATCH  /api/documents/{id}           # Metadaten manuell korrigieren
DELETE /api/documents/{id}           # Dokument loeschen (Soft-Delete)
GET    /api/documents/{id}/file      # Original-Datei herunterladen
GET    /api/documents/{id}/thumbnail # Thumbnail herunterladen
GET    /api/tags                     # Alle Tags auflisten
POST   /api/documents/{id}/tags      # Tag zu Dokument hinzufuegen
DELETE /api/documents/{id}/tags/{tag} # Tag entfernen
```

### 6. Pipeline-Integration

Verbinde den Archivierungs-Service mit der bestehenden Verarbeitungs-Pipeline:

```
Queue-Worker (aus Prompt 02)
  -> OCR (Prompt 03)
  -> LLM-Analyse (Prompt 03)
  -> Archivierungs-Service (NEU):
     -> Datei in Archiv-Ordner verschieben
     -> Document-Eintrag in DB erstellen
     -> Tags erstellen/verknuepfen
     -> WarrantyInfo erstellen (falls relevant)
     -> ReviewQuestions erstellen (falls noetig)
     -> AuditLog-Eintrag schreiben
  -> Job-Status auf COMPLETED setzen
```

## Akzeptanzkriterien

- [ ] Alle Modelle sind als SQLAlchemy-Klassen implementiert
- [ ] Alembic-Migration laeuft fehlerfrei durch
- [ ] Duplikaterkennung ueber Hash funktioniert
- [ ] Archiv-Ordnerstruktur wird korrekt erstellt (Jahr/Monat/Typ)
- [ ] `GET /api/documents` liefert paginierte Liste
- [ ] `GET /api/documents/{id}` liefert alle Metadaten
- [ ] `PATCH /api/documents/{id}` erlaubt manuelle Korrektur
- [ ] Soft-Delete funktioniert (Dokument wird nicht physisch geloescht)
- [ ] Tags koennen hinzugefuegt und entfernt werden
- [ ] Pipeline speichert KI-Ergebnisse korrekt in der Datenbank
- [ ] AuditLog protokolliert alle Aenderungen

## Nicht-Ziele dieses Prompts

- Kein Frontend (kommt in Prompt 05)
- Keine Volltextsuche im Frontend (kommt in Prompt 06)
- Kein Steuerexport (kommt in Prompt 07)
- Kein Garantie-Dashboard (kommt in Prompt 08)
