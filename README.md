# Zettelwirtschaft

Lokales Dokumentenmanagementsystem für Privathaushalte. Rechnungen, Belege und Dokumente werden per Scanner oder Smartphone erfasst, automatisch durch KI analysiert, kategorisiert und durchsuchbar archiviert.

Läuft ausschließlich on-premise im Heim-WLAN. Kein Cloud-Zwang, keine Abos, keine Telemetrie.

**Ausgehende Verbindungen:** Dokumente verlassen nie den Rechner. Einzige automatische Internet-Verbindung ist der Download fehlender KI-Modelle durch Ollama aus dessen Registry (beim Start, nur wenn `OLLAMA_MODEL`/`EMBEDDING_MODEL` fehlt; sichtbar sind dort nur IP-Adresse und Modellname). Abschaltbar mit `OLLAMA_AUTO_PULL=false` — Modelle dann von Hand mit Ollama herunterladen (Befehle siehe Installation). Die Update-Prüfung ist opt-in, E-Mail-Abruf nur für selbst eingerichtete Konten.

## Features

- **KI-Dokumentenanalyse** - Automatische Erkennung von Typ, Datum, Betrag, Aussteller via Ollama/LLM
- **OCR** - Text aus Scans und PDFs extrahieren (Tesseract + pdfplumber)
- **Volltextsuche** - SQLite FTS5 mit Facetten und Autocomplete
- **Steuerpaket-Export** - Belege nach Steuerkategorien filtern und als ZIP exportieren
- **Garantie-Tracker** - Ablaufdaten im Blick mit automatischen Erinnerungen
- **Smartphone-Scan** - Dokumente per Kamera erfassen (PWA)
- **KI-Assistent (RAG)** - Fragen zu eigenen Dokumenten in natürlicher Sprache stellen
- **Ablagebereiche** - Dokumente nach Bereichen organisieren (z.B. Privat, Praxis)
- **Rückfrage-System** - KI fragt bei unklaren Dokumenten gezielt nach
- **E-Mail-Import** - Anhänge aus IMAP-Postfächern übernehmen (nur ungelesene Mails, KI prüft die Relevanz)
- **Steuerrelevanz** - Direkt in der Dokumentenliste sichtbar und per Klick änderbar
- **Mehrdokument-Scans aufteilen** - Stapel aus dem Einzugsscanner werden erkannt, im Editor bestätigt und als einzelne Dokumente abgelegt

## Installation (Windows)

Es gibt zwei Installationspfade:

| Pfad | Wann | Voraussetzungen |
|---|---|---|
| **Native** (empfohlen ab v1.3) | Setup.exe, Hintergrunddienst, kein Docker | Windows 10/11, mind. 8 GB RAM, 10 GB Plattenplatz |
| **Docker** | Headless-Server, mehrere Plattformen | Docker Desktop, 8 GB RAM, 10 GB Plattenplatz |

### Pfad A — Native (Setup.exe)

Die Setup.exe installiert Zettelwirtschaft als Windows-Dienst. Der Browser öffnet sich
am Ende, im Hintergrund läuft der Dienst ohne Konsolenfenster. Aus dem LAN ist
das System unter `http://<rechnername>:8080` erreichbar.

