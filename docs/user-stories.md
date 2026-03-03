# Zettelwirtschaft - User Stories

## Uebersicht

| Epic | Stories | P1 | P2 | P3 |
|------|---------|----|----|-----|
| 1. Authentifizierung | 2 | 1 | 1 | 0 |
| 2. Dashboard | 4 | 2 | 1 | 1 |
| 3. Dokument-Upload | 2 | 2 | 0 | 0 |
| 4. Dokumentenliste | 5 | 2 | 2 | 1 |
| 5. Dokumentendetails | 5 | 2 | 2 | 1 |
| 6. Rueckfrage-System | 4 | 2 | 1 | 1 |
| 7. Volltextsuche | 5 | 2 | 2 | 1 |
| 8. Steuerpaket-Export | 3 | 1 | 1 | 1 |
| 9. Garantie-Tracker | 2 | 1 | 1 | 0 |
| 10. KI-Assistent | 3 | 1 | 1 | 1 |
| 11. Kamera-Scan | 1 | 0 | 1 | 0 |
| 12. Einstellungen | 5 | 2 | 2 | 1 |
| 13. E-Mail-Integration | 1 | 0 | 1 | 0 |
| 14. Benachrichtigungen | 1 | 0 | 1 | 0 |
| 15. Responsive Design & Navigation | 3 | 1 | 1 | 1 |
| **Gesamt** | **46** | **21** | **18** | **7** |

Prioritaeten: **P1** = Kernfunktionalitaet, **P2** = Wichtig, **P3** = Nice-to-have

---

## Epic 1: Authentifizierung

### US-1.1: PIN-Login (P1)

**Als** Benutzer
**moechte ich** mich mit einer PIN anmelden
**damit** unbefugte Personen im Heimnetz keinen Zugriff auf meine Dokumente haben.

**Akzeptanzkriterien:**
- [ ] Bei aktiviertem PIN-Schutz (`PIN_ENABLED=true`) werde ich zur PIN-Eingabe (`/pin`) umgeleitet
- [ ] Das PIN-Feld ist ein Passwort-Feld mit numerischer Eingabe und Placeholder `****`
- [ ] Bei korrekter PIN werde ich zur Zielseite (oder Dashboard) weitergeleitet
- [ ] Bei falscher PIN sehe ich eine rote Fehlermeldung
- [ ] Der "Anmelden"-Button ist deaktiviert, solange das PIN-Feld leer ist
- [ ] Waehrend der Pruefung zeigt der Button "Pruefen..." an

### US-1.2: Geschuetzte Routen (P2)

**Als** Benutzer
**moechte ich** dass alle Seiten ausser der PIN-Eingabe geschuetzt sind
**damit** kein direkter URL-Zugriff ohne Authentifizierung moeglich ist.

**Akzeptanzkriterien:**
- [ ] Direkter Zugriff auf `/dokumente`, `/upload`, `/einstellungen` etc. leitet zu `/pin` um
- [ ] Nach erfolgreicher Anmeldung werde ich zur urspruenglich angeforderten Seite zurueckgeleitet
- [ ] `/api/health` ist ohne Authentifizierung erreichbar
- [ ] Bei 401-API-Antwort werde ich automatisch zur PIN-Eingabe umgeleitet

---

## Epic 2: Dashboard

### US-2.1: Statistik-Ueberblick (P1)

**Als** Benutzer
**moechte ich** auf dem Dashboard die wichtigsten Kennzahlen sehen
**damit** ich den Status meines Dokumentenarchivs auf einen Blick erfasse.

**Akzeptanzkriterien:**
- [ ] Statistik-Karten zeigen: Gesamt-Dokumente, Diesen Monat, Offene Rueckfragen, Garantien (30 Tage)
- [ ] E-Mail-Statistik wird nur angezeigt, wenn E-Mail-Konten konfiguriert sind
- [ ] Alle Zahlen werden korrekt aus der API geladen und angezeigt
- [ ] Die Karten haben unterschiedliche Farben (blau, gruen, orange, lila, indigo)

