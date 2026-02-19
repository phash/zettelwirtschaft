# Prompt 03 - KI-Dokumentenanalyse (OCR + LLM-Klassifikation)

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Die Import-Pipeline (Prompt 02) ist fertig: Dokumente gelangen per Upload oder Watch-Ordner ins System und landen als `PENDING`-Jobs in der Queue. Der Queue-Worker ruft eine Verarbeitungs-Pipeline auf, die jetzt implementiert werden muss.

**Wichtig:** Alles laeuft lokal, ohne Cloud-Anbindung. Fuer die KI wird Ollama mit einem lokalen LLM (z.B. Llama 3.2 oder Mistral) verwendet.

## Aufgabe

Implementiere die KI-gestuetzte Dokumentenanalyse-Pipeline. Sie besteht aus zwei Stufen:
1. **OCR:** Text aus gescannten Dokumenten extrahieren
2. **LLM-Analyse:** Den extrahierten Text analysieren, kategorisieren und Metadaten extrahieren

## Anforderungen

### 1. OCR-Service (`services/ocr_service.py`)

Texterkennung fuer verschiedene Eingabeformate:

- **PDF-Dateien:**
  - Pruefe zuerst, ob die PDF bereits durchsuchbaren Text enthaelt (digitale PDF)
  - Falls ja: Text direkt extrahieren (mit `pdfplumber` oder `PyPDF2`)
  - Falls nein (gescannte PDF): Seiten als Bilder rendern, dann OCR
- **Bilddateien (JPG, PNG, TIFF):**
  - Vorverarbeitung fuer bessere OCR-Ergebnisse:
    - Bild gerade richten (Deskew) falls schief gescannt
    - Kontrast optimieren
    - Rauschen reduzieren
  - OCR mit Tesseract ausfuehren
- **Sprache:** Deutsch als primaere Sprache, Englisch als Fallback (`deu+eng`)
- **Ausgabe:** Strukturierter Text mit Seitenzuordnung

```python
@dataclass
class OcrResult:
    full_text: str                    # Gesamter extrahierter Text
    pages: list[PageText]             # Text pro Seite
    confidence: float                 # Durchschnittliche OCR-Konfidenz (0-100)
    language_detected: str            # Erkannte Sprache
    is_digital_pdf: bool              # Ob Text direkt extrahiert wurde
```

### 2. LLM-Analyse-Service (`services/llm_service.py`)

Kommunikation mit dem lokalen Ollama-Server:

- HTTP-Client fuer Ollama REST-API (`/api/generate` oder `/api/chat`)
- Timeout-Handling (lokale LLMs koennen auf schwacher Hardware langsam sein)
- Retry-Logik bei Verbindungsproblemen
- Fallback wenn Ollama nicht erreichbar: Dokument als `NEEDS_REVIEW` markieren statt Fehler

### 3. Dokumenten-Analyse-Service (`services/analysis_service.py`)

Nutzt den LLM-Service um aus dem OCR-Text strukturierte Metadaten zu extrahieren:

#### Schritt 1: Dokumenttyp erkennen

Prompt an LLM mit dem OCR-Text. Das LLM soll den Dokumenttyp bestimmen:

```
RECHNUNG, QUITTUNG, KAUFVERTRAG, GARANTIESCHEIN, VERSICHERUNGSPOLICE,
KONTOAUSZUG, LOHNABRECHNUNG, STEUERBESCHEID, MIETVERTRAG, HANDWERKER_RECHNUNG,
ARZTRECHNUNG, REZEPT, AMTLICHES_SCHREIBEN, BEDIENUNGSANLEITUNG, SONSTIGES
```

#### Schritt 2: Metadaten extrahieren

Je nach Dokumenttyp unterschiedliche Felder extrahieren. Gemeinsame Felder fuer alle:

```python
@dataclass
class DocumentMetadata:
    document_type: str                # Erkannter Dokumenttyp
    title: str                        # Kurzer beschreibender Titel
    date: date | None                 # Datum des Dokuments
    amount: Decimal | None            # Betrag (falls vorhanden)
    currency: str                     # Waehrung (Default: EUR)
    issuer: str | None                # Aussteller (Firma, Behoerde, etc.)
    recipient: str | None             # Empfaenger
    reference_number: str | None      # Rechnungsnr, Aktenzeichen, etc.
    tax_relevant: bool                # Steuerlich relevant?
    tax_category: str | None          # Steuerkategorie (Werbungskosten, etc.)
    warranty_relevant: bool           # Garantie/Gewaehrleistung relevant?
    warranty_expiry: date | None      # Garantieablauf (falls erkennbar)
    tags: list[str]                   # Automatisch generierte Schlagworte
    summary: str                      # Kurze Zusammenfassung (2-3 Saetze)
    confidence_score: float           # Wie sicher ist die KI (0-1)
    needs_review: bool                # Manuelle Pruefung empfohlen?
    review_questions: list[str]       # Konkrete Fragen falls needs_review=True
```

