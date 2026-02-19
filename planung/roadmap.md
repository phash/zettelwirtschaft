# Zettelwirtschaft - Produkt-Roadmap

## Vision

Ein einfach zu bedienendes, lokal betriebenes Dokumentenmanagementsystem fuer Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI analysiert, kategorisiert und durchsuchbar archiviert. Kein Cloud-Zwang, keine Abos - laeuft im eigenen WLAN.

---

## Technologie-Stack (Empfehlung)

| Komponente | Technologie | Begruendung |
|---|---|---|
| Backend | Python 3.12+ / FastAPI | Starkes AI/ML-Oekosystem, schnelle API |
| Datenbank | SQLite (Start), PostgreSQL (optional) | Einfach, kein Server noetig, portabel |
| OCR | Tesseract OCR + pdf2image | Lokal, kostenlos, gute DE-Unterstuetzung |
| KI-Analyse | Ollama + lokales LLM (Llama 3 / Mistral) | On-Premise, keine Cloud, datenschutzkonform |
| Frontend | Vue.js 3 (SPA) | Leichtgewichtig, reaktiv, einsteigerfreundlich |
| Dateispeicher | Lokales Dateisystem / SMB-Netzlaufwerk | Einfach, keine Abhaengigkeit |
| Deployment | Docker Compose | Ein-Kommando-Installation |
| Smartphone | PWA (Progressive Web App) | Kein App-Store noetig, plattformuebergreifend |

---

## Phasen-Uebersicht

### Phase 1: Fundament (Prompts 01-03)
> Ziel: Lauffaehiges Backend mit Dokumenten-Import und KI-Erkennung

- [x] Prompt 01 - Projekt-Setup und Grundarchitektur
- [x] Prompt 02 - Dokumenten-Import-Pipeline (Scanner, Upload, Watch-Ordner)
- [x] Prompt 03 - KI-Dokumentenanalyse (OCR + LLM-Klassifikation)

**Meilenstein:** Dokumente koennen importiert und automatisch analysiert werden.

### Phase 2: Kern-Features (Prompts 04-06)
> Ziel: Vollfunktionales Archiv mit Weboberflaeche

- [x] Prompt 04 - Datenmodell und Archiv-Datenbank
- [x] Prompt 05 - Web-Oberflaeche (Dashboard, Dokumentenansicht, Suche)
- [x] Prompt 06 - Such- und Filtersystem mit Volltextsuche

**Meilenstein:** Benutzer kann Dokumente suchen, ansehen und verwalten.

### Phase 3: Mehrwert-Features (Prompts 07-08)
> Ziel: Steuerexport und Garantie-Tracking

- [x] Prompt 07 - Steuerpaket-Export fuer den Steuerberater
- [x] Prompt 08 - Garantie- und Gewaehrleistungs-Tracker

**Meilenstein:** Steuerunterlagen als ZIP exportierbar, Garantiefristen werden ueberwacht.

### Phase 4: Mobile & Deployment (Prompts 09-10)
> Ziel: Smartphone-Zugang und einfache Installation

- [x] Prompt 09 - Smartphone-Integration (PWA mit Kamera-Scan)
- [x] Prompt 10 - Installation, Deployment und Wartung (Docker)

**Meilenstein:** Produkt ist produktionsreif und einfach installierbar.

### Phase 5: Verfeinerung (Prompt 11)
> Ziel: Rueckfragen-System fuer unklare Dokumente

- [x] Prompt 11 - Interaktives Rueckfrage-System bei unklaren Dokumenten

**Meilenstein:** System stellt gezielt Fragen zu Dokumenten, die es nicht vollstaendig erkennen kann.

---

## Abhaengigkeiten zwischen Prompts

```
01 Projekt-Setup
 |
 +---> 02 Import-Pipeline
 |      |
 |      +---> 03 KI-Analyse
 |             |
 +---> 04 Datenmodell
        |
        +---> 05 Web-Oberflaeche
        |      |
        |      +---> 06 Such-/Filtersystem
        |      |
        |      +---> 07 Steuerexport
        |      |
        |      +---> 08 Garantie-Tracker
        |      |
        |      +---> 11 Rueckfrage-System
        |
        +---> 09 Smartphone (PWA)
        |
        +---> 10 Deployment (Docker)
```

---

## Qualitaetskriterien (alle Phasen)

- **Einfachheit:** Ein technisch nicht versierter Nutzer muss das System installieren und bedienen koennen.
- **Datenschutz:** Alle Daten bleiben lokal. Keine Telemetrie, keine Cloud-Anbindung.
- **Robustheit:** Fehlerhafte Scans oder unerkannte Dokumente duerfen das System nicht blockieren.
- **Performance:** Dokumentenanalyse unter 30 Sekunden pro Dokument auf Consumer-Hardware (8 GB RAM).
- **Wartbarkeit:** Updates ueber `docker compose pull && docker compose up -d`.

---

## Hinweise zur Nutzung der Prompts

1. Die Prompts sind **sequenziell** zu verwenden (01 vor 02, usw.).
2. Jeder Prompt ist **eigenstaendig** - er enthaelt genug Kontext fuer die Coding-KI.
3. Jeder Prompt referenziert die vorherigen Ergebnisse, wo noetig.
4. Am Ende jedes Prompts stehen **Akzeptanzkriterien** - erst wenn diese erfuellt sind, zum naechsten Prompt wechseln.
5. **Technologie-Entscheidungen** aus diesem Dokument koennen angepasst werden - die Prompts sind aber auf den empfohlenen Stack abgestimmt.