### US-2.2: Letzte Dokumente (P1)

**Als** Benutzer
**moechte ich** die zuletzt hinzugefuegten Dokumente sehen
**damit** ich schnell auf neue Eintraege zugreifen kann.

**Akzeptanzkriterien:**
- [ ] Bis zu 5 Dokumente werden in einer Liste angezeigt
- [ ] Jeder Eintrag ist klickbar und fuehrt zur Detailseite (`/dokumente/{id}`)
- [ ] Ein "Alle anzeigen"-Link fuehrt zur Dokumentenliste
- [ ] Bei leerer Liste erscheint "Noch keine Dokumente vorhanden."

### US-2.3: Verarbeitungs-Queue (P2)

**Als** Benutzer
**moechte ich** den Status laufender Verarbeitungen sehen
**damit** ich weiss, ob und welche Dokumente gerade analysiert werden.

**Akzeptanzkriterien:**
- [ ] Bei aktiven Jobs erscheint eine Queue-Anzeige mit animierten Status-Punkten
- [ ] "Pausieren"/"Fortsetzen"-Button schaltet die Queue um
- [ ] Fehlgeschlagene Jobs werden separat angezeigt mit Fehlermeldung
- [ ] Pro fehlgeschlagenem Job gibt es "Wiederholen"- und "Kopieren"-Buttons
- [ ] Auto-Polling (3s) aktualisiert den Status bei aktiven Jobs

### US-2.4: Quick-Upload (P3)

**Als** Benutzer
**moechte ich** Dokumente direkt vom Dashboard per Drag-and-Drop hochladen
**damit** ich nicht erst zur Upload-Seite navigieren muss.

**Akzeptanzkriterien:**
- [ ] Drag-and-Drop-Bereich akzeptiert PDF, JPG, PNG, TIFF
- [ ] Klick auf die Drop-Zone navigiert zur Upload-Seite

---

## Epic 3: Dokument-Upload

### US-3.1: Datei-Upload (P1)

**Als** Benutzer
**moechte ich** Dokumente ueber eine Upload-Seite hochladen
**damit** sie automatisch verarbeitet und archiviert werden.

**Akzeptanzkriterien:**
- [ ] Drag-and-Drop oder Klick oeffnet Dateiauswahl
- [ ] Mehrere Dateien gleichzeitig moeglich
- [ ] Akzeptierte Formate: PDF, JPG, JPEG, PNG, TIFF, BMP (max 50 MB)
- [ ] Upload-Fortschritt wird als Prozentzahl und Fortschrittsbalken angezeigt
- [ ] Die Drop-Zone ist waehrend des Uploads deaktiviert

### US-3.2: Upload-Status-Verfolgung (P1)

**Als** Benutzer
**moechte ich** den Verarbeitungsstatus meiner hochgeladenen Dateien sehen
**damit** ich weiss, wann die Analyse abgeschlossen ist.

**Akzeptanzkriterien:**
- [ ] Pro Datei: Dateiname + Status-Badge (Wartet, Wird verarbeitet, Fertig, Fehlgeschlagen, Pruefung noetig)
- [ ] Spinner/Haekchen/X-Symbol je nach Status
- [ ] Bei Status "Fertig": "Anzeigen"-Link zur Detailseite
- [ ] Status wird automatisch aktualisiert (Polling)

---

## Epic 4: Dokumentenliste

### US-4.1: Dokumenten-Tabelle (P1)

**Als** Benutzer
**moechte ich** alle meine Dokumente in einer Tabelle sehen
**damit** ich einen Ueberblick ueber mein Archiv habe.

**Akzeptanzkriterien:**
- [ ] Tabelle zeigt: Dateityp-Icon, Titel (+ Originaldateiname), Typ (Badge), Datum, Betrag, Steuer (Checkbox), Aussteller, Tags
- [ ] Jede Zeile ist klickbar und oeffnet die Detailseite
- [ ] Bei leerer Liste: "Keine Dokumente gefunden."
- [ ] "Hochladen"-Button im Header verlinkt zu `/upload`

