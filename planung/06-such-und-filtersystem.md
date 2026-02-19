# Prompt 06 - Such- und Filtersystem mit Volltextsuche

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Backend, Datenmodell und Web-Oberflaeche sind fertig. Dokumente haben OCR-Text, Metadaten und Tags in der SQLite-Datenbank. Die Dokumenten-Liste hat bereits einfache Filter (Typ, Datum, Freitext ueber Titel/Aussteller). Jetzt soll eine leistungsfaehige Suche implementiert werden, die den vollstaendigen OCR-Text durchsuchbar macht.

## Aufgabe

Implementiere ein umfassendes Such- und Filtersystem mit SQLite FTS5 Volltextsuche. Ein Privatanwender mit hunderten bis tausenden Dokumenten soll jedes Dokument in Sekunden finden koennen.

## Anforderungen

### 1. SQLite FTS5 Volltextindex

Erstelle eine FTS5-Virtual-Table fuer die Volltextsuche:

```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title,
    ocr_text,
    issuer,
    summary,
    tags,
    content=documents,
    content_rowid=rowid,
    tokenize='unicode61 remove_diacritics 2'
);
```

- Synchronisierung: Trigger oder Service der den FTS-Index bei Aenderungen aktualisiert
- Alembic-Migration fuer die FTS-Tabelle
- Initiales Rebuild des Index fuer bestehende Dokumente

### 2. Such-API

`GET /api/search`

Query-Parameter:
```
q                    # Suchbegriff (Volltextsuche)
document_type        # Filter: Dokumenttyp (mehrere mit Komma)
date_from            # Filter: Datum von (ISO-Format)
date_to              # Filter: Datum bis
amount_min           # Filter: Mindestbetrag
amount_max           # Filter: Hoechstbetrag
issuer               # Filter: Aussteller (Teilstring)
tax_relevant         # Filter: nur steuerrelevante (true/false)
tax_year             # Filter: Steuerjahr
tax_category         # Filter: Steuerkategorie
warranty_active      # Filter: nur mit aktiver Garantie (true/false)
tags                 # Filter: Tags (mehrere mit Komma, AND-Verknuepfung)
status               # Filter: Dokumentstatus
sort_by              # Sortierung: relevance, date, amount, title, created_at
sort_order           # asc oder desc
page                 # Seitenzahl (Default: 1)
page_size            # Ergebnisse pro Seite (Default: 25, Max: 100)
```

Response:
```json
{
  "results": [
    {
      "id": "uuid",
      "title": "Telekom Rechnung Maerz 2024",
      "document_type": "RECHNUNG",
      "document_date": "2024-03-15",
      "amount": 49.99,
      "issuer": "Telekom",
      "thumbnail_url": "/api/documents/uuid/thumbnail",
      "tags": ["telekommunikation", "monatlich"],
      "tax_relevant": true,
      "highlight": "...monatliche <mark>Rechnung</mark> fuer Ihren Festnetz...",
      "relevance_score": 0.95
    }
  ],
  "total": 142,
  "page": 1,
  "page_size": 25,
  "facets": {
    "document_types": {"RECHNUNG": 89, "QUITTUNG": 23, "SONSTIGES": 30},
    "years": {"2024": 67, "2023": 45, "2022": 30},
    "top_issuers": {"Telekom": 12, "Amazon": 8, "IKEA": 5}
  }
}
```

### 3. Suchfunktionen

- **Volltextsuche:** Durchsucht OCR-Text, Titel, Aussteller, Zusammenfassung und Tags
- **Highlighting:** Suchbegriffe in den Ergebnissen hervorheben (FTS5 `highlight()`)
- **Snippet-Generierung:** Relevante Textausschnitte um den Treffer herum (FTS5 `snippet()`)
- **Ranking:** Ergebnisse nach Relevanz sortieren (FTS5 `rank`)
- **Facetten:** Aggregierte Zaehler fuer Filteroptionen (Wie viele Rechnungen? Welche Jahre?)
- **Kombinierte Suche:** Volltextsuche + Metadatenfilter gleichzeitig
- **Suchvorschlaege:** Endpoint fuer Autocomplete waehrend der Eingabe

