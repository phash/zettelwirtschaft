# Prompt 09 - Smartphone-Integration (PWA mit Kamera-Scan)

## Kontext

Ich arbeite am Projekt "Zettelwirtschaft" - einem lokalen Dokumentenmanagementsystem. Die Web-Oberflaeche (Prompt 05) ist desktop-optimiert. Nutzer wollen aber auch unterwegs oder im Haushalt schnell einen Beleg mit dem Smartphone abfotografieren und ins System schicken - ohne eine App aus dem Store installieren zu muessen. Das System laeuft ausschliesslich im Heim-WLAN.

## Aufgabe

Erweitere die bestehende Vue.js-Webanwendung zu einer Progressive Web App (PWA) mit Fokus auf mobile Dokumentenerfassung per Smartphone-Kamera. Kein App-Store noetig - der Benutzer oeffnet die Web-Adresse im Browser und kann die App zum Homescreen hinzufuegen.

## Anforderungen

### 1. PWA-Grundkonfiguration

#### Service Worker:
- Offline-Faehigkeit fuer Basis-UI (Erfassungs-Formular)
- Cache-Strategie: Network-First fuer API, Cache-First fuer statische Assets
- Background-Sync: Fotos zwischenspeichern wenn Netzwerk kurz weg ist

#### Web App Manifest (`manifest.json`):
```json
{
  "name": "Zettelwirtschaft",
  "short_name": "Zettel",
  "description": "Dokumente scannen und archivieren",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#2563eb",
  "orientation": "portrait",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

- Vite PWA Plugin (`vite-plugin-pwa`) fuer automatische Generierung

### 2. Mobile Scan-Ansicht (`/scan`)

Hauptfeature fuer Smartphone-Nutzung - eine dedizierte, mobiloptimierte Scan-Seite:

**Kamera-Ansicht:**
- Vollbild-Kameraansicht (HTML5 `getUserMedia` API)
- Rueckkamera als Default (Umschalten auf Frontkamera moeglich)
- Ausloeser-Button gross und mittig unten
- Blitz-Toggle (falls verfuegbar)

**Dokumentenerkennung (optional, nice-to-have):**
- Einfache Kantenerkennung im Kamerabild (mit Canvas + einfacher Bildverarbeitung)
- Rahmen um erkanntes Dokument anzeigen
- Falls zu komplex: Weglassen und stattdessen Hilfslinien anzeigen ("Dokument innerhalb des Rahmens positionieren")

**Nach dem Foto:**
- Vorschau des aufgenommenen Bildes
- Optionen:
  - "Weitere Seite" - fuer mehrseitige Dokumente (sammelt mehrere Bilder)
  - "Fertig & Hochladen" - schliesst Erfassung ab und laedt hoch
  - "Verwerfen" - Foto loeschen und erneut aufnehmen
- Einfache Bildkorrektur:
  - Drehen (90-Grad-Schritte)
  - Zuschneiden (optional)

**Mehrseitige Dokumente:**
- Seitenleiste/Karussell zeigt aufgenommene Seiten als Thumbnails
- Reihenfolge per Drag-and-Drop aendern
- Einzelne Seiten loeschen oder neu aufnehmen
- Alle Seiten werden als ein Dokument hochgeladen

**Upload:**
- Automatische Komprimierung (Qualitaet 85%, max. 2000px Breite - guter Kompromiss aus Qualitaet und Groesse)
- Fortschrittsanzeige
- Bestaetigung nach erfolgreichem Upload
- Bei Netzwerkproblemen: Lokal zwischenspeichern (IndexedDB), spaeter automatisch hochladen

### 3. Alternatives Hochladen (Dateiauswahl)

Nicht jeder will die In-App-Kamera nutzen:

- "Aus Galerie waehlen"-Button als Alternative
- Akzeptiert Bilder und PDFs aus dem Dateisystem
- Gleicher Upload-Flow wie Kamera-Erfassung

### 4. Mobile Navigation

Angepasste Navigation fuer Smartphones:

- **Bottom Navigation Bar** (statt Desktop-Sidebar) mit:
  - Scan (Kamera-Icon) - Hauptaktion, hervorgehoben
  - Dokumente (Liste)
  - Suche (Lupe)
  - Mehr (Hamburger -> Steuer, Garantien, Einstellungen)
- Hamburger-Menue fuer selten genutzte Funktionen

### 5. Mobile Dokumentenliste

Angepasste Darstellung fuer kleine Bildschirme:

- Karten-Layout statt Tabelle
- Jede Karte: Thumbnail, Titel, Typ-Badge, Datum, Betrag
- Wischen nach links zum Loeschen (mit Bestaetigung)
- Pull-to-Refresh
- Unendliches Scrollen statt Paginierung

### 6. Mobile Dokumenten-Detailansicht

- Vollbild-Bildansicht mit Pinch-to-Zoom
- Metadaten unterhalb des Bildes (scrollbar)
- Schnellaktionen: Teilen, Herunterladen

### 7. Lokale Netzwerk-Erkennung

Da das System nur im Heim-WLAN laeuft:

- **mDNS/Bonjour-Unterstuetzung:** Backend registriert sich als `zettelwirtschaft.local` im lokalen Netzwerk
  - Benutzer gibt im Smartphone-Browser `http://zettelwirtschaft.local` ein
  - Docker-Container mit Avahi-Daemon fuer mDNS