### US-4.2: Dokumenten-Filter (P1)

**Als** Benutzer
**moechte ich** Dokumente nach Typ, Datum, Bereich und Steuerrelevanz filtern
**damit** ich schnell bestimmte Dokumente finde.

**Akzeptanzkriterien:**
- [ ] Typ-Dropdown mit allen 15 Dokumenttypen + "Alle Typen"
- [ ] Datumsfilter "Von" und "Bis"
- [ ] Bereichs-Dropdown (nur sichtbar bei >1 Ablagebereich)
- [ ] Steuerrelevant-Checkbox
- [ ] "Zuruecksetzen"-Button setzt alle Filter zurueck
- [ ] Filter werden sofort angewendet

### US-4.3: Dokumenten-Sortierung (P2)

**Als** Benutzer
**moechte ich** Dokumente nach verschiedenen Kriterien sortieren
**damit** ich sie in der gewuenschten Reihenfolge sehe.

**Akzeptanzkriterien:**
- [ ] Sortierbar nach: Titel, Typ, Datum, Betrag
- [ ] Klick auf Spaltenheader wechselt Sortierrichtung
- [ ] Sortierrichtung wird durch Pfeile angezeigt

### US-4.4: Steuerrelevanz direkt aendern (P2)

**Als** Benutzer
**moechte ich** die Steuerrelevanz direkt in der Tabelle per Klick aendern
**damit** ich nicht jedes Dokument einzeln oeffnen muss.

**Akzeptanzkriterien:**
- [ ] Steuer-Spalte zeigt eine klickbare Checkbox pro Dokument
- [ ] Aenderung wird sofort per API gespeichert
- [ ] Visuelle Rueckmeldung bei Erfolg

### US-4.5: Paginierung (P3)

**Als** Benutzer
**moechte ich** durch grosse Dokumentenmengen blaettern
**damit** die Seite nicht zu lang wird.

**Akzeptanzkriterien:**
- [ ] Paginierung wird bei mehr als einer Seite angezeigt
- [ ] Seitenwechsel laedt neue Daten
- [ ] Aktuelle Seite ist visuell hervorgehoben

---

## Epic 5: Dokumentendetails

### US-5.1: Dokumenten-Vorschau (P1)

**Als** Benutzer
**moechte ich** ein Dokument in der Detailansicht als Vorschau sehen
**damit** ich den Inhalt pruefen kann ohne die Datei herunterladen zu muessen.

**Akzeptanzkriterien:**
- [ ] PDF-Dateien werden in einem eingebetteten Viewer angezeigt
- [ ] Bilddateien (JPG, PNG etc.) werden als Bild dargestellt
- [ ] "Herunterladen"-Link laedt die Originaldatei
- [ ] Zurueck-Button fuehrt zur Dokumentenliste

### US-5.2: Metadaten bearbeiten (P1)

**Als** Benutzer
**moechte ich** die KI-erkannten Metadaten korrigieren und ergaenzen
**damit** falsche Eintraege richtig gestellt werden.

**Akzeptanzkriterien:**
- [ ] Formularfelder: Titel, Dokumenttyp (Dropdown), Datum, Betrag, Aussteller, Referenznummer
- [ ] Ablagebereich-Dropdown mit "+" fuer Inline-Anlage eines neuen Bereichs
- [ ] Steuerrelevant-Checkbox mit bedingtem Steuerkategorie-Dropdown
- [ ] "Speichern"-Button sendet Aenderungen an die API
- [ ] Button zeigt "Wird gespeichert..." waehrend des Speicherns

### US-5.3: Tags verwalten (P2)

**Als** Benutzer
**moechte ich** Tags zu Dokumenten hinzufuegen und entfernen
**damit** ich Dokumente individuell verschlagworten kann.