1. [Neuestes Release herunterladen](https://github.com/phash/zettelwirtschaft/releases/latest) → `Zettelwirtschaft-<version>-Native-Setup.exe`
2. Doppelklick. Der Wizard fragt: Programmordner, Datenordner (frei wählbar — NAS,
   externe SSD, OneDrive-Sync möglich), Port, PIN-Auto-Generierung.
3. Setup installiert Tesseract + poppler (gebündelt), registriert den Backend-Dienst
   und erlaubt den Port in der Windows-Firewall (Profil „Privat").
4. **Ollama wird nicht mitinstalliert.** Setup prüft, ob Ollama vorhanden ist, und
   verlinkt sonst die Download-Seite ([ollama.com/download](https://ollama.com/download)).
   Benötigt wird **Ollama ab 0.30.3** (getestet mit 0.40.5) — ältere Versionen kennen
   die Default-Modelle nicht; der System-Status warnt dann. Ollama selbst installieren —
   danach lädt Zettelwirtschaft die benötigten Modelle (zusammen ca. 5–10 GB) beim Start
   automatisch herunter. Das Setup wählt sie passend zur Hardware: Analyse
   `gemma4:e2b-it-qat`, mit NVIDIA-GPU ab 10 GB Grafikspeicher `gemma4:12b-it-qat`;
   Suche `qwen3-embedding:4b`, bei weniger als 12 GB RAM `qwen3-embedding:0.6b`. Bis dahin landen neue Dokumente zur Prüfung; der
   System-Status in den Einstellungen zeigt fehlende Modelle bzw. den laufenden Download an.

Der Dienst heißt `ZettelwirtschaftBackend` und startet automatisch beim Systemstart.
Steuerbar über `services.msc` oder `net start/stop ZettelwirtschaftBackend`.

Migration aus einer bestehenden Docker-Installation: manuell mit
`scripts/Convert-Env-To-Config.ps1` (alte `.env` → `config.toml`) und Kopie des
Datenordners — Ablauf siehe Abschnitt „Native-Service-Ops" in [`CLAUDE.md`](CLAUDE.md).
Ein leerer ChromaDB-Ordner wird beim ersten Start neu indexiert.

**Von Hand eingerichtete Native-Installation aktualisieren** (Dienst per
`service-install.bat` registriert, z. B. nach dieser Migration): einfach die neue
Setup.exe starten. Sie erkennt den Dienst, bietet die Übernahme an, sichert Datenbank
und `config.toml` nach `Dokumente\Zettelwirtschaft-Backups`, verlegt die
Programmdateien nach `C:\Program Files\Zettelwirtschaft` und behält Datenordner,
Einstellungen und PIN.

Details: [`planung/native-windows-konzept.md`](planung/native-windows-konzept.md).

### Pfad B — Docker (klassisch)

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/) installieren und starten
2. Release-Archiv entpacken
3. `install.bat` doppelklicken — Wizard prueft Docker, konfiguriert Ports und KI-Modelle
   (Vorschlag nach Hardware wie beim Native-Setup; bei einem Update bleiben die bisherigen Modelle).

> **Hinweis:** Windows zeigt beim ersten Start moeglicherweise eine SmartScreen-Warnung ("Der Computer wurde durch Windows geschuetzt"), da der Installer nicht digital signiert ist. Das ist bei Open-Source-Software normal. Zum Fortfahren: **"Weitere Informationen"** klicken → **"Trotzdem ausfuehren"**. Der vollstaendige Quellcode ist [auf GitHub](https://github.com/phash/zettelwirtschaft) einsehbar.

### Bedienung

| Skript | Beschreibung |
|---|---|
| `start.bat` | System starten und Browser öffnen |
| `stop.bat` | System stoppen |
| `update.bat` | System aktualisieren (erstellt vorher ein Backup) |
| `uninstall.bat` | System deinstallieren |

### Manuelle Installation

1. Repository klonen:
   ```bash
   git clone https://github.com/phash/zettelwirtschaft.git
   cd zettelwirtschaft
   ```

2. Konfiguration erstellen:
   ```bash
   cp .env.example .env
   ```

3. Anwendung starten:
   ```bash
   docker compose up --build -d
   ```

4. KI-Modelle: Das Backend lädt beim Start die in `.env` konfigurierten Modelle
   (`OLLAMA_MODEL`, `EMBEDDING_MODEL`) automatisch in den Ollama-Container. Wer den
   Download vorab anstoßen will (exakt derselbe Tag wie in `.env`):
   ```bash
   docker compose exec ollama ollama pull gemma4:e2b-it-qat
   docker compose exec ollama ollama pull qwen3-embedding:4b
   ```

5. Im Browser öffnen:
   - **Anwendung:** http://localhost:8080
   - **Health-Check:** http://localhost:8080/api/health

   Das Backend (Port 8000) ist im Docker-Betrieb bewusst nur intern erreichbar;
   die API-Dokumentation (`/docs`) gibt es nur im Entwicklungsmodus
   (`uvicorn app.main:app --reload` → http://localhost:8000/docs).

### Konfiguration

Die Konfiguration erfolgt über die `.env`-Datei. Wichtige Einstellungen:

| Variable | Default | Beschreibung |
|---|---|---|
| `FRONTEND_PORT` | `8080` | Port für die Web-Oberfläche |
| `OLLAMA_MODEL` | `gemma4:e2b-it-qat` | LLM-Modell für die Analyse (exakter Ollama-Tag; die Installer schlagen bei NVIDIA-GPU ab 10 GB VRAM `gemma4:12b-it-qat` vor; bisheriger Default `qwen2.5:7b-instruct`) |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama-Server URL |
| `OCR_LANGUAGES` | `deu+eng` | OCR-Sprachen |
| `EMBEDDING_MODEL` | `qwen3-embedding:4b` | Embedding-Modell für RAG-Vektorisierung (bei < 12 GB RAM `qwen3-embedding:0.6b`; bisheriger Default `bge-m3`). Ein Wechsel baut den Vektor-Index beim nächsten Start automatisch neu auf |
| `RAG_TOP_K` | `5` | Anzahl der relevantesten Textpassagen für RAG-Antworten |
| `TIMEZONE` | `Europe/Berlin` | Zeitzone, in der Cron-Zeitpläne der E-Mail-Konten ausgewertet werden |

Vollständige Liste mit Erläuterungen: [`.env.example`](.env.example) (Docker) bzw.
[`config.toml.example`](config.toml.example) (Native).

## HTTPS für den Smartphone-Scan (Heim-WLAN)

Der **Smartphone-Scan** (Kamera) und der **PWA-Service-Worker** verlangen einen
*Secure Context* — also HTTPS oder `localhost`. Über `http://<rechner>:8080`
verweigert der Browser den Kamerazugriff. Lösung: ein selbstsigniertes
Server-Zertifikat fürs lokale Netz.

**Docker:**

```bash
# 1. Zertifikat erzeugen (10 Jahre gültig, serverAuth + SAN für localhost/LAN-IP)
bash ssl/generate-cert.sh

# 2. Mit HTTPS-Overlay starten
docker compose -f docker-compose.yml -f docker-compose.ssl.yml up -d

# 3. Am Smartphone https://<server-ip>/ öffnen, einmalig die Browser-Warnung
#    bestätigen ("Erweitert" → "Trotzdem fortfahren") — danach läuft der Scan.
```

HTTP (`:80`) wird automatisch auf HTTPS (`:443`) umgeleitet. Das Zertifikat ist
selbstsigniert; die einmalige Warnung ist im LAN normal und unbedenklich. Der
nginx-Master läuft im SSL-Overlay als root (bindet 80/443, liest den `0600`-Key),
die Worker bleiben non-root. `cert.pem`/`key.pem` liegen unter `ssl/` und werden
nicht eingecheckt (`.gitignore`).

**Native (Windows):** Zertifikat mit dem gebündelten Python erzeugen
(`<install>\bin\python.exe ssl\generate-self-signed-cert.py`) und in
`config.toml` `SERVER_SSL_CERTFILE` + `SERVER_SSL_KEYFILE` setzen — dann startet
der Dienst direkt mit TLS.

## Verzeichnisstruktur

Nach dem ersten Start werden folgende Verzeichnisse unter `data/` angelegt:

| Verzeichnis | Zweck |
|---|---|
| `data/uploads` | Hochgeladene Originaldokumente |
| `data/watch` | Scanner-Eingabeordner (automatische Erkennung; verarbeitete Dateien werden hier entfernt — bei einem selbst gewählten Watch-Ordner stattdessen in den Unterordner `verarbeitet` verschoben) |
| `data/archive` | Archivierte, verarbeitete Dokumente |
| `data/backups` | Automatische und manuelle Backups |

## Technologie-Stack

- **Backend:** Python 3.13 / FastAPI
- **Datenbank:** SQLite (SQLAlchemy + Alembic)
- **OCR:** Tesseract + pdfplumber
- **KI:** Ollama ≥ 0.30.3 mit lokalem LLM (Default `gemma4:e2b-it-qat`) + Embeddings (`qwen3-embedding:4b`)
- **Vektor-Suche:** ChromaDB (Hybrid-Suche mit FTS5 für den KI-Assistenten)
- **Frontend:** Vue.js 3 + TailwindCSS (PWA)
- **Deployment:** Native Windows-Dienst (Setup.exe) oder Docker Compose

## Changelog

### Unreleased
- **Neu analysieren mit Änderungsvorschlag (#39):** Ein Dokument lässt sich in der Detailansicht mit dem aktuellen KI-Modell neu analysieren (optional mit neuer Texterkennung). Das Ergebnis ist immer ein Vorschlag: die App zeigt die Unterschiede zum aktuellen Stand, du übernimmst alles oder nur einzelne Felder; von dir bearbeitete Felder bleiben geschützt. Ändern sich nur Titel und Zusammenfassung, gilt das Dokument als unverändert.
- **Sammellauf:** In der Dokumentliste lassen sich mehrere Dokumente auswählen und gemeinsam neu analysieren. Der Lauf startet nur, wenn keine neuen Dokumente warten oder verarbeitet werden, wartet bei Ollama-Ausfällen und setzt nach einem Neustart fort; bereits geprüfte Dokumente werden ausgelassen. Am Ende erscheint eine Benachrichtigung; die Vorschläge stehen unter „Zu prüfen“ → „Änderungsvorschläge“ und lassen sich gesammelt übernehmen.
- Datenbank: Migration `019_reanalysis` (Vorschläge, Läufe, `documents.analysis_model`).

### v1.7.0
- **Mehrdokument-Scans aufteilen:** Legt der Einzugsscanner mehrere Briefe und Rechnungen in eine PDF, erkennt die App das jetzt vor der Analyse. PDFs ab 3 Seiten aus Upload, Watch-Ordner und E-Mail-Anhängen werden Seite für Seite gelesen; Seitenzähler („Seite 1 von …“), neue Anschrift mit Anrede, Referenz- und Vertragsnummern, Trennblätter, Formatwechsel und Duplex-Blätter ergeben einen Aufteilungsvorschlag. Wurden mehrere Dokumente gefunden oder ist eine Stelle unsicher, erscheint der Stapel unter „Zu prüfen“ → „Stapel zum Aufteilen“ (mit Benachrichtigung). Ein einzelnes Dokument läuft ohne Umweg normal weiter.
- **Aufteilungs-Editor** (`/aufteilen/…`): Seiten-Vorschaubilder, Schnitte per Klick oder Tastatur setzen und entfernen, unsichere Stellen orange mit Begründung, Seiten weglassen (nie automatisch), Großansicht, Rückfrage bei Schnitten mitten durch ein Duplex-Blatt. Danach wird jeder Teil als eigenes Dokument analysiert, abgelegt und durchsuchbar; die Teile sind exakte Seitenkopien (keine Neukodierung) und tragen ihre Herkunft („Teil 2 von 3, Seiten 3–44“). Keine Seite wird zweimal per OCR gelesen.
- **„Dokument aufteilen“** in der Detailansicht und Option „Enthält mehrere Dokumente“ beim Upload. Teile eines archivierten Dokuments übernehmen Ablagebereich und manuelle Tags; das Original wird erst nach dem letzten Teil als gelöscht markiert.
- **„Aufteilung rückgängig“**, solange kein Teil bearbeitet wurde. Der Originalstapel wird 30 Tage aufbewahrt (`SPLIT_KEEP_SOURCE_DAYS`) und danach automatisch entfernt.
- **Optionale KI-Prüfung** unsicherer Stellen (höchstens ein Aufruf je Stapel); ohne erreichbares Ollama arbeitet die Erkennung allein mit den Regeln weiter. Einstellungen unter „System“ → „Verarbeitung“ (automatische Prüfung, Mindestseitenzahl, KI-Prüfung), Grenzen per Konfiguration (`SPLIT_MAX_PAGES`, `SPLIT_TIME_BUDGET_S` u. a.).
- **Robustheit:** Absturz während Erkennung oder Aufteilung setzt sauber fort (Checkpoint alle 5 Seiten, keine halben Teildateien), doppelte Bestätigung oder zwei Geräte gleichzeitig erzeugen keine doppelten Dokumente, ein erneut importierter Stapel wird als Duplikat erkannt, verschlüsselte/defekte PDFs laufen normal weiter.
- **Native:** neue Abhängigkeit `pypdfium2`; der Build prüft `pdfium.dll` und führt einen Selbsttest der fertigen Exe aus (`--self-test-pdfium`).
- **Netzwerkordner (Native):** Eingangs- und Exportordner auf einer NAS per UNC-Pfad (`\\nas\scan`) mit eigenem Benutzer/Passwort der Freigabe (verschlüsselt gespeichert); „Verbindung testen“, Überwachung per Polling mit automatischem Wiederverbinden, Status in der Systemprüfung. Netzlaufwerke (`V:`) werden mit Vorschlag des passenden UNC-Pfads abgelehnt — der Dienst sieht Laufwerksbuchstaben nicht. Der Dienst meldet sich nie mit seiner eigenen Identität an fremden Freigaben an.
- **Scanner-Freigabe:** Optional im Setup oder per Startmenü „Scanner-Freigabe einrichten“: der lokale Eingangsordner wird als `\\<PC>\Zettelwirtschaft-Eingang` für Netzwerk-Scanner freigegeben — eigenes Konto `zw-scanner`, nur Schreibrechte auf den Eingangsordner, Firewall nur fürs lokale Subnetz; Deinstallation entfernt alles.
- Docker: Hinweis zum Einbinden einer NAS als CIFS-Volume; Verhalten unverändert.
- Tests: Backend **1699** (1 übersprungen), E2E **668** (Desktop + Mobile, 2 übersprungen), Vitest 53, Skript-Tests grün.

### v1.6.2
- Fix: Die Rückfrage „Die KI schlägt einen neuen Ablagebereich … vor. Soll dieser erstellt werden?“ akzeptiert jetzt „Ja“ (legt den Bereich an und ordnet zu) und „Nein“ (Zuordnung bleibt). Bisher wurde „ja“ als Bereichsname gelesen („Ablagebereich „ja“ existiert nicht“). Die Detailansicht zeigt Antwortvorschläge zusätzlich als Buttons.
- Fix (Native-Setup): Beim Übernehmen einer alten Installation wird die kurz gesperrte alte `nssm.exe` mit Wiederholung gelöscht statt liegen zu bleiben.
- Tests: Zugangsdaten (PIN, IMAP-Passwörter, Verschlüsselungs-Key) kommen aus einer lokalen, nicht versionierten `.env.test` (Vorlage `.env.test.example` in `backend/`, `e2e/`, `scripts/`); ohne Datei werden pro Lauf Zufallswerte erzeugt. Keine Dummy-Credentials mehr im Repository.

### v1.6.1
- **Native-Setup übernimmt von Hand eingerichtete Installationen:** Bisher brach die Setup.exe ab, wenn der Dienst `ZettelwirtschaftBackend` ohne Setup eingerichtet war (z. B. nach der Docker→Native-Migration per `service-install.bat`), und verwies auf das Update-Paket, das solche Installationen ebenfalls nicht kannte. Jetzt erkennt das Setup den Dienst (Programm-, Konfigurations-, Daten- und Log-Pfad aus den NSSM-Einstellungen, auch bei Version 1.2.x/1.3.x) und bietet die Aktualisierung an: Dienst stoppen → Sicherung von Datenbank (+ WAL) und `config.toml` nach `Dokumente\Zettelwirtschaft-Backups\pre-<version>_<zeit>.zip` (ohne die alte Exe, ZIP wird geprüft) → neue Programmdateien nach `C:\Program Files\Zettelwirtschaft` → in der `config.toml` nur `FRONTEND_DIST_DIR` umstellen → Dienst neu registrieren (gleicher Log-Ordner, Ollama-Abhängigkeit) → Datenbank-Migrationen beim Start → Registry-Eintrag wie bei einer normalen Installation. Läuft die Weboberfläche, bietet das Setup an, den alten Programmordner zu löschen (nur wenn darin keine Daten liegen). Bricht das Setup vorher ab, wird der bisherige Dienst wiederhergestellt. Nicht übernehmbare Dienste (kein NSSM, fehlende/relative Pfade) werden mit konkreter Anleitung abgelehnt.
- `restore-backup.bat` spielt eine mitgesicherte WAL-Datei mit zurück.
- Release-CI: Tests mit echtem Chroma werden übersprungen, wenn nur `chromadb-client` installiert ist (der 1.6.0-Release-Lauf brach daran ab; GitHub-Release und GHCR-Images kommen deshalb mit 1.6.1).

### v1.6.0
- **Neue KI-Standardmodelle (Benchmark über 70 Dokumente, 13 LLMs, 6 Embedding-Modelle):** Analyse mit `gemma4:e2b-it-qat` (Kernfelder 84,7 % statt 54 % mit `qwen2.5:7b` beim bisherigen Schema; auf CPU ca. 40 s statt 60–110 s pro Dokument), bei NVIDIA-GPU ab 10 GB VRAM `gemma4:12b-it-qat` (90 %). Suche mit `qwen3-embedding:4b` (Trefferquote R@1 100 %), unter 12 GB RAM `qwen3-embedding:0.6b`. Die Installer wählen die Stufe anhand von RAM und Grafikspeicher. **Bestehende Installationen behalten ihre Modelle.** Wechsel des Suchmodells baut den Index automatisch neu auf, sobald das Modell geladen ist. Das Analyse-Schema verlangt jetzt alle Felder — das allein hebt die Genauigkeit aller Modelle deutlich. Ollama ≥ 0.30.3 (getestet 0.40.5), die Systemprüfung warnt bei älteren Versionen.
- **Hintergrund-Vorgänge:** Neu-Analyse, E-Mail-Abruf, Voll-Backup und Vektor-Neuaufbau laufen im Hintergrund mit Fortschrittsanzeige und überstehen das Schließen der Seite.
- **Review:** „Später" ändert den Status nicht mehr, „So übernehmen" übernimmt die Werte ausdrücklich; Neu-Analysen überschreiben keine Nutzerkorrekturen; wiederholte Korrekturen (ab 3×) werden automatisch angewendet.
- **Code-Review-Härtung (222 Befunde, Blocker bis Low)** u. a. robuste Volltextsuche (Bindestriche, Daten, Beträge), UID-basierter IMAP-Abruf mit sicherem Verschieben, Steuerjahr-Feld, Hinweis auf Nicht-EUR-Belege im Steuerexport, Upload-Link auf das erzeugte Dokument, Upgrade-Modus im Native-Setup.
- **Security-Audit (40 Befunde):** Schutz gegen DNS-Rebinding (`ALLOWED_HOSTS`) und CSRF, kein Credential-CORS mehr, IMAP-Zertifikatsprüfung, PIN-Sperre mit globalem Fehlversuchsbudget, gehärtete Ordnerpfade (UNC, `\\?\`, Junctions), Security-Header und ACL-Härtung im Native-Modus, Limits gegen PDF-/E-Mail-/Request-Bomben, CSV-Formel-Injection im Steuerexport, keine internen Fehlertexte in der API. `OLLAMA_AUTO_PULL` schaltet den automatischen Modell-Download ab.
- **Abhängigkeiten:** Python 3.13, FastAPI 0.143, Starlette 1.8, SQLAlchemy 2.1, Pillow 13, Pinia 4, vite-plugin-pwa 2, Playwright 1.64, TypeScript 7, nginx 1.30, Node 24, Ollama-Image 0.40.5.
- **Verhaltensänderungen:** Zugriff über eigene Domains/Reverse-Proxy braucht einen Eintrag in `ALLOWED_HOSTS`; Netzwerk-Watch-Ordner brauchen `ALLOW_NETWORK_FOLDERS=true`; IMAP-Server mit selbstsigniertem Zertifikat werden abgelehnt; mehr als 3 gleichzeitige Re-Analysen → 429; Backups brechen bei zu wenig Speicher ab; `/docs` nur mit `ENABLE_API_DOCS=true`.
- Tests: Backend **1270** (1 übersprungen), E2E **551** (Desktop + Mobile), Vitest 41.

### v1.5.0
- **In-App-Hilfe & Onboarding:** Geführter Einstieg für neue Nutzer. 5-Schritt-Willkommens-Tour (zeigt sich beim ersten App-Start, schließbar, jederzeit erneut startbar) sowie eine Hilfe-Seite unter `/hilfe` (Schnellstart, 9 Funktions-Karten mit Deep-Links, 12er-FAQ, Datenschutz-Hinweis). „Hilfe" in der Seitenleiste + „?"-Button im Header. Barrierefrei (Dialog-Rolle, Fokus-Management). Tests: +6 E2E.
- **Code-Review-Härtung (24 Findings bis Low gefixt):**
  - Fix (**Blocker**): Das HTTPS-Overlay aus v1.4.1 (`docker-compose.ssl.yml`) crash-loopte beim Start — `user: root` zusammen mit `cap_drop: ALL` verhindert das Binden von 80/443 und das Lesen des 0600-Keys. Jetzt werden gezielt die fünf nötigen Capabilities zurückgegeben (NET_BIND_SERVICE, DAC_OVERRIDE, SETUID, SETGID, CHOWN); verifiziert.
  - Fix (High): Native-Restore mergte Dokumente statt zu ersetzen (Archiv/DB-Divergenz) → altes Archiv wird zur Seite gelegt; Batch-Elevation escaped `'` in Pfaden (Injection); Onboarding-Auto-Start auf Auth-State gegated.
  - Fix (Medium): `parse_version` Unicode-Ziffern (latenter 500) + Cache auf Manifest-URL gekeyt; Voll-Backups werden gepruned + Temp-DB ins System-Temp; Zip-Slip-Schutz im Restore; Registry-Version nach Update nachgezogen; `generate-cert.sh` delegiert an den Python-Generator.
  - Fix (Low): HSTS gekürzt (Self-Signed/IP), `.gitignore` für Keys breiter, `release_url` nur als Link bei `https://`, diverse a11y-/Hygiene-Fixes.
- Tests: Backend **393** (2 übersprungen).

### v1.4.1
- **HTTPS / selbstsigniertes Zertifikat (Heim-WLAN):** Ermöglicht den Smartphone-Scan (Kamera-API) und den PWA-Service-Worker im LAN — beide verlangen einen *Secure Context* (HTTPS oder localhost). Neu: `ssl/generate-self-signed-cert.py` (RSA-2048, **serverAuth-EKU** + SAN für localhost/127.0.0.1/LAN-IP/Hostname, 10 J., CA:FALSE, Key 0600) und `ssl/generate-cert.sh` (openssl-Wrapper mit Python-Fallback), `ssl/nginx-ssl.conf` (HTTP→HTTPS-Redirect + TLS auf 443), `docker-compose.ssl.yml` (Overlay, Host-Ports 80/443; nginx-Master als root, Worker non-root — Standard-TLS-Modell). Native-Modus: `SERVER_SSL_CERTFILE`/`SERVER_SSL_KEYFILE` → uvicorn startet direkt mit TLS. Vorgehen analog praxiszeit v1.8.7. Doku: README „HTTPS für den Smartphone-Scan". Tests: +2 (Cert-Generator: serverAuth/SAN/CA:FALSE/RSA-2048/Key-0600).

### v1.4.0
- **Update-Prüfung (opt-in):** Neuer Backend-Endpoint `GET/PUT /api/system/update-check` vergleicht die installierte Version mit einem veröffentlichten Manifest (`latest.json`) und meldet verfügbare Updates. **Telemetriefrei und standardmäßig deaktiviert** — erst nach ausdrücklicher Aktivierung in den Einstellungen wird ein externer Aufruf gemacht (nur die Manifest-Abfrage, keine Dokument- oder Nutzungsdaten). Frontend: „Updates"-Karte in den Einstellungen mit Opt-in-Schalter, Versionsanzeige und „Update verfügbar"-Banner inkl. Download-Link. Gecached (6 h), graceful bei Netzwerkfehlern. Konfiguration: `UPDATE_CHECK_ENABLED` (Default `false`), `UPDATE_MANIFEST_URL`. Tests: +13 (Versionsvergleich, Endpoint-Opt-in, E2E-Karte).
- **Präsentations-/Download-Website:** Statische Marketing- und Download-Seite (Präsentation, Hilfe/FAQ, Download mit Prüfsummen, Impressum, Datenschutz) unter `https://zettelwirtschaft.mr-development.de` — selbst-gehostete Fonts (keine externen Aufrufe), strenge CSP, barrierefrei.

### v1.3.1
- Fix: ChromaDB-Server-Image (`docker-compose.yml`) von 1.0.20 auf **1.5.9** angehoben — passend zum in v1.3.0 migrierten `chromadb-client` 1.5. Beseitigt den Client/Server-Versions-Skew im Docker-Pfad (Native/embedded war nicht betroffen). Innerhalb 1.x ist das Volume-Format kompatibel; bei Problemen Vektor-Index über die System-Wartung neu aufbauen.
- Chore: GitHub-Actions im Release-Workflow auf Node-24-Majors aktualisiert (checkout v6, setup-python v6, docker/setup-buildx v4, docker/login v4, docker/build-push v7, action-gh-release v3) — behebt die Node-20-Deprecation (GitHub erzwingt Node 24 ab 2026-06-16).

### v1.3.0
- **Native-Windows-Modus (Foundation):** Setup.exe → NSSM-Service `ZettelwirtschaftBackend`, PyInstaller-Onedir-Bundle, Frontend über FastAPI `StaticFiles`, ChromaDB embedded (`PersistentClient`), Tesseract + poppler gebündelt, Konfiguration über `config.toml`. Migrationspfad Docker → Native dokumentiert und getestet.
- **Aggressive Abhängigkeits-Migration auf neueste Stable-Versionen:** Frontend **Vite 7 → 8** und **TypeScript 5 → 6** (Major), vue-router 5.1, Pinia 3.0.4, Vue 3.5.38, Tailwind 4.3; Backend FastAPI 0.137, uvicorn 0.49, cryptography 49, SQLAlchemy 2.0.51, pytest 9 / pytest-asyncio 1.x, chromadb 1.5. Plugin-Peer-Kompatibilität für Vite 8 vorab verifiziert (kein `--legacy-peer-deps`).
- **Code-Review-Hardening (Blocker → Low):**
  - Fix (Blocker): `migrate.detect_stamp()` lieferte drei nicht existierende Alembic-Revisions-IDs (001/003/004) → `alembic upgrade head` brach bei alten Legacy-DBs ab und die App startete nicht. Korrigiert + Guard-Test.
  - Fix (High): System-Health prüfte ChromaDB im Native/embedded-Modus fälschlich per HTTP (Status dauerhaft „degraded"); Re-Analyse aktualisiert jetzt FTS-Index + Vektoren (Suche/Chat waren veraltet); Garantie-Update ohne riskanten `session.refresh()` auf einer `lazy="raise"`-Relationship.
  - Fix (Medium): PIN-Lockout mit exponentiellem Backoff statt Reset alle 30 s; statische Frontend-Shell im Native-Modus auch bei aktivem PIN erreichbar (Login-Seite ladbar); Watch-Ordner kopiert und löscht die Quelldatei erst nach DB-Commit (rollback-sicher); Decimal-Konvertierung am Schreib-Rand von `Document.amount`; Vektor-Rebuild-Task mit starker Referenz.
  - Fix (Low): Pillow-Decompression-Bomb-Schutz, gehärtete IMAP-Test-Fehlermeldung, `/api/health` gibt keine DB-Exception-Details mehr preis; Frontend: PWA-Runtime-Caching griff nie (Match gegen `url.href` statt `pathname`), Header-Suche aktualisiert Ergebnisse auch auf der Suchseite, StatCard-Icons in Steuer/Garantie, statischer Auth-/Router-Import (Vite-8-Build ohne Warnungen) und weitere Robustheits-/Hygiene-Fixes.
- Tests: Backend **374** (1 übersprungen), E2E **145** (chromium) grün.

### v1.2.2
- Feature: „Erneut analysieren"-Button bei fehlgeschlagener LLM-Analyse (z.B. wenn Ollama beim Dokumentimport nicht erreichbar war)
- Neuer Endpunkt `POST /api/review/documents/{id}/reanalyze` — führt LLM-Analyse mit vorhandenem OCR-Text erneut durch
- Fix: `entrypoint.sh` CRLF-Zeilenenden korrigiert (lokaler Docker-Build schlug fehl)

### v1.2.1
- Fix: PDF-Vorschau in Dokumentendetails zeigte die App statt das Dokument (Service Worker fing `/api/.../file` Requests ab und lieferte `index.html` als Fallback)
- Fix: `navigateFallbackDenylist` für `/api/` in PWA-Konfiguration
- Fix: Datei- und Thumbnail-Endpunkte auf `NetworkOnly` (keine SW-Cache-Interferenz)
- AGPL-3.0 Lizenz hinzugefügt
- SmartScreen-Hinweis in Installationsanleitung

### v1.2.0
- **ReviewView: Kontext für KI-Rückfragen** — Kontext-Cards unter jeder Rückfrage zeigen betroffenes Feld und erkannten KI-Wert an. Erkannte-Daten-Block hebt das betroffene Feld der aktiven Frage visuell hervor (amber Highlighting).
- **Umlaute im gesamten Frontend** — Alle sichtbaren UI-Texte von ASCII-Ersetzungen (ue/oe/ae) auf echte deutsche Umlaute (ü/ö/ä) umgestellt. Betrifft ReviewView, Sidebar, Dashboard, DocumentDetail, Documents, Search, Chat, Settings, Tax und E2E-Tests.

### v1.1.1
- Fix (kritisch): Datenbankmigrationen laufen jetzt automatisch beim Container-Start (`alembic upgrade head` via `entrypoint.sh`)
- Fix: Legacy-Installationen ohne `alembic_version`-Tracking werden automatisch erkannt und korrekt gestempelt (betrifft Updates von v1.0.x auf v1.1.x)

### v1.1.0
- **Umfassender Audit + Bugfix-Release (43 Fixes)**
- Fix (kritisch): Datei wird erst nach DB-Flush verschoben (verhindert Datenverlust bei DB-Fehler)
- Fix (kritisch): SQLite-Backup via `sqlite3.backup()` API statt Dateikopie (konsistente Sicherung bei laufenden Schreibvorgängen)
- Fix (kritisch): `WarrantyInfo.document` Relationship mit `lazy="selectin"` (verhindert N+1 Queries und MissingGreenlet)
- Fix (kritisch): Login mit falschem PIN gibt HTTP 401 statt 200 zurück
- Fix: Garantie-Erinnerungen für30-Tage und Ablauf-Schwelle (separate Flags pro Schwelle statt einzelnes Boolean)
- Fix: ChromaDB-Suche blockiert nicht mehr den Event Loop (`asyncio.to_thread`)
- Fix: Tesseract-OCR läuft nur noch einmal pro Bild (doppelter Aufruf eliminiert, Performanceverbesserung)
- Fix: XSS-Risiko bei Suchergebnis-Highlights behoben (`v-html` durch sichere Text-Interpolation ersetzt)
- Fix: Chat-Eingabe auf Mobilgeräten nicht mehr hinter BottomNav verborgen
- Fix: Settings-Seite flasht nicht mehr alle 10 Sekunden beim Health-Polling
- Fix: Division-durch-Null in Garantie-Fortschrittsbalken und Steuer-Kategoriebalken
- Fix: Doppelte API-Calls bei Filterwechsel in Dokumentenliste eliminiert
- Fix: Sortierungswechsel in Suche setzt Seite auf 1 zurück
- Fix: Retry in Dashboard startet Polling und aktualisiert Jobansicht
- Fix: UploadView räumt Polling-Interval bei Navigation auf (Memory-Leak behoben)
- Fix: PIN-Schutz bleibt bei Backend-Netzwerkfehler aktiv (sicherer Default)
- Fix: ReviewQuestionResponse-Schema um 4 fehlende Felder erweitert (question_type, explanation, suggested_answers, priority)
- Fix: Tag.documents Relationship auf `lazy="noload"` (unnützes Laden aller Dokumente pro Tag verhindert)
- Fix: Atomare Job-Übernahme im Queue-Worker (optimistic locking statt TOCTOU)
- Fix: IntegrityError bei parallelen Duplikat-Uploads wird korrekt als ValueError behandelt
- Fix: FTS5-Operator-Injection verhindert (Spaltenfilter und Boolesche Operatoren werden sanitized)
- Fix: `float()` bei LLM-Antworten mit try/except abgesichert
- Fix: HTTP 5xx von Ollama wird jetzt wiederholt (Retry-Logik erweitert)
- Fix: Prompt-Template-Injection durch OCR-Text verhindert (Ersetzungsreihenfolge korrigiert)
- Fix: `str.format()` in RAG-Service durch `.replace()` ersetzt (KeyError bei geschweiften Klammern verhindert)
- Fix: SHA-256 Hash-Berechnung async via `asyncio.to_thread` (blockiert Event Loop nicht mehr)
- Fix: Backup-Erstellung async (blockiert Event Loop nicht mehr)
- Fix: Image.open mit Context Manager in OCR (Dateideskriptor-Leak behoben)
- Fix: SavedSearchResponse.query_params wird als Dict statt JSON-String geliefert
- Fix: JSON.parse bei gespeicherten Suchen mit try/catch abgesichert
- Fix: POST /saved-searches gibt HTTP 201 statt 200 zurück
- Fix: 404-Catch-All-Route im Frontend-Router (unbekannte URLs leiten zum Dashboard)
- Fix: Touch-Event-Support fürBild-Pan/Zoom in ReviewView (Smartphone-Unterstützung)
- Fix: Review-Skip setzt review_status auf OK statt No-Op
- Fix: App-Version dynamisch aus VERSION-Datei statt hardcodiert
- Fix: Notifications-Endpoint mit Paginierung (limit/offset statt hardcodiertem limit 50)
- Fix: sort_by/sort_order Query-Parameter mit Regex-Validierung
- Fix: models/__init__.py um SystemSetting und ChatMessage Imports ergänzt
- Migration: 008_add_warranty_reminder_flags (separate 90d/30d/0d Reminder-Flags)
- Tests: 236 Tests (angepasst an neue HTTP-Statuscodes)

### v1.0.9
- Feature: Host-Ordner fürWatch/Export via Docker-Volume-Mount (Windows-Pfade wie `V:\Zettelwirtschaft` werden als Docker-Volume gemountet)
- Feature: Installationspfad in Settings anzeigen mit "Ordner öffnen"-Button (kopiert Explorer-Befehl)
- Feature: Settings Auto-Refresh (Health-Status alle 10 Sekunden aktualisiert)
- Fix: Ablagebereich-Dropdown immer sichtbar + Schnell-Anlegen per "+"-Button (#15)
- Fix: Fehlgeschlagene Jobs mit Retry-Button, selektierbarer Fehlermeldung und Zeitstempel (#16)
- Fix: ChromaDB-Image auf 0.6.3 gepinnt (Kompatibilität mit chromadb-client 0.6.x)
- Fix: Bessere Fehlermeldungen bei leeren Exceptions im Queue-Worker
- Tests: 237 Tests (Host-Mount-Settings, Retry-Endpoint)

### v1.0.8
- Fix: Dashboard zeigte keine Statistiken (falscher API-Endpunkt `/stats` statt korrektem Pfad)
- Fix: Installer schrieb Version zirkulär aus API (las `data/.version`, schrieb sie zurück) - jetzt direkt aus Installer-Paket
- Fix: Windows-Pfade (z.B. `V:\Ordner`) in Watch/Export-Ordner werden erkannt und mit Warnung markiert (Docker kann nur Container-Pfade lesen)
- Fix: ChromaDB-Fehler zeigt jetzt Hilfetext mit Diagnosebefehlen statt nur "HTTP 404"
- Verbesserung: Ordner-Einstellungen mit Standard-Button und Pfad-Hinweisen
- Tests: 232 Tests (System-Settings Roundtrip, Dashboard-Stats-Struktur)

### v1.0.7
- Fix: Installer-Log zeigte Docker-Fortschrittszeilen hunderte Male (Downloading-Spam durch \r-Output)
- Fix: Progressbar zeigt jetzt animierten Marquee während Docker-Images geladen werden statt einzufrieren

### v1.0.6
- Fix: Setup.exe enthielt keine VERSION-Datei - Installer zeigte immer "v1.0.3" als neue Version
- Fix: Fallback-Version im Installer von "1.0.3" auf "unbekannt" geändert

### v1.0.5
- Feature: Installierte Version im Sidebar-Footer und in Einstellungen → System angezeigt
- Feature: `/api/system/health` liefert jetzt `app_version` aus `data/.version`
- Fix: Installer schreibt nach Install tatsächliche Backend-Version (nicht mehr aus VERSION-Datei)
- Fix: Installer-Erkennung einer bestehenden Installation robuster (version-Datei allein reicht)
- Fix: Versionsvergleich im Installer - Downgrade wird als Warnung angezeigt
- Fix: Ollama-Modell-Download wird übersprungen wenn Modell bereits vorhanden
- Feature: Dashboard zeigt fehlgeschlagene Jobs mit "Kopieren"-Button fürClaude Code Fehler-Report
- Feature: Dashboard Queue-Pause/Fortsetzen-Button
- Feature: Dashboard Auto-Polling wenn aktive Jobs vorhanden
- Feature: ReviewView - Zoom (Mausrad + Buttons), Pan, Download, In-neuem-Tab-öffnen

### v1.0.4
- Fix: GUI-Installer - Erkennung bestehender Installation und Migrations-Dialog
- Fix: GUI-Installer - semantischer Versionsvergleich (Downgrade-Warnung)

### v1.0.3
- Fix: GUI-Installer (`install-gui.ps1`) - Absturz beim Klick auf Watch-Ordner-Checkbox behoben (`$btnBrowse` war als lokale Variable nicht im Event-Handler-Scope verfügbar)

### v1.0.2
- Fix: `app`-Namenskonflikt in der `lifespan`-Funktion behoben
- Feature: Watch-Ordner Startup-Scan, UI-konfigurierbare Ordner, Export-Ordner

### v1.0.1
- Fix: Docker Health-Checks auf curl-freie Alternativen umgestellt, ChromaDB Health-Check hinzugefügt

### v1.0.0
- Erstveröffentlichung mit vollem Feature-Set (RAG-Assistent, PIN-Schutz, Ablagebereiche, Steuerrelevanz-Checkbox)

## Entwicklung

### Tests ausführen

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q

# E2E (Playwright, API gemockt): baut das Frontend und startet einen eigenen
# Preview-Server — nie gegen Port 8080 (laufende Installation) testen.
cd ../e2e
npx playwright test --project=chromium
```

Aktuelle Testzahlen: `python -m pytest --collect-only -q` bzw. `npx playwright test --list`.

### Releases erstellen

Ein neues Release wird automatisch ueber GitHub Actions erstellt:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Dies erzeugt:
- GitHub Release mit Installer-Archiv (ZIP + tar.gz)
- Docker Images auf `ghcr.io/phash/zettelwirtschaft/backend` und `frontend`

### Native-Build (Setup.exe)

Lokaler Build der `Zettelwirtschaft-<version>-Native-Setup.exe`:

```powershell
# Voraussetzungen am Build-Host:
# - Python 3.13, Node 22.12+ (empfohlen 24 LTS), NSIS 3.x (makensis im PATH)
# - tools/tesseract/, tools/poppler/, tools/nssm/win64/nssm.exe vorbereitet
#   (idempotent per `pwsh scripts/fetch-build-tools.ps1`, braucht 7-Zip)

# Build-Dependencies installieren:
pip install -r backend/requirements.txt -r backend/requirements-build.txt

# Full Build (Backend + Frontend + Installer):
pwsh scripts/build-native.ps1

# Output: Zettelwirtschaft-<version>-Native-Setup.exe im Repo-Root
# plus dist/native/ mit dem entpackten Bundle.
```

Komponenten des Native-Builds:
- **Backend:** PyInstaller-Onedir-Bundle (`backend/zettelwirtschaft.spec`)
- **Frontend:** Vite-Static-Build, vom Backend ueber `FRONTEND_DIST_DIR` serviert
- **Tesseract + poppler:** gebundled aus `tools/`
- **NSSM:** Windows-Service-Wrapper
- **ChromaDB:** embedded (`PersistentClient`, kein separater HTTP-Service)
- **Konfiguration:** `config.toml` (vom Installer generiert, vom Backend via `ZETTELWIRTSCHAFT_CONFIG` env gelesen)

## Lizenz

Dieses Projekt steht unter der [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

Copyright (C) 2026 Manuel Rödig
