# Prompt 07 - Steuerpaket-Export fuer den Steuerberater

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Dokumente werden automatisch per KI analysiert und als steuerrelevant oder nicht-steuerrelevant klassifiziert. Die Steuerkategorie (Werbungskosten, Handwerkerleistungen, etc.) und das Steuerjahr werden automatisch erkannt. Suche und Filter funktionieren. Jetzt soll der Benutzer steuerrelevante Dokumente zusammenstellen und als Paket fuer den Steuerberater exportieren koennen.

## Aufgabe

Implementiere ein Steuer-Modul das steuerrelevante Dokumente nach Jahr und Kategorie organisiert und als strukturiertes ZIP-Paket exportiert. Der Steuerberater soll die Unterlagen ohne Rueckfragen nutzen koennen.

## Anforderungen

### 1. Steuerkategorien definieren

Erstelle ein Enum/Mapping der gaengigen Steuerkategorien fuer Privatpersonen:

```python
TAX_CATEGORIES = {
    "werbungskosten": {
        "label": "Werbungskosten",
        "description": "Ausgaben im Zusammenhang mit der Berufstaetigkeit",
        "examples": ["Fachliteratur", "Arbeitsmittel", "Fortbildung", "Fahrtkosten"],
        "elster_anlage": "Anlage N"
    },
    "handwerkerleistungen": {
        "label": "Handwerkerleistungen (§35a)",
        "description": "Handwerker- und Renovierungsarbeiten im Haushalt",
        "examples": ["Malerarbeiten", "Sanitaer", "Elektrik", "Gartenarbeit"],
        "elster_anlage": "Anlage Haushaltsnahe Aufwendungen"
    },
    "haushaltsnahe_dienstleistungen": {
        "label": "Haushaltsnahe Dienstleistungen (§35a)",
        "description": "Dienstleistungen im und ums Haus",
        "examples": ["Reinigung", "Gartenpflege", "Kinderbetreuung"],
        "elster_anlage": "Anlage Haushaltsnahe Aufwendungen"
    },
    "sonderausgaben": {
        "label": "Sonderausgaben",
        "description": "Versicherungen, Vorsorge, Spenden",
        "examples": ["Krankenversicherung", "Spenden", "Kirchensteuer"],
        "elster_anlage": "Anlage Vorsorgeaufwand / Anlage Sonderausgaben"
    },
    "aussergewoehnliche_belastungen": {
        "label": "Aussergewoehnliche Belastungen",
        "description": "Krankheitskosten, Pflege, etc.",
        "examples": ["Arztkosten", "Medikamente", "Brille", "Zahnersatz"],
        "elster_anlage": "Anlage Aussergewoehnliche Belastungen"
    },
    "kapitalertraege": {
        "label": "Kapitalertraege",
        "description": "Zinsen, Dividenden, Aktiengewinne",
        "examples": ["Bankzinsen", "Dividenden", "Jahressteuerbescheinigung"],
        "elster_anlage": "Anlage KAP"
    },
    "vermietung": {
        "label": "Vermietung und Verpachtung",
        "description": "Einnahmen und Ausgaben aus Vermietung",
        "examples": ["Mieteinnahmen", "Nebenkosten", "Reparaturen"],
        "elster_anlage": "Anlage V"
    },
    "sonstiges": {
        "label": "Sonstige steuerrelevante Belege",
        "description": "Nicht eindeutig zuordenbar",
        "examples": [],
        "elster_anlage": "-"
    }
}
```

### 2. Steuer-Uebersichtsseite (`/steuer`)

Dashboard fuer steuerrelevante Dokumente:

**Jahresauswahl:**
- Dropdown oben zur Auswahl des Steuerjahres
- Default: aktuelles Jahr

**Kategorie-Uebersicht (Karten-Layout):**
- Eine Karte pro Steuerkategorie
- Jede Karte zeigt:
  - Kategorie-Name und Beschreibung
  - Anzahl Dokumente
  - Gesamtsumme der Betraege
  - Zugehoerige ELSTER-Anlage
  - "Ansehen"-Button -> Dokumentenliste gefiltert auf diese Kategorie

**Zusammenfassung:**
- Gesamtzahl steuerrelevanter Dokumente im gewaehlten Jahr
- Gesamtsumme aller Betraege (nach Kategorie aufgeschluesselt)
- Hinweis falls Dokumente mit `NEEDS_REVIEW` in steuerrelevanter Kategorie sind

**Export-Button:**
- Grosser, auffaelliger "Steuerpaket exportieren"-Button
- Fuer das gewaehlte Jahr

### 3. Export-Service (`services/tax_export_service.py`)

Erstellt ein strukturiertes ZIP-Paket:

```
Steuerpaket_2024.zip
  |
  +-- 00_UEBERSICHT.pdf              # Automatisch generierte Uebersicht
  +-- 00_UEBERSICHT.csv              # Maschinenlesbare Belegliste
  |
  +-- 01_Werbungskosten/
  |     +-- _Belegliste.csv           # Betraege und Details dieser Kategorie
  |     +-- 2024-01-15_Fachbuch_Thalia_29.99EUR.pdf
  |     +-- 2024-03-20_Laptop_MediaMarkt_899.00EUR.pdf
  |
  +-- 02_Handwerkerleistungen/
  |     +-- _Belegliste.csv
  |     +-- 2024-06-10_Malerarbeiten_Mueller_1200.00EUR.pdf
  |
  +-- 03_Sonderausgaben/
  |     +-- _Belegliste.csv
  |     +-- 2024-05-01_Spende_Rotes_Kreuz_100.00EUR.pdf
  |
  ... (weitere Kategorien)
```

#### Dateinamen-Konvention:
`{Datum}_{Kurzbeschreibung}_{Aussteller}_{Betrag}{Waehrung}.{Endung}`

Sonderzeichen und Umlaute in Dateinamen ersetzen (ae, oe, ue, ss).

#### Uebersichts-PDF (`00_UEBERSICHT.pdf`):

Automatisch generiertes PDF (mit `reportlab` oder `weasyprint`) das enthaelt:
- Name des Steuerpflichtigen (aus Einstellungen, falls konfiguriert)
- Steuerjahr
- Tabelle: Kategorie | Anzahl Belege | Gesamtsumme
- Gesamtsumme aller Kategorien
- Hinweis: "Erstellt mit Zettelwirtschaft am {Datum}"
- Hinweis falls Belege manuell geprueft werden sollten

#### CSV-Dateien:

Pro Kategorie eine `_Belegliste.csv`:
```csv
Datum;Beschreibung;Aussteller;Betrag;Waehrung;Referenznummer;Dateiname
2024-01-15;Fachbuch Python;Thalia;29.99;EUR;INV-2024-001;2024-01-15_Fachbuch_Thalia_29.99EUR.pdf
```

Gesamtuebersicht `00_UEBERSICHT.csv`:
```csv
Kategorie;Anzahl;Gesamtsumme;Waehrung
Werbungskosten;5;1234.56;EUR
Handwerkerleistungen;2;2400.00;EUR
```

### 4. Export-API

```
POST /api/tax/export
```

Request-Body:
```json
{
  "year": 2024,
  "categories": ["werbungskosten", "handwerkerleistungen"],  // Optional, Default: alle
  "include_overview_pdf": true,
  "include_csv": true,
  "taxpayer_name": "Max Mustermann"  // Optional
}
```

Response: ZIP-Datei als Download

```
GET /api/tax/summary/{year}
```

Response: Zusammenfassung fuer das Dashboard (Kategorien, Zaehler, Summen)

### 5. Manuelle Zuordnung

Benutzer muss Dokumente manuell zur Steuer hinzufuegen oder entfernen koennen:

- In der Dokumenten-Detailansicht: Toggle "Steuerrelevant" und Dropdown "Steuerkategorie"
- In der Steuer-Uebersicht: "Dokument hinzufuegen"-Button (oeffnet Dokumenten-Suche)
- Drag-and-Drop von der Dokumentenliste in eine Steuerkategorie (optional, nice-to-have)

### 6. Validierung vor Export

Vor dem Export pruefen und warnen:

- Dokumente ohne Betrag in steuerrelevanter Kategorie
- Dokumente mit Status `NEEDS_REVIEW`
- Dokumente ohne Datum
- Doppelte Belege (gleicher Aussteller + Betrag + Datum)
- Ungewoehnlich hohe Einzelbetraege (> 5.000 EUR) als Hinweis

Warnungen anzeigen, Export aber trotzdem ermoeglichen (der Benutzer entscheidet).

## Technische Details

### Neue Dependencies:
- `reportlab` oder `weasyprint` (PDF-Generierung)
- Python `zipfile` (Standardbibliothek, fuer ZIP-Erstellung)
- Python `csv` (Standardbibliothek)

### Performance:
- ZIP-Erstellung im Hintergrund falls > 50 Dokumente
- Fortschrittsanzeige im Frontend
- Generiertes ZIP temporaer speichern, nach Download loeschen

## Akzeptanzkriterien

- [ ] Steuer-Uebersicht zeigt alle Kategorien mit korrekten Zaehler und Summen
- [ ] Jahresauswahl filtert korrekt
- [ ] ZIP-Export enthaelt alle steuerrelevanten Dokumente des gewaehlten Jahres
- [ ] Ordnerstruktur im ZIP entspricht den Steuerkategorien
- [ ] Dateinamen sind sprechend und enthalten Datum, Beschreibung, Betrag
- [ ] Uebersichts-PDF wird korrekt generiert
- [ ] CSV-Dateien sind korrekt formatiert (Semikolon-getrennt, UTF-8)
- [ ] Benutzer kann Steuerkategorie manuell aendern
- [ ] Validierungswarnungen werden vor Export angezeigt
- [ ] Export funktioniert auch mit > 100 Dokumenten performant

## Nicht-Ziele dieses Prompts

- Keine ELSTER-Integration (zu komplex fuer V1, evtl. spaetere Version)
- Keine automatische Steuererkennung verbessern (die KI-Klassifikation aus Prompt 03 wird genutzt)
- Keine Mehrfach-Steuerpflichtigen-Verwaltung (1 Haushalt = 1 Steuerpflichtiger)