**Akzeptanzkriterien:**
- [ ] Vorhandene Tags werden als Chips mit Entfernen-Button angezeigt
- [ ] Neues Tag wird ueber Texteingabe + "+"-Button oder Enter hinzugefuegt
- [ ] Entfernen eines Tags erfolgt sofort per API-Aufruf
- [ ] Hinzufuegen eines Tags erfolgt sofort per API-Aufruf

### US-5.4: Dokument loeschen (P2)

**Als** Benutzer
**moechte ich** ein Dokument loeschen (Soft-Delete)
**damit** fehlerhafte oder doppelte Eintraege entfernt werden koennen.

**Akzeptanzkriterien:**
- [ ] "Loeschen"-Button im Header oeffnet Bestaetigung-Dialog
- [ ] Nach Bestaetigung wird das Dokument per API geloescht (Soft-Delete)
- [ ] Nach erfolgreicher Loeschung Weiterleitung zur Dokumentenliste

### US-5.5: KI-Analyse und Rueckfragen anzeigen (P3)

**Als** Benutzer
**moechte ich** die KI-Analyse-Ergebnisse und offene Rueckfragen sehen
**damit** ich die Qualitaet der automatischen Erkennung bewerten kann.

**Akzeptanzkriterien:**
- [ ] Konfidenz-Anzeige mit farbigem Fortschrittsbalken (gruen/gelb/rot)
- [ ] Zusammenfassung (falls vorhanden)
- [ ] Garantie-Informationen (falls vorhanden): Produkt, Ablaufdatum, Aktiv/Abgelaufen-Badge
- [ ] Offene Rueckfragen mit Antwortmoeglichkeit

---

## Epic 6: Rueckfrage-System

### US-6.1: Rueckfragen-Ueberblick (P1)

**Als** Benutzer
**moechte ich** eine Uebersicht aller Dokumente mit offenen Rueckfragen sehen
**damit** ich weiss, welche Dokumente meine Aufmerksamkeit benoetigen.

**Akzeptanzkriterien:**
- [ ] Zaehler "X von Y Dokumenten" im Header
- [ ] Wenn alles geprueft: "Alles geprueft!" Nachricht mit Haekchen
- [ ] Automatisches Laden des naechsten Dokuments nach Bestaetigung

### US-6.2: Rueckfragen beantworten (P1)

**Als** Benutzer
**moechte ich** KI-Rueckfragen zu einem Dokument beantworten
**damit** die Metadaten vervollstaendigt werden koennen.

**Akzeptanzkriterien:**
- [ ] Pro Frage: Nummer, Typ-Badge (Klassifikation/Extraktion/Kontext/Bestaetigung), Fragetext, Erklaerung
- [ ] Vorgeschlagene Antworten als klickbare Buttons
- [ ] Freitext-Antwort per Textarea
- [ ] "Beantworten"-Button (auch Ctrl+Enter) sendet die Antwort
- [ ] Fortschrittsbalken zeigt beantwortete/offene Fragen

### US-6.3: Review abschliessen (P2)

**Als** Benutzer
**moechte ich** ein Dokument nach Beantwortung aller Fragen bestaetigen
**damit** es als geprueft markiert wird.

**Akzeptanzkriterien:**
- [ ] "Bestaetigen"-Button erscheint erst, wenn alle Fragen beantwortet sind
- [ ] "Ueberspringen"-Button fuer spaeteres Review
- [ ] "Details"-Link fuehrt zur Dokumenten-Detailseite
- [ ] Nach Bestaetigung wird das naechste Dokument geladen

### US-6.4: Dokument-Vorschau im Review (P3)

**Als** Benutzer
**moechte ich** das Dokument waehrend des Reviews sehen
**damit** ich die KI-Fragen im Kontext des Originals beantworten kann.

**Akzeptanzkriterien:**
- [ ] PDF: eingebetteter Viewer
- [ ] Bilder: zoombare Ansicht (Mausrad, +/- Buttons, 1:1-Reset)
- [ ] Drag-to-Pan bei gezoomten Bildern
- [ ] Download-Button und "In neuem Tab oeffnen"-Button

---

## Epic 7: Volltextsuche