### 4. Autocomplete-Endpoint

`GET /api/search/suggest?q=tele`

- Liefert Vorschlaege basierend auf: Aussteller-Namen, Tags, haeufigen Suchbegriffen
- Maximal 10 Vorschlaege
- Schnelle Antwort (< 100ms)

### 5. Frontend: Erweiterte Suche

Aktualisiere die Web-Oberflaeche:

**Suchleiste (global, im Header):**
- Prominentes Suchfeld im Header, immer sichtbar
- Autocomplete-Dropdown bei Eingabe
- Enter druecken fuehrt zur Suchergebnis-Seite

**Suchergebnis-Seite (`/suche`):**
- Ergebnisliste mit hervorgehobenen Treffern und Textausschnitten
- Filter-Sidebar links:
  - Dokumenttyp (Checkboxen mit Zaehler aus Facetten)
  - Zeitraum (Datepicker)
  - Betragsbereich (Slider oder Min/Max-Felder)
  - Steuerrelevant (Toggle)
  - Tags (Auswahl)
- Sortierungsoptionen: Relevanz, Datum, Betrag
- "Keine Ergebnisse"-Seite mit hilfreichen Vorschlaegen

**Erweiterte Suche (aufklappbar):**
- Felder fuer gezielte Suche: Aussteller, Referenznummer, Betrag exakt
- Boolesche Operatoren erklaert (AND, OR, NOT - falls FTS5-Syntax genutzt)

### 6. Gespeicherte Suchen

Benutzer kann haeufig verwendete Suchanfragen speichern:

Modell `SavedSearch`:
```python
class SavedSearch(Base):
    id: int
    name: str                         # Vom Benutzer gewaehlter Name
    query_params: str                 # JSON der Suchparameter
    created_at: datetime
```

API:
```
POST   /api/saved-searches            # Suche speichern
GET    /api/saved-searches            # Gespeicherte Suchen auflisten
DELETE /api/saved-searches/{id}       # Gespeicherte Suche loeschen
```

Im Frontend: Gespeicherte Suchen als Schnellzugriff in der Sidebar.

## Technische Details

### FTS5-Besonderheiten:
- Deutsche Umlaute: `unicode61 remove_diacritics 2` sorgt dafuer dass "Rechnung" auch "rëchnung" findet
- Prefix-Suche unterstuetzen (`tele*` findet "Telekom", "Telefon", etc.)
- Phrase-Suche in Anfuehrungszeichen (`"Deutsche Telekom"`)

### Performance-Ziele:
- Volltextsuche ueber 10.000 Dokumente: < 200ms
- Autocomplete: < 100ms
- Facetten-Berechnung: < 500ms

### Alembic:
- FTS5-Tabellen koennen nicht direkt ueber Alembic verwaltet werden
- Erstelle die FTS-Tabelle ueber `op.execute()` in der Migration
- Trigger fuer automatische Synchronisierung

## Akzeptanzkriterien

- [ ] Volltextsuche findet Dokumente anhand von Woertern im OCR-Text
- [ ] Suchbegriffe werden in den Ergebnissen hervorgehoben
- [ ] Textausschnitte um Treffer herum werden angezeigt
- [ ] Ergebnisse sind nach Relevanz sortiert
- [ ] Filter (Typ, Datum, Betrag, Steuer) schraenken Ergebnisse korrekt ein
- [ ] Facetten zeigen korrekte Zaehler
- [ ] Autocomplete liefert sinnvolle Vorschlaege
- [ ] Prefix-Suche funktioniert ("tele" findet "Telekom")
- [ ] Kombinierte Suche (Freitext + Filter) funktioniert
- [ ] Gespeicherte Suchen koennen angelegt und aufgerufen werden
- [ ] Suche ist responsive und funktioniert fluessig

## Nicht-Ziele dieses Prompts

- Keine semantische Suche / Vektorsuche (moegliche Erweiterung fuer die Zukunft)
- Kein Steuerexport (kommt in Prompt 07)
- Kein Garantie-Dashboard (kommt in Prompt 08)