#### Schritt 3: Steuerrelevanz bewerten

Das LLM soll bewerten, ob und wie ein Dokument steuerlich relevant ist:

- Handwerker-Rechnungen -> Handwerkerleistungen (§35a EStG)
- Arbeitsmittel -> Werbungskosten
- Spendenquittungen -> Sonderausgaben
- Arzt-/Apothekenrechnungen -> Aussergewoehnliche Belastungen
- usw.

#### Schritt 4: Garantie-Informationen extrahieren

Falls es sich um einen Kaufbeleg handelt:

- Gekauftes Produkt identifizieren
- Kaufdatum extrahieren
- Garantiedauer bestimmen (gesetzlich 2 Jahre wenn nicht anders angegeben)
- Haendler identifizieren

### 4. Prompt-Engineering

Erstelle gut strukturierte Prompts fuer das LLM. Wichtige Prinzipien:

- **Klare Anweisungen:** Das LLM soll JSON zurueckgeben
- **Beispiele:** Few-Shot-Prompting mit 2-3 Beispieldokumenten
- **Fehlerfaelle:** Was tun wenn Information nicht erkennbar? -> `null` zurueckgeben
- **Sprache:** Prompts auf Deutsch (da Dokumente deutsch sind)
- **Laenge begrenzen:** OCR-Text ggf. auf die relevantesten Teile kuerzen (Anfang + Ende des Dokuments enthalten meist die wichtigsten Infos)

Speichere Prompts als Template-Dateien unter `backend/app/prompts/`, nicht hardcoded im Code.

### 5. Pipeline-Integration

Verbinde die Services mit dem Queue-Worker aus Prompt 02:

```
Queue-Worker nimmt PENDING-Job
  -> OCR-Service: Text extrahieren
  -> LLM-Service: Dokumenttyp + Metadaten erkennen
  -> Ergebnis in Datenbank speichern (vorlaeufig als JSON, strukturiert in Prompt 04)
  -> Bei niedriger Konfidenz: Status = NEEDS_REVIEW + Fragen generieren
  -> Bei Erfolg: Status = COMPLETED
```

## Technische Details

### Neue Dependencies:
- `pytesseract` (Python-Wrapper fuer Tesseract)
- `pdfplumber` (PDF-Textextraktion)
- `pdf2image` (PDF zu Bild, falls nicht schon vorhanden)
- `httpx` (Async HTTP-Client fuer Ollama-Kommunikation)
- `Pillow` (Bildvorverarbeitung)

### Docker-Anpassung:
- `tesseract-ocr` und `tesseract-ocr-deu` im Backend-Container installieren
- Ollama-Service konfigurieren mit Auto-Pull des gewaehlten Modells beim ersten Start

### Prompt-Dateien:
```
backend/app/prompts/
  classify_document.txt       # Dokumenttyp erkennen
  extract_metadata.txt        # Metadaten extrahieren
  assess_tax_relevance.txt    # Steuerrelevanz bewerten
  extract_warranty_info.txt   # Garantie-Informationen
```

## Akzeptanzkriterien

- [ ] OCR erkennt Text aus einer gescannten PDF (deutschsprachig) mit mindestens 85% Genauigkeit
- [ ] OCR erkennt Text aus JPG/PNG-Fotos von Dokumenten
- [ ] Digitale PDFs werden ohne OCR direkt ausgelesen
- [ ] LLM klassifiziert eine Rechnung korrekt als `RECHNUNG`
- [ ] LLM extrahiert Betrag, Datum, Aussteller aus einer typischen Rechnung
- [ ] Steuerrelevanz wird fuer Handwerker-Rechnung korrekt als `true` erkannt
- [ ] Bei unleserlichem Scan: Status wird `NEEDS_REVIEW`, sinnvolle Fragen werden generiert
- [ ] Pipeline laeuft komplett durch: Upload -> OCR -> LLM -> Ergebnis gespeichert
- [ ] Verarbeitung einer einzelnen DIN-A4-Rechnung (Scan, 300dpi) dauert unter 30 Sekunden
- [ ] System funktioniert auch wenn Ollama nicht laeuft (graceful degradation zu NEEDS_REVIEW)
- [ ] Prompts sind als separate Template-Dateien gespeichert und leicht anpassbar

## Nicht-Ziele dieses Prompts

- Kein persistentes Datenmodell fuer Dokumente (kommt in Prompt 04)
- Keine Web-Oberflaeche fuer Ergebnisanzeige (kommt in Prompt 05)
- Kein interaktives Rueckfragesystem (kommt in Prompt 11)