- **QR-Code auf der Desktop-Oberflaeche:**
  - Einstellungsseite zeigt QR-Code mit der URL des Systems
  - Smartphone scannt QR-Code -> oeffnet Web-App direkt
- **Fallback:** IP-Adresse manuell eingeben (im Setup-Guide erklaert)

### 8. Backend-Anpassungen

#### Upload-Endpoint erweitern:

`POST /api/documents/upload/mobile`

- Akzeptiert mehrere Bilder als ein Dokument (mehrseitig)
- Konvertiert mehrere Bilder automatisch zu einer PDF
- Komprimierung serverseitig nachbessern falls noetig
- Metadaten: Device-Info, Aufnahme-Zeitpunkt

#### mDNS-Service:

- Avahi-Daemon im Docker-Container oder Python-basiertes mDNS (`zeroconf`)
- Registriert den Service im lokalen Netzwerk

### 9. Sicherheit im lokalen Netzwerk

Da kein HTTPS im LAN (ohne eigene Zertifikate):

- Kamera-API benoetigt HTTPS oder `localhost`
  - Loesung 1: Selbst-signiertes Zertifikat im Docker-Setup (automatisch generiert)
  - Loesung 2: Benutzer muss im Browser eine Ausnahme bestaetigen
  - Hinweis in der Installationsanleitung
- Kein Authentifizierungssystem noetig (privates Netzwerk), aber:
  - Optional: Einfacher PIN-Schutz (4-6 Ziffern) als Barriere gegen versehentlichen Zugriff

## Technische Details

### Neue Dependencies (Frontend):
- `vite-plugin-pwa` (Service Worker + Manifest)
- `workbox` (Cache-Strategien, automatisch ueber vite-plugin-pwa)

### Neue Dependencies (Backend):
- `zeroconf` (Python mDNS-Registrierung) oder Avahi im Docker
- `img2pdf` oder `Pillow` (Bilder zu PDF zusammenfuegen)

### Docker-Anpassungen:
- Self-signed TLS-Zertifikat generieren beim ersten Start
- Nginx-Config um HTTPS erweitern
- mDNS-Service starten
- Ports: 80 (HTTP) + 443 (HTTPS)

### Responsive Breakpoints:
- Mobile: < 768px (Karten-Layout, Bottom-Nav)
- Tablet: 768px - 1024px (Hybrid)
- Desktop: > 1024px (bestehende Sidebar)

## Akzeptanzkriterien

- [ ] Web-App kann auf dem Smartphone zum Homescreen hinzugefuegt werden
- [ ] Kamera oeffnet sich auf `/scan` und nimmt Fotos auf
- [ ] Mehrseitige Dokumenten-Erfassung funktioniert (mehrere Fotos -> ein Dokument)
- [ ] Bilder werden komprimiert und als Dokument hochgeladen
- [ ] Upload funktioniert zuverlaessig ueber WLAN
- [ ] Bottom-Navigation ist auf dem Smartphone benutzbar
- [ ] Dokumentenliste ist als Karten-Layout mobilfreundlich
- [ ] Pull-to-Refresh aktualisiert die Dokumentenliste
- [ ] QR-Code auf Desktop-Seite fuehrt Smartphone zur Web-App
- [ ] `http://zettelwirtschaft.local` ist im LAN erreichbar (mDNS)
- [ ] PWA funktioniert offline fuer die Scan-Funktion (Upload wird nachgeholt)
- [ ] Selbst-signiertes Zertifikat ermoeglicht Kamera-Zugriff im LAN

## Nicht-Ziele dieses Prompts

- Keine native App (iOS/Android)
- Keine Cloud-Synchronisierung
- Keine automatische Dokumentenerkennung per KI auf dem Smartphone (das macht das Backend)
- Keine Push-Benachrichtigungen (Server muesste dafuer oeffentlich erreichbar sein)