### US-7.1: Einfache Suche (P1)

**Als** Benutzer
**moechte ich** meine Dokumente per Freitext durchsuchen
**damit** ich schnell relevante Dokumente finde.

**Akzeptanzkriterien:**
- [ ] Suchfeld mit Placeholder "Volltextsuche..."
- [ ] "Suchen"-Button oder Enter startet die Suche
- [ ] Ergebnisse zeigen: Typ-Icon, Titel, DocType-Badge, Aussteller, Datum, Betrag, Highlight-Snippet, Tags
- [ ] Ergebnisse sind klickbar und fuehren zur Detailseite
- [ ] Anzahl der Treffer wird angezeigt

### US-7.2: Erweiterte Suche (P1)

**Als** Benutzer
**moechte ich** erweiterte Suchfilter nutzen
**damit** ich die Ergebnisse praeziser eingrenzen kann.

**Akzeptanzkriterien:**
- [ ] "Erweitert"/"Einfach"-Toggle blendet Zusatzfilter ein/aus
- [ ] Filter: Datum von/bis, Min/Max Betrag, Nur steuerrelevante
- [ ] "Zuruecksetzen"-Button setzt alle erweiterten Filter zurueck
- [ ] Filter werden mit der Textsuche kombiniert

### US-7.3: Facetten-Filter (P2)

**Als** Benutzer
**moechte ich** Suchergebnisse per Facetten weiter eingrenzen
**damit** ich Dokumente nach Typ oder Zeitraum filtern kann.

**Akzeptanzkriterien:**
- [ ] Dokumenttyp-Facetten als Checkboxen mit Trefferanzahl
- [ ] Jahres-Facetten mit Trefferanzahl (nur Anzeige)
- [ ] Ablagebereich-Dropdown (bei >1 Bereich)
- [ ] Facetten-Auswahl aktualisiert die Ergebnisse sofort

### US-7.4: Gespeicherte Suchen (P2)

**Als** Benutzer
**moechte ich** Suchanfragen speichern und wieder aufrufen
**damit** ich haeufige Suchen nicht wiederholt eingeben muss.

**Akzeptanzkriterien:**
- [ ] "+ Speichern"-Link oeffnet Inline-Dialog mit Namenseingabe
- [ ] Gespeicherte Suchen erscheinen in der Seitenleiste
- [ ] Klick auf eine gespeicherte Suche fuehrt sie erneut aus
- [ ] Loeschen einer gespeicherten Suche per X-Button

### US-7.5: Sortierung der Suchergebnisse (P3)

**Als** Benutzer
**moechte ich** Suchergebnisse nach verschiedenen Kriterien sortieren
**damit** ich die relevantesten Ergebnisse zuerst sehe.

**Akzeptanzkriterien:**
- [ ] Sortier-Dropdown: Relevanz, Datum, Betrag, Titel, Erstellt
- [ ] Aenderung der Sortierung aktualisiert die Ergebnisse sofort
- [ ] Standard-Sortierung ist "Relevanz"

---

## Epic 8: Steuerpaket-Export

### US-8.1: Steuer-Ueberblick (P1)

**Als** Benutzer
**moechte ich** eine Uebersicht aller steuerrelevanten Belege pro Jahr sehen
**damit** ich meine Steuerunterlagen organisieren kann.

**Akzeptanzkriterien:**
- [ ] Jahres-Auswahl per Dropdown
- [ ] Statistik-Karten: Belege (Anzahl), Gesamtbetrag (EUR), Kategorien (Anzahl)
- [ ] Kategorien-Karten mit: Name, Beleganzahl, Betrag, proportionalem Fortschrittsbalken
- [ ] Bei keinen Belegen: "Keine steuerrelevanten Belege fuer [Jahr]."
- [ ] Bereichs-Filter (bei >1 Ablagebereich)

### US-8.2: Steuerpaket exportieren (P2)

**Als** Benutzer
**moechte ich** ein Steuerpaket als ZIP-Datei exportieren
**damit** ich es meinem Steuerberater uebergeben kann.

