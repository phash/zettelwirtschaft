# Prompt 11 - Interaktives Rueckfrage-System bei unklaren Dokumenten

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Die KI analysiert eingescannte Dokumente automatisch (Prompt 03). Bei manchen Dokumenten ist die KI unsicher - z.B. bei schlecht lesbaren Scans, ungewoehnlichen Dokumenttypen oder fehlenden Informationen. Diese Dokumente werden als `NEEDS_REVIEW` markiert und haben automatisch generierte Fragen (`ReviewQuestion`-Modell, Prompt 04). Die Pruef-Seite (Prompt 05) zeigt diese Dokumente bereits als einfache Liste.

Jetzt soll ein intelligentes, interaktives Rueckfrage-System entstehen, das den Benutzer gezielt durch die Klaerung fuehrt.

## Aufgabe

Erweitere das bestehende Rueckfrage-System zu einem interaktiven Dialog zwischen KI und Benutzer. Die KI soll gezielte, verstaendliche Fragen stellen, und die Antworten sollen die Dokumenten-Metadaten automatisch aktualisieren.

## Anforderungen

### 1. Intelligente Fragengenerierung

Verbessere die Fragengenerierung in der KI-Analyse-Pipeline (Prompt 03):

#### Frage-Kategorien:

**Typ A - Klassifikationsfragen** (Was ist das fuer ein Dokument?)
```
Die KI konnte den Dokumenttyp nicht eindeutig erkennen.
Beispiel: "Ich bin mir nicht sicher, um welche Art von Dokument es sich handelt.
           Es koennte eine Rechnung oder eine Auftragsbestaetigung sein.
           Welcher Dokumenttyp trifft zu?"
Antwortformat: Auswahl aus vordefinierten Typen
```

**Typ B - Extraktionsfragen** (Welchen Wert hat ein bestimmtes Feld?)
```
Ein konkretes Feld konnte nicht zuverlaessig erkannt werden.
Beispiel: "Ich konnte den Rechnungsbetrag nicht eindeutig lesen.
           Ich habe '149,99 EUR' oder '119,99 EUR' erkannt.
           Welcher Betrag ist korrekt?"
Antwortformat: Freitext oder Auswahl aus erkannten Alternativen
```

**Typ C - Kontextfragen** (Zusaetzliche Info die nicht im Dokument steht)
```
Information die nur der Benutzer kennt.
Beispiel: "Fuer welchen Zweck wurde dieser Artikel gekauft?
           Das hilft mir bei der steuerlichen Einordnung."
Antwortformat: Freitext oder Auswahl
```

**Typ D - Bestaetigungsfragen** (Stimmt meine Erkennung?)
```
Die KI ist sich halbwegs sicher, moechte aber Bestaetigung.
Beispiel: "Ich habe erkannt: Rechnung von 'Amazon' ueber '49,99 EUR'
           vom '15.03.2024'. Ist das korrekt?"
Antwortformat: Ja/Nein + Korrekturmoeglichkeit
```

#### LLM-Prompt fuer Fragengenerierung:

Erstelle einen spezialisierten Prompt (`prompts/generate_questions.txt`):

```
Du bist ein freundlicher Assistent der einem Privatnutzer hilft,
seine Dokumente zu organisieren.

Analysiere den folgenden Text eines gescannten Dokuments und die
bisherige automatische Erkennung. Generiere gezielte Fragen fuer
Informationen die du nicht sicher erkennen konntest.

Regeln fuer deine Fragen:
- Formuliere einfach und verstaendlich (kein Fachjargon)
- Stelle maximal 5 Fragen pro Dokument
- Priorisiere: Wichtige Felder zuerst (Typ, Datum, Betrag)
- Biete Antwortoptionen an wo moeglich
- Erklaere kurz warum du fragst
- Sei hoeflich aber nicht umstaendlich

Gib deine Fragen als JSON zurueck...
```

### 2. Datenmodell-Erweiterung

Erweitere das `ReviewQuestion`-Modell:

```python
class ReviewQuestion(Base):
    __tablename__ = "review_questions"

    id: UUID
    document_id: UUID                 # FK -> documents
    question_type: str                # CLASSIFICATION, EXTRACTION, CONTEXT, CONFIRMATION
    question: str                     # Menschenlesbare Frage
    explanation: str | None           # Warum wird gefragt (Kontext)
    field_affected: str               # Welches Document-Feld wird aktualisiert
    suggested_answers: str | None     # JSON: Vorgeschlagene Antworten / Optionen
    answer: str | None                # Benutzerantwort
    is_answered: bool
    priority: int                     # 1 (hoch) bis 5 (niedrig)
    created_at: datetime
    answered_at: datetime | None
```

### 3. Interaktive Pruef-Seite (`/pruefen`) - Ueberarbeitung

Gestalte die bestehende Pruefseite als gefuehrten Dialog um:

#### Layout (Zweispaltig):

**Linke Spalte (55%):**
- Dokument-Vorschau (PDF/Bild)
- Zoom und Navigation
- Markierungen: Relevante Bereiche im Dokument hervorheben wo die KI unsicher war (falls Positionsdaten aus OCR verfuegbar)

