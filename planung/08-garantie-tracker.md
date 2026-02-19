# Prompt 08 - Garantie- und Gewaehrleistungs-Tracker

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Die KI-Analyse erkennt Kaufbelege und extrahiert automatisch Garantie-Informationen (Produktname, Kaufdatum, Haendler). Das Datenmodell `WarrantyInfo` (Prompt 04) speichert diese Informationen. Jetzt soll ein benutzerfreundliches Garantie-Dashboard entstehen, das den Ueberblick ueber alle Garantien gibt und rechtzeitig warnt.

## Aufgabe

Implementiere ein Garantie-Tracking-System mit Dashboard, Erinnerungsfunktion und Schadensfall-Workflow.

## Anforderungen

### 1. Garantie-Dashboard (`/garantien`)

#### Statusuebersicht (oben):
- **Aktive Garantien:** Anzahl (gruen)
- **Laeuft bald ab (< 90 Tage):** Anzahl (orange)
- **Abgelaufen:** Anzahl (grau)
- **Gesamt:** Anzahl

#### Garantie-Liste:
Tabellarische Darstellung aller Garantien:

| Produkt | Kategorie | Kaufdatum | Haendler | Garantie bis | Status | Aktion |
|---|---|---|---|---|---|---|
| Samsung TV 55" | Elektronik | 15.03.2023 | MediaMarkt | 15.03.2025 | Aktiv | Ansehen |
| Bosch Waschmaschine | Haushalt | 01.06.2022 | Saturn | 01.06.2024 | Abgelaufen | Ansehen |

- **Status-Badges:** Farbig (Gruen=Aktiv, Orange=Bald ablaufend, Rot=< 30 Tage, Grau=Abgelaufen)
- **Fortschrittsbalken:** Visuell zeigen wie viel Garantiezeit bereits vergangen ist
- **Sortierung:** Nach Ablaufdatum (bald ablaufende zuerst), Kaufdatum, Produktname
- **Filter:** Status (Aktiv/Ablaufend/Abgelaufen), Kategorie, Zeitraum

#### Zeitleisten-Ansicht (Toggle):
Alternative Darstellung als Timeline:
- Horizontale Zeitleiste mit Garantien als Balken
- Heute-Markierung
- Optisch schnell erfassbar welche Garantien wann ablaufen

### 2. Garantie-Detailansicht (`/garantien/:id`)

Zeigt alle Details zu einer Garantie:

**Produktinformationen:**
- Produktname und -kategorie
- Kaufdatum und Kaufpreis
- Haendler/Verkaeufer
- Referenz-/Bestellnummer

**Garantiestatus:**
- Garantietyp (Gesetzlich / Herstellergarantie / Erweitert)
- Garantiedauer in Monaten
- Garantie-Ablaufdatum
- Verbleibende Tage (farbig hervorgehoben)
- Fortschrittsbalken

**Verknuepftes Dokument:**
- Vorschau des Kaufbelegs
- Link zur Dokumenten-Detailansicht
- Download-Button fuer den Beleg

**Benutzer-Aktionen:**
- Garantiedauer manuell anpassen (z.B. bei Herstellergarantie > 2 Jahre)
- Notizen hinzufuegen
- Produkt-Kategorie aendern
- Garantie als "Schaden gemeldet" markieren

### 3. Produktkategorien

Vordefinierte Kategorien fuer bessere Organisation:

```python
PRODUCT_CATEGORIES = {
    "elektronik": "Elektronik & Computer",
    "haushalt": "Haushaltsgeraete",
    "moebel": "Moebel & Einrichtung",
    "werkzeug": "Werkzeug & Garten",
    "kleidung": "Kleidung & Schuhe",
    "sport": "Sport & Freizeit",
    "kfz": "Auto & Zubehoer",
    "kinder": "Kinder & Spielzeug",
    "sonstiges": "Sonstiges"
}
```

Die KI aus Prompt 03 soll versuchen, Produkte automatisch in diese Kategorien einzuordnen.

### 4. Erinnerungssystem

Automatische Warnungen bei ablaufenden Garantien:

#### Backend-Service (`services/warranty_reminder_service.py`):