**Akzeptanzkriterien:**
- [ ] "ZIP exportieren"-Button startet den Export
- [ ] Button ist deaktiviert wenn keine Belege vorhanden oder Export laeuft
- [ ] Button zeigt "Exportiere..." waehrend des Exports
- [ ] ZIP-Datei wird automatisch heruntergeladen als `Steuerpaket_{Jahr}.zip`

### US-8.3: Export-Warnungen (P3)

**Als** Benutzer
**moechte ich** vor dem Export auf moegliche Probleme hingewiesen werden
**damit** ich fehlende oder fehlerhafte Belege korrigieren kann.

**Akzeptanzkriterien:**
- [ ] Warnungs-Panel zeigt "X Hinweise vor dem Export"
- [ ] Panel ist auf-/zuklappbar
- [ ] Einzelne Warnungen werden als Liste angezeigt

---

## Epic 9: Garantie-Tracker

### US-9.1: Garantie-Ueberblick (P1)

**Als** Benutzer
**moechte ich** alle Garantien mit ihrem Status sehen
**damit** ich ablaufende Garantien rechtzeitig bemerke.

**Akzeptanzkriterien:**
- [ ] Statistik-Karten: Gesamt, Aktiv, Bald ablaufend, Abgelaufen
- [ ] Pro Garantie: Produktname, Status-Badge, Haendler, Kauf-/Ablaufdatum, Fortschrittsbalken
- [ ] Farbcodierung: Gruen (aktiv), Gelb (bald ablaufend), Rot (abgelaufen)
- [ ] Klick auf Garantie navigiert zur verknuepften Dokumenten-Detailseite
- [ ] Bei keinen Garantien: "Keine Garantien vorhanden."

### US-9.2: Garantie-Filter (P2)

**Als** Benutzer
**moechte ich** Garantien nach Status filtern
**damit** ich gezielt aktive oder ablaufende Garantien sehe.

**Akzeptanzkriterien:**
- [ ] Status-Filter-Dropdown: Alle, Aktiv, Bald ablaufend, Abgelaufen
- [ ] Filter wird sofort angewendet
- [ ] Statistik-Karten bleiben immer sichtbar

---

## Epic 10: KI-Assistent

### US-10.1: Fragen stellen (P1)

**Als** Benutzer
**moechte ich** Fragen zu meinen Dokumenten in natuerlicher Sprache stellen
**damit** ich schnell Informationen finde ohne manuell suchen zu muessen.

**Akzeptanzkriterien:**
- [ ] Texteingabe mit Placeholder "Stelle eine Frage zu deinen Dokumenten..."
- [ ] Senden per Enter oder Sende-Button
- [ ] Benutzer-Nachrichten rechtsbuendig in farbiger Sprechblase
- [ ] KI-Antworten linksbuendig in grauer Sprechblase
- [ ] Tipp-Indikator (3 huepfende Punkte) waehrend Verarbeitung
- [ ] Maximale Eingabelaenge: 2000 Zeichen

### US-10.2: Quellen-Referenzen (P2)

**Als** Benutzer
**moechte ich** zu KI-Antworten die Quell-Dokumente sehen
**damit** ich die Aussagen verifizieren kann.

**Akzeptanzkriterien:**
- [ ] Quell-Links in der KI-Antwort fuehren zur Dokumenten-Detailseite
- [ ] Beispielfragen werden bei leerem Chat als klickbare Buttons angezeigt
- [ ] Klick auf Beispielfrage sendet sie direkt

### US-10.3: Chat-Verlauf (P3)

**Als** Benutzer
**moechte ich** den Chat-Verlauf einsehen und loeschen koennen
**damit** ich fruehere Fragen nachschlagen oder einen Neustart machen kann.

**Akzeptanzkriterien:**
- [ ] Verlauf wird chronologisch angezeigt
- [ ] Ablagebereich-Filter (bei >1 Bereich)
- [ ] "Verlauf loeschen"-Button (nur sichtbar wenn Nachrichten existieren)
- [ ] Nach Loeschen: leerer Chat mit Beispielfragen