**Rechte Spalte (45%):**
- KI-Zusammenfassung oben: "Das habe ich erkannt:" (alle bereits sicheren Felder)
- Darunter: Fragen als Karten, eine nach der anderen (Wizard-Stil)

#### Frage-Karten:

Jede Frage wird als interaktive Karte dargestellt:

```
+--------------------------------------------------+
| Frage 1 von 3                           [Typ-Badge] |
|                                                      |
| "Ich konnte den Betrag nicht sicher lesen.           |
|  Welcher der folgenden Betraege ist korrekt?"        |
|                                                      |
| Erkannt im Dokument: [Markierung im Dokument]       |
|                                                      |
| ( ) 149,99 EUR    <- Option A                        |
| ( ) 119,99 EUR    <- Option B                        |
| ( ) Anderer Betrag: [____________]                   |
|                                                      |
| [Ueberspringen]                    [Bestaetigen ->]  |
+--------------------------------------------------+
```

#### Antwort-Typen je nach Frage:

- **Auswahl:** Radiobuttons mit vorgeschlagenen Optionen + "Anderer Wert"-Freifeld
- **Ja/Nein:** Zwei grosse Buttons, bei "Nein" erscheint Korrekturfeld
- **Freitext:** Eingabefeld mit ggf. Autovervollstaendigung (z.B. Aussteller-Namen)
- **Datum:** Datepicker
- **Betrag:** Numerisches Feld mit Waehrungsauswahl

#### Fortschritt:
- Fortschrittsbalken oben: "3 von 5 Fragen beantwortet"
- Nach der letzten Frage: Zusammenfassung aller Antworten + "Alles korrekt?"-Bestaetigung

### 4. Automatische Metadaten-Aktualisierung

Nach Beantwortung einer Frage:

- Das betroffene Feld im `Document`-Modell wird automatisch aktualisiert
- `ReviewQuestion.is_answered` wird auf `True` gesetzt
- `Document.review_status` wird auf `REVIEWED` gesetzt wenn alle Fragen beantwortet sind
- `AuditLog`-Eintrag: Wer hat was geaendert (Benutzer, nicht KI)

### 5. Lerneffekt (Optional, Nice-to-Have)

Die KI soll aus den Korrekturen lernen:

- Wenn der Benutzer regelmaessig einen bestimmten Aussteller korrigiert -> Korrektur-Mapping speichern
- Beispiel: KI erkennt "Amaz0n" -> Benutzer korrigiert zu "Amazon" -> Bei zukuenftigen Dokumenten automatisch korrigieren

Einfache Implementierung:
```python
class CorrectionMapping(Base):
    __tablename__ = "correction_mappings"

    id: int
    field: str                        # z.B. "issuer"
    original_value: str               # Was die KI erkannt hat
    corrected_value: str              # Was der Benutzer eingegeben hat
    occurrence_count: int             # Wie oft korrigiert
    auto_apply: bool                  # Automatisch anwenden (nach 3x gleiche Korrektur)
    created_at: datetime
```

### 6. API-Endpoints

```
GET    /api/review/pending             # Dokumente mit offenen Fragen
GET    /api/review/documents/{id}      # Fragen zu einem Dokument
POST   /api/review/questions/{id}/answer  # Frage beantworten
POST   /api/review/documents/{id}/skip    # Dokument ueberspringen
POST   /api/review/documents/{id}/approve # Alle Antworten bestaetigen
GET    /api/review/stats               # Statistiken (beantwortet/offen)
```

### 7. Benachrichtigung bei neuen Rueckfragen

- Zaehler auf Dashboard aktualisieren wenn neue Dokumente `NEEDS_REVIEW` werden
- In der Navigation: Badge mit Anzahl offener Fragen
- Optional: Benachrichtigung im Notification-System (Prompt 08)

## Akzeptanzkriterien

- [ ] KI generiert verstaendliche, kontextbezogene Fragen auf Deutsch
- [ ] Maximal 5 Fragen pro Dokument
- [ ] Fragen haben den korrekten Typ (Klassifikation/Extraktion/Kontext/Bestaetigung)
- [ ] Pruefseite zeigt Dokument und Fragen nebeneinander
- [ ] Antwort-Widgets passen zum Fragetyp (Auswahl, Freitext, Datum, Betrag)
- [ ] Beantwortung einer Frage aktualisiert automatisch das zugehoerige Dokumentenfeld
- [ ] Nach Beantwortung aller Fragen wechselt der Status zu REVIEWED
- [ ] Ueberspringen einer Frage ist moeglich (Frage bleibt offen)
- [ ] Fortschrittsanzeige zeigt korrekt an wie viele Fragen beantwortet sind
- [ ] AuditLog protokolliert alle manuellen Korrekturen
- [ ] Navigation zeigt Badge mit Anzahl offener Rueckfragen

## Nicht-Ziele dieses Prompts

- Kein Training/Finetuning des LLM auf Basis der Korrekturen
- Kein Chat-Interface (die Interaktion ist strukturiert, kein freier Chat)
- Keine automatische Neubewertung alter Dokumente
