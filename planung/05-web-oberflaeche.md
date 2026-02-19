# Prompt 05 - Web-Oberflaeche (Dashboard und Dokumentenansicht)

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Backend, Import-Pipeline, KI-Analyse und Datenmodell sind fertig. Dokumente werden importiert, analysiert und strukturiert in SQLite gespeichert. Die REST-API bietet CRUD-Endpoints unter `/api/documents`. Jetzt braucht das System eine Benutzeroberflaeche.

## Aufgabe

Erstelle eine Web-Oberflaeche mit Vue.js 3 und Vite. Die UI soll klar, aufgeraeumt und fuer technisch nicht versierte Benutzer bedienbar sein. Kein ueberladenes Design - funktional und uebersichtlich.

## Anforderungen

### 1. Projekt-Setup (Frontend)

- Vue.js 3 mit Composition API und `<script setup>`
- Vite als Build-Tool
- Vue Router fuer Navigation
- Pinia fuer State-Management
- Axios fuer API-Kommunikation
- Eine schlichte CSS-Loesung (z.B. PrimeVue, Naive UI, oder TailwindCSS - waehle eine passende)
- Deutsche UI-Texte durchgehend

### 2. Layout und Navigation

Einfaches Layout mit:
- **Seitenleiste (links):** Navigation mit Icons
  - Dashboard (Startseite)
  - Dokumente (Alle Dokumente)
  - Upload (Dokument hochladen)
  - Zu pruefen (Dokumente mit Rueckfragen)
  - Steuer (spaeter, Prompt 07)
  - Garantien (spaeter, Prompt 08)
  - Einstellungen (spaeter)
- **Hauptbereich (rechts):** Seiteninhalt
- **Header:** App-Name, Verbindungsstatus (Backend erreichbar?)

### 3. Dashboard-Seite (`/`)

Ueberblick auf einen Blick:

- **Statistik-Karten:**
  - Gesamtzahl Dokumente
  - Dokumente diesen Monat
  - Offene Rueckfragen (Anzahl, orange hervorgehoben)
  - Ablaufende Garantien naechste 30 Tage
- **Letzte Dokumente:** Die 5 zuletzt importierten Dokumente als Liste mit Thumbnail, Titel, Datum, Typ
- **Verarbeitungs-Queue:** Falls Dokumente gerade verarbeitet werden, Fortschritt anzeigen
- **Schnell-Upload:** Drag-and-Drop-Bereich zum direkten Hochladen

### 4. Dokumenten-Liste (`/dokumente`)

Tabellarische Uebersicht aller Dokumente:

- **Spalten:** Thumbnail (klein), Titel, Typ (als farbiges Badge), Datum, Betrag, Aussteller, Tags
- **Sortierung:** Klick auf Spaltenkoepfe
- **Paginierung:** 25 Dokumente pro Seite
- **Schnellfilter-Leiste oben:**
  - Dokumenttyp (Dropdown-Auswahl)
  - Zeitraum (Von-Bis Datumsauswahl)
  - Nur steuerrelevante (Toggle)
  - Nur mit Garantie (Toggle)
  - Freitext-Suche (Eingabefeld, sucht in Titel, Aussteller, Tags)
- **Batch-Aktionen:** Mehrere Dokumente auswaehlen fuer: Loeschen, Tag hinzufuegen, Exportieren

### 5. Dokumenten-Detailansicht (`/dokumente/:id`)

Zweispaltig:

**Linke Spalte (60%):**
- Dokumenten-Vorschau (PDF inline anzeigen mit pdf.js, Bilder direkt)
- Alternativ: Vollbild-Ansicht oeffnen
- Download-Button fuer Originaldatei

**Rechte Spalte (40%):**
- Alle Metadaten als bearbeitbares Formular:
  - Titel (editierbar)
  - Dokumenttyp (Dropdown, editierbar)
  - Datum (Datepicker, editierbar)
  - Betrag + Waehrung (editierbar)
  - Aussteller (editierbar)
  - Referenznummer (editierbar)
  - Steuerrelevant (Toggle, editierbar)
  - Steuerkategorie (Dropdown, editierbar)