- Taeglicher Check (als Background-Task oder Cron-Job im Container)
- Prueft alle aktiven Garantien gegen konfigurierbares Zeitfenster:
  - 90 Tage vor Ablauf: erste Warnung
  - 30 Tage vor Ablauf: dringende Warnung
  - Am Ablauftag: letzte Warnung
- Erstellt Benachrichtigungs-Eintraege in der Datenbank

#### Datenmodell-Erweiterung:

```python
class Notification(Base):
    __tablename__ = "notifications"

    id: UUID
    type: str                         # WARRANTY_EXPIRING, WARRANTY_EXPIRED, etc.
    title: str
    message: str
    document_id: UUID | None          # FK -> documents
    warranty_id: UUID | None          # FK -> warranty_info
    is_read: bool
    created_at: datetime
    read_at: datetime | None
```

#### Frontend-Benachrichtigungen:

- Glocken-Icon im Header mit ungelesener Anzahl
- Dropdown mit Benachrichtigungsliste
- Klick auf Benachrichtigung -> Garantie-Detailansicht
- Benachrichtigungen als gelesen markieren

### 5. Schadensfall-Unterstuetzung

Wenn ein Garantiefall eintritt, soll der Benutzer schnell alle noetigten Informationen finden:

**Button "Garantiefall melden" in der Garantie-Detailansicht:**

Zeigt eine Zusammenstellung:
- Kaufbeleg als PDF (Download-Button)
- Alle relevanten Informationen kompakt:
  - Produktbezeichnung
  - Kaufdatum
  - Haendler mit Kontaktinformationen (falls im Beleg erkennbar)
  - Rechnungsnummer
  - Bezahlter Betrag
  - Verbleibende Garantie
- Hinweis auf rechtliche Grundlage:
  - Innerhalb 12 Monate: Beweislastumkehr beim Verkaeufer
  - 12-24 Monate: Beweislast beim Kaeufer
  - Hinweis: "Dies ist keine Rechtsberatung"
- "Alles als PDF exportieren"-Button (Zusammenfassung + Kaufbeleg in einem PDF)

### 6. API-Endpoints

```
GET    /api/warranties                 # Alle Garantien (paginiert, filterbar)
GET    /api/warranties/{id}            # Einzelne Garantie
PATCH  /api/warranties/{id}            # Garantie-Daten aktualisieren
GET    /api/warranties/expiring        # Bald ablaufende Garantien
POST   /api/warranties/{id}/claim      # Garantiefall melden / Zusammenfassung generieren
GET    /api/warranties/{id}/claim-pdf  # Garantiefall als PDF exportieren
GET    /api/notifications              # Benachrichtigungen auflisten
PATCH  /api/notifications/{id}/read    # Als gelesen markieren
POST   /api/notifications/read-all     # Alle als gelesen markieren
GET    /api/warranties/stats           # Statistiken fuer Dashboard
```

### 7. Manuelle Garantie-Erweiterung

Benutzer kann zusaetzliche Garantie-Informationen erfassen die nicht automatisch erkannt wurden:

- Herstellergarantie separat zur gesetzlichen Gewaehrleistung hinzufuegen
- Erweiterte Garantie (z.B. Elektromarkt-Garantieverlaengerung) hinzufuegen
- Mehrere Garantien pro Produkt moeglich (gestaffelt)

## Akzeptanzkriterien

- [ ] Dashboard zeigt korrekte Anzahl aktiver, ablaufender und abgelaufener Garantien
- [ ] Garantieliste ist sortier- und filterbar
- [ ] Fortschrittsbalken zeigt verbleibende Garantiezeit visuell
- [ ] Detailansicht zeigt alle Produktinformationen und verknuepften Beleg
- [ ] Benachrichtigungen werden 90/30/0 Tage vor Ablauf erstellt
- [ ] Glocken-Icon zeigt Anzahl ungelesener Benachrichtigungen
- [ ] Garantiefall-Zusammenfassung zeigt alle relevanten Informationen
- [ ] Garantiefall-PDF kann exportiert werden
- [ ] Benutzer kann Garantiedauer manuell aendern
- [ ] Gesetzliche Gewaehrleistung (2 Jahre) wird als Default gesetzt

## Nicht-Ziele dieses Prompts

- Keine automatische Kontaktaufnahme mit Haendlern
- Keine Integration mit Haendler-Portalen
- Keine Produktdatenbank-Anbindung (Produkte kommen nur aus den Belegen)