---

## Epic 11: Kamera-Scan

### US-11.1: Dokument per Kamera scannen (P2)

**Als** Benutzer
**moechte ich** Dokumente mit der Smartphone-Kamera abfotografieren und direkt hochladen
**damit** ich Belege ohne Scanner digitalisieren kann.

**Akzeptanzkriterien:**
- [ ] "Kamera starten"-Button aktiviert die Kamera
- [ ] Live-Vorschau mit Ausrichtungshilfe (gestrichelter Rahmen)
- [ ] Grosser runder Aufnahme-Button
- [ ] "Kamera wechseln"-Button fuer Front-/Rueckkamera
- [ ] Aufgenommene Bilder als Thumbnail-Galerie mit Nummerierung
- [ ] Entfernen einzelner Aufnahmen per X-Button
- [ ] "Hochladen"-Button mit Fortschrittsanzeige
- [ ] Fallback: "Datei waehlen"-Button bei Kamera-Problemen

---

## Epic 12: Einstellungen

### US-12.1: System-Status (P1)

**Als** Benutzer
**moechte ich** den Systemstatus auf einen Blick sehen
**damit** ich weiss, ob alle Komponenten ordnungsgemaess funktionieren.

**Akzeptanzkriterien:**
- [ ] Status-Anzeige: "System betriebsbereit" / "System eingeschraenkt" mit farbigem Punkt
- [ ] Versions-Badge (v{version})
- [ ] Installations-Pfad mit "Ordner oeffnen"-Button
- [ ] Komponentenstatus: Backend, SQLite, Ollama, ChromaDB mit OK/Fehler
- [ ] ChromaDB-Fehler: Hilfetext mit Docker-Befehlen + "Kopieren"-Button
- [ ] Speicher-Ueberblick: Datenbank, Archiv, Uploads, Festplatte frei + Fortschrittsbalken

### US-12.2: Ablagebereiche verwalten (P1)

**Als** Benutzer
**moechte ich** Ablagebereiche anlegen, bearbeiten und loeschen
**damit** ich Dokumente nach meinen Beduerfnissen organisieren kann.

**Akzeptanzkriterien:**
- [ ] Liste aller Bereiche mit Farbpunkt, Name, Standard-Badge, Beschreibung, Schluesselwoerter
- [ ] "+ Neuer Bereich"-Button oeffnet Inline-Formular
- [ ] Formular: Name, Beschreibung, Schluesselwoerter (kommagetrennt), Farbwahl, Standard-Checkbox
- [ ] "Bearbeiten"-Link oeffnet Formular mit vorausgefuellten Daten
- [ ] "Loeschen"-Link (nur fuer Nicht-Standard-Bereiche) mit Bestaetigung-Dialog
- [ ] Aenderungen werden per API gespeichert

### US-12.3: Ordner-Konfiguration (P2)

**Als** Benutzer
**moechte ich** Eingangs- und Zielordner konfigurieren
**damit** ich bestimmen kann, woher Dokumente importiert und wohin sie exportiert werden.

**Akzeptanzkriterien:**
- [ ] Eingabefelder fuer Eingangsordner und Zielordner
- [ ] "Speichern"-Button speichert die Konfiguration
- [ ] "Auf Standard zuruecksetzen"-Button setzt auf Standardwerte zurueck
- [ ] Neustart-Banner wenn Ordner geaendert wurden
- [ ] Erweitert-Bereich fuer Container-Pfade

### US-12.4: Wartung und Backup (P2)

**Als** Benutzer
**moechte ich** Wartungsaufgaben und Backups ausfuehren
**damit** mein System stabil und meine Daten gesichert bleiben.

**Akzeptanzkriterien:**
- [ ] Wartungs-Buttons: "Datenbank optimieren", "Suchindex neu aufbauen", "Vektor-Index aufbauen"
- [ ] Backup-Buttons: "Backup (DB)" und "Vollbackup"
- [ ] Backup-Liste: Dateiname, Groesse, Datum, Download-Link
- [ ] Feedback bei Aktionen (Erfolgsmeldung oder Fehler)