- Tags (als Chips, hinzufuegen/entfernen)
- KI-Zusammenfassung (anzeigen)
- KI-Konfidenz als farbiger Balken (gruen > 0.8, gelb > 0.5, rot < 0.5)
- Garantie-Info Box (falls vorhanden): Produkt, Ablaufdatum, Status
- Rueckfragen-Bereich (falls vorhanden, siehe Prompt 11)
- Speichern-Button fuer manuelle Aenderungen
- Loeschen-Button (mit Bestaetigung)

### 6. Upload-Seite (`/upload`)

- Grosser Drag-and-Drop-Bereich
- Alternativ: Datei-Auswahl-Dialog
- Mehrere Dateien gleichzeitig hochladen
- Fortschrittsanzeige pro Datei
- Nach Upload: Status-Anzeige (Hochgeladen -> Wird verarbeitet -> Fertig)
- Link zum verarbeiteten Dokument sobald fertig

### 7. Zu-Pruefen-Seite (`/pruefen`)

Liste der Dokumente mit Status `NEEDS_REVIEW`:

- Dokument-Vorschau + Rueckfragen der KI nebeneinander
- Formularfelder zum Beantworten der Fragen
- "Bestaetigen"-Button der die Antworten speichert und das Dokument freigibt
- "Ueberspringen"-Button fuer spaeter
- Zaehler: "3 von 7 Dokumenten geprueft"

### 8. API-Service (Frontend)

Zentraler API-Service (`src/services/api.js`):

- Axios-Instanz mit Base-URL aus Umgebungsvariable
- Automatische Fehlerbehandlung (Toast-Benachrichtigungen bei Fehlern)
- Request/Response-Interceptors
- Typisierte API-Funktionen:
  - `getDocuments(filters, page)` -> paginierte Liste
  - `getDocument(id)` -> Einzeldokument
  - `updateDocument(id, data)` -> Metadaten aktualisieren
  - `deleteDocument(id)` -> Loeschen
  - `uploadDocuments(files)` -> Upload mit Fortschritt
  - `getJobStatus(id)` -> Verarbeitungsstatus
  - `getDashboardStats()` -> Statistiken
  - `getReviewDocuments()` -> Dokumente zum Pruefen
  - `answerReviewQuestion(questionId, answer)` -> Frage beantworten

### 9. Responsive Design

- Desktop-optimiert (Hauptnutzung am PC)
- Tablet-tauglich (fuer gelegentliche Nutzung)
- Smartphone: Grundfunktionen nutzbar, Fokus auf Upload (PWA kommt in Prompt 09)

### 10. Docker-Integration

- Nginx-Container fuer das gebaute Frontend
- Nginx-Config: SPA-Routing (alle Pfade auf index.html), API-Proxy zu Backend
- Multi-Stage Dockerfile: Node fuer Build, Nginx fuer Serving

## Akzeptanzkriterien

- [ ] Dashboard zeigt Statistiken und letzte Dokumente korrekt an
- [ ] Dokumenten-Liste laesst sich nach Typ, Datum und Freitext filtern
- [ ] Dokumenten-Detail zeigt Vorschau und alle Metadaten
- [ ] Metadaten koennen manuell bearbeitet und gespeichert werden
- [ ] Datei-Upload per Drag-and-Drop und Dateiauswahl funktioniert
- [ ] Upload-Fortschritt wird angezeigt
- [ ] Pruefseite zeigt Dokumente mit Rueckfragen und erlaubt Beantwortung
- [ ] Navigation ist intuitiv und durchgaengig deutsch
- [ ] Fehler werden als verstaendliche Meldungen angezeigt (keine technischen Details)
- [ ] Frontend wird korrekt ueber Docker/Nginx ausgeliefert
- [ ] API-Proxy in Nginx leitet `/api/*` an das Backend weiter

## Nicht-Ziele dieses Prompts

- Keine Volltextsuche (kommt in Prompt 06)
- Kein Steuer-Export-Bereich (kommt in Prompt 07)
- Kein Garantie-Dashboard (kommt in Prompt 08)
- Keine PWA/Smartphone-Optimierung (kommt in Prompt 09)
- Kein interaktives Rueckfrage-System (kommt in Prompt 11, hier nur Grundgeruest)