### US-12.5: E-Mail-Konten verwalten (P3)

**Als** Benutzer
**moechte ich** E-Mail-Konten fuer den automatischen Import konfigurieren
**damit** rechnungsrelevante E-Mails automatisch verarbeitet werden.

**Akzeptanzkriterien:**
- [ ] E-Mail-Konten-Liste (EmailAccountList-Komponente)
- [ ] Formular zum Anlegen/Bearbeiten von E-Mail-Konten
- [ ] IMAP-Verbindungstest
- [ ] Manueller E-Mail-Abruf
- [ ] E-Mail-Verlauf einsehen

---

## Epic 13: E-Mail-Integration

### US-13.1: E-Mail-Import-Status (P2)

**Als** Benutzer
**moechte ich** im Dashboard sehen, wie viele E-Mails importiert wurden
**damit** ich den Ueberblick ueber den automatischen Import behalte.

**Akzeptanzkriterien:**
- [ ] E-Mail-Statistik-Karte im Dashboard (nur bei konfigurierten Konten)
- [ ] Anzeige: Anzahl importierter E-Mails
- [ ] Karte in Indigo-Farbe

---

## Epic 14: Benachrichtigungen

### US-14.1: Benachrichtigungs-Glocke (P2)

**Als** Benutzer
**moechte ich** ueber wichtige Ereignisse per Benachrichtigungsglocke informiert werden
**damit** ich ablaufende Garantien und abgeschlossene Verarbeitungen nicht verpasse.

**Akzeptanzkriterien:**
- [ ] Glocken-Icon im Header mit Badge-Zaehler fuer ungelesene Benachrichtigungen
- [ ] Dropdown mit Benachrichtigungsliste bei Klick
- [ ] Benachrichtigungstypen: Garantie-Ablauf, Review noetig, Verarbeitung fertig, System
- [ ] "Alle als gelesen markieren"-Funktion
- [ ] Einzelne Benachrichtigungen als gelesen markierbar

---

## Epic 15: Responsive Design & Navigation

### US-15.1: Desktop-Navigation (P1)

**Als** Benutzer
**moechte ich** auf dem Desktop eine Sidebar-Navigation nutzen
**damit** ich schnell zwischen den Bereichen wechseln kann.

**Akzeptanzkriterien:**
- [ ] Sidebar mit 10 Navigationspunkten: Dashboard, Dokumente, Upload, Scan, Zu pruefen, Suche, KI-Assistent, Steuer, Garantien, System
- [ ] Aktiver Link visuell hervorgehoben
- [ ] Versions-Nummer im Footer
- [ ] Sidebar sichtbar ab 1024px Viewport-Breite

### US-15.2: Mobile Navigation (P2)

**Als** Benutzer
**moechte ich** auf dem Smartphone eine Bottom-Navigation nutzen
**damit** ich die App bequem mit dem Daumen bedienen kann.

**Akzeptanzkriterien:**
- [ ] Bottom-Navigation mit 4 Punkten: Scan (hervorgehoben), Dokumente, Suche, Assistent
- [ ] Scan-Button als grosser runder Button hervorgehoben
- [ ] Aktiver Punkt in Primaerfarbe
- [ ] Nur sichtbar unter 1024px Viewport-Breite

### US-15.3: Responsive Layout (P3)

**Als** Benutzer
**moechte ich** die Anwendung auf verschiedenen Geraeten nutzen
**damit** sie auf Desktop, Tablet und Smartphone funktioniert.

**Akzeptanzkriterien:**
- [ ] Desktop (>1024px): Sidebar + volle Inhaltsbreite
- [ ] Tablet (768-1024px): Kompakte Darstellung ohne Sidebar
- [ ] Mobile (<768px): Bottom-Navigation, einspaltige Darstellung
- [ ] Alle Views passen sich dem Viewport an
