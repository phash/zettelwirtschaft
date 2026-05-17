# Zettelwirtschaft Native-Windows-Konzept

**Status:** Konzept · Entwurf 2026-05-17
**Ziel:** Eine Setup.exe, die der User runterlaedt, doppelklickt und danach laeuft
Zettelwirtschaft als Windows-Service. Keine Docker-Installation noetig. DB-Pfad
frei waehlbar. Aus dem Heim-Netzwerk wie bisher erreichbar.

---

## 1. Vision (User-Sicht)

1. User laedt `Zettelwirtschaft-Setup-1.3.0.exe` (~ 400-600 MB inkl. Modelle) runter.
2. Doppelklick → Setup-Wizard auf Deutsch:
   - Lizenz akzeptieren
   - **Installationsordner** fuer Programm (Default: `C:\Programme\Zettelwirtschaft`)
   - **Datenordner** fuer DB+Archiv+Uploads (Default: `C:\Users\<Name>\Documents\Zettelwirtschaft`, **frei waehlbar**)
   - PIN setzen (per Default an, 6-stelliger PIN auto-generiert + angezeigt)
   - Port waehlen (Default 8080)
   - Watch-Ordner aktivieren? (optional)
   - LLM-Modell-Auswahl (qwen2.5:7b / llama3.2 — Download-Indikator)
3. Installation laeuft: Komponenten installiert + Windows-Service registriert + LLM-Modell gepullt
4. Browser oeffnet automatisch `http://localhost:<port>`
5. Aus dem LAN erreichbar via `http://<hostname>:<port>` ohne weitere Konfiguration
6. Tray-Icon im Taskbar zeigt Status, bietet Start/Stop/Logs/Open Browser

Der laufende Dienst startet automatisch bei jedem Windows-Login (oder System-Start je nach Auswahl) und ist im Hintergrund verfuegbar.

---

## 2. Komponenten-Auswahl

| Komponente | Heute (Docker) | Native | Begruendung |
|---|---|---|---|
| **Backend** | FastAPI in Linux-Container | **PyInstaller-Onedir-Build + NSSM als Windows-Service** | Onedir statt onefile spart Antivirus-False-Positives + Startup-Zeit |
| **Frontend** | nginx in Container, Port 8080 | **Backend serviert die statische `dist/` selbst via FastAPI `StaticFiles`** | Spart einen Process + nginx-Setup. Wegen LAN-Last (Heim, max. 5 Clients) kein Performance-Problem |
| **Ollama** | Container `ollama/ollama:latest` | **Nativer Windows-Installer von ollama.com** (Bundle mitliefern) | Hat eigene .exe, laeuft als Background-Service, GPU-Detection out of the box |
| **ChromaDB** | Container `chromadb/chroma:1.0.20`, HTTP | **Embedded `chromadb.PersistentClient`** in den Backend-Prozess | Spart eigenen Service. Kein HTTP-Roundtrip. Auth-Frage entfaellt komplett (in-process). |
| **Tesseract** | Im Container apt-installed | **Nativer Tesseract-OCR-Installer** (Tesseract-OCR-w64-setup-5.x.exe, bundled) | Sprachpakete deu+eng als Teil der Bundle-MSI |
| **SQLite** | aiosqlite in Container | **Unveraendert** — ist eh nur eine Python-Lib mit eingebetteter sqlite3.dll | Datei-basiert, kein Service |
| **poppler** (pdf2image) | apt | **poppler-windows als Bundle** | Wird von pdf2image gebraucht |

**Service-Topologie nativ:**
- **Ein** Windows-Service (`Zettelwirtschaft Backend`) der python.exe → uvicorn → FastAPI startet
- Ollama laeuft als separater Windows-Service (`Ollama`), den der Ollama-Installer einrichtet
- Frontend ist statisch im Backend-Prozess
- ChromaDB ist embedded (kein Service)

---

## 3. Konfiguration & Datenpfade

### 3.1 Trennung Programm vs. Daten

```
C:\Programme\Zettelwirtschaft\           ← Read-only, vom Installer verwaltet
├── backend\                             (PyInstaller-Bundle)
│   ├── zettelwirtschaft-backend.exe
│   ├── python313.dll, _internal\, etc.
│   └── app\, alembic\, prompts\
├── frontend\                            (statischer Vite-Build)
│   └── dist\                            (index.html, assets/...)
├── bin\
│   ├── tesseract.exe (+ tessdata\)
│   └── poppler\ (pdftoppm.exe etc.)
├── nssm.exe                             (Service-Wrapper)
├── service-install.bat / service-uninstall.bat
├── VERSION
└── Uninstall.exe

C:\Users\<Name>\Documents\Zettelwirtschaft\     ← User-Daten (frei waehlbar!)
├── data\
│   ├── zettelwirtschaft.db              (SQLite)
│   ├── chromadb\                        (ChromaDB persistent_directory)
│   ├── uploads\
│   ├── archive\
│   ├── thumbnails\
│   ├── watch\          (optional, Watch-Ordner)
│   └── backups\
├── logs\
│   ├── backend.log
│   └── service.log
└── config.toml                          (siehe 3.2)
```

### 3.2 `config.toml` ersetzt `.env`

```toml
# config.toml — Zettelwirtschaft Konfiguration
# Wird vom Setup-Wizard erzeugt, vom Backend gelesen.
# Aenderungen wirken nach Service-Neustart.

[server]
host = "0.0.0.0"        # 0.0.0.0 fuer LAN-Zugriff, 127.0.0.1 nur lokal
port = 8080

[paths]
data_dir = "C:/Users/manue/Documents/Zettelwirtschaft/data"
log_dir = "C:/Users/manue/Documents/Zettelwirtschaft/logs"
# Default-Subordner unter data_dir: uploads, archive, thumbnails, chromadb, watch
# Override moeglich:
# archive_dir = "D:/Familienarchiv"        ← externer Pfad
# export_dir = "Z:/Steuerberater/Eingang"  ← Netzlaufwerk

[auth]
pin_enabled = true
pin_code = "<vom Installer generiert oder gesetzt>"
session_timeout_minutes = 1440

[ollama]
base_url = "http://localhost:11434"      # Native-Default
model = "qwen2.5:7b-instruct"
timeout_seconds = 300
max_retries = 2

[embedding]
model = "bge-m3"

[email]
encryption_key = "<vom Installer kryptografisch generiert>"

[rag]
chunk_size = 800
chunk_overlap = 150
top_k = 5
use_reranker = false

[llm]
use_verifier = false
verifier_threshold = 0.5
```

`Settings` wird auf `pydantic-settings` mit `TomlConfigSettingsSource` umgestellt
(bereits in pydantic-settings v2.4+ enthalten). ENV-Vars bleiben als Override.

### 3.3 Datenordner-Wahl im Wizard

Vorteile fuer den User:
- **NAS-Mount**: `Z:\Familie\Dokumente` direkt als Archive-Pfad — Backup im NAS-Snapshot enthalten
- **Externe SSD**: schnelle Archivierung getrennt vom Systemlaufwerk
- **Cloud-Sync** (OneDrive/Dropbox/Nextcloud): Datenordner darin, automatischer Cloud-Backup
- **Wechsel** ohne Neuinstallation: Daten verschieben, Pfad in `config.toml` aendern, Service neu starten

Setup-Wizard validiert:
- Pfad ist schreibbar
- Genug freier Speicher (mindestens 5 GB empfohlen)
- Wenn Pfad nicht leer: Migration vorhanden? Bestaetigung fragen.

---

## 4. Verteilungs-Mechanik

### 4.1 Bundling-Stack

| Schicht | Tool | Output |
|---|---|---|
| Backend-Python-Bundle | **PyInstaller 6.x** (onedir mode) | `backend/zettelwirtschaft-backend.exe` mit `_internal\python313.dll` etc. |
| Frontend | `npm run build` | `frontend/dist/` (statische Files) |
| OCR | Tesseract 5.x w64-Installer | extrahiert in `bin/` |
| poppler | poppler-windows-Release | extrahiert in `bin/poppler/` |
| Ollama | `OllamaSetup.exe` ~600 MB | als Sub-Installer aufgerufen (Silent oder UI) |
| Service-Wrapper | **NSSM 2.24+** | `nssm.exe` (single file, ~600 KB) |
| Installer | **NSIS 3.x** (vorhanden!) | `Zettelwirtschaft-Setup-1.3.0.exe` |

**Optional MSI** statt NSIS via **WiX**: hoeherer Aufwand, dafuer GPO-faehig und
nativ in Windows-Update integrierbar. Fuer Heim-Setup ist NSIS klar besser
(deutsche UI, weniger Komplexitaet).

### 4.2 Installer-Groessen-Abschaetzung

| Inhalt | Groesse |
|---|---|
| PyInstaller-Backend-Bundle (Python + Libs) | ~ 80-120 MB |
| Frontend-Build (gzipped) | ~ 2-3 MB |
| Tesseract + Sprachpakete (deu+eng) | ~ 35 MB |
| poppler-windows | ~ 25 MB |
| NSSM + Skripte | ~ 1 MB |
| Ollama-Setup (separates Download oder included) | ~ 600 MB (oder als optionaler Download) |
| **Total ohne Ollama** | ~ 150 MB |
| **Total mit Ollama** | ~ 750 MB |
| LLM-Modell (qwen2.5:7b-instruct GGUF) | ~ 4.5 GB — wird zur Laufzeit gepullt, nicht gebundelt |

**Empfehlung:** Setup.exe ohne Ollama (~ 150 MB) + Ollama als optionaler Download
im Wizard (mit Fortschrittsanzeige). Modell wird beim ersten Start vom Service
gepullt.

### 4.3 Code-Signing

Pflicht: Authenticode-Signatur fuer Setup.exe + Service-Binary. Sonst SmartScreen
und Antivirus-Falschpositive bei PyInstaller-Output.

Optionen:
- **EV-Cert** (~ 300 €/Jahr): sofortige Reputation, USB-Token
- **OV-Cert** (~ 100 €/Jahr): braucht ein paar Wochen Reputation aufzubauen
- **Self-Sign + Doku** (gratis): User muss bei "Diesen Herausgeber zulassen" haendisch klicken
  - Tradeoff: gut fuer Open-Source / Beta, fuer 1.0-Release zu rau

---

## 5. Service-Lifecycle

### 5.1 Backend als Windows-Service via NSSM

```bash
nssm install ZettelwirtschaftBackend "C:\Programme\Zettelwirtschaft\backend\zettelwirtschaft-backend.exe"
nssm set ZettelwirtschaftBackend AppDirectory "C:\Programme\Zettelwirtschaft\backend"
nssm set ZettelwirtschaftBackend AppEnvironmentExtra ZETTELWIRTSCHAFT_CONFIG=C:\Users\manue\Documents\Zettelwirtschaft\config.toml
nssm set ZettelwirtschaftBackend AppStdout C:\Users\manue\Documents\Zettelwirtschaft\logs\backend.log
nssm set ZettelwirtschaftBackend AppStderr C:\Users\manue\Documents\Zettelwirtschaft\logs\backend.log
nssm set ZettelwirtschaftBackend AppRotateFiles 1
nssm set ZettelwirtschaftBackend AppRotateBytes 10485760
nssm set ZettelwirtschaftBackend Start SERVICE_AUTO_START
nssm set ZettelwirtschaftBackend Description "Zettelwirtschaft Dokumentenmanagement"
```

### 5.2 Ollama-Dependency

Der native Ollama-Installer registriert seinen Service selbst (`Ollama`). Wir
setzen `ZettelwirtschaftBackend` als abhaengig:

```bash
nssm set ZettelwirtschaftBackend DependOnService Ollama
```

Damit startet Ollama zuerst, dann unser Backend.

### 5.3 Tray-Icon (Optional, fuer 1.1)

Eine schmale `tray.exe` (per `pystray` oder C# Windows-Forms) im User-Autostart:
- Zeigt Status (gruen/gelb/rot) per Health-API-Polling
- Menue: "Open Browser", "Show Logs", "Stop Service", "Restart Service", "Settings"
- Notifications bei Fehlern

Fuer Phase 1 nicht zwingend — Startmenue-Eintrag `Zettelwirtschaft oeffnen.lnk`
genuegt erstmal.

---

## 6. Code-Aenderungen am Backend

### 6.1 ChromaDB Embedded
**Geaendert:** `backend/app/services/vectorize_service.py`

```python
import chromadb

def _get_chroma_client(settings: Settings):
    # Native: PersistentClient mit lokalem Pfad statt HttpClient
    return chromadb.PersistentClient(path=str(Path(settings.DATA_DIR) / "chromadb"))
```

Settings bekommt `CHROMADB_MODE: Literal["embedded", "http"]` als Schalter, default `embedded`. Docker-Pfad bleibt als `http` erhalten — saubere Migration.

`_check_chromadb_reachable_async` wird im embedded-Mode obsolet (immer True).

### 6.2 Frontend-Static-Serving im Backend
**Geaendert:** `backend/app/main.py`

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Native: Frontend liegt als ../frontend/dist relativ zum Backend-EXE
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
```

Die `/` muss am Schluss gemountet werden, NACH allen API-Routen.

### 6.3 Settings auf TOML umstellen
**Geaendert:** `backend/app/config.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=os.environ.get("ZETTELWIRTSCHAFT_CONFIG", "config.toml"),
    )
    @classmethod
    def settings_customise_sources(cls, ...):
        return (init_settings, TomlConfigSettingsSource(settings_cls), env_settings, ...)
    ...
```

`.env`-Fallback bleibt fuer Dev-Setup und Tests.

### 6.4 PyInstaller-Hooks

Heikle Stellen die manuell gehookt werden:
- `chromadb.utils.embedding_functions` — nutzt Lazy-Imports
- `alembic` — braucht Versions-Files als Data-Files (nicht in Python-Bundle automatisch)
- `httpx` certifi-Bundle
- `pdf2image` — `poppler_path` muss explizit auf bundled poppler zeigen
- `pytesseract` — `tesseract_cmd` explizit auf bundled `tesseract.exe`

`backend/zettelwirtschaft.spec`:
```python
a = Analysis(
    ['app/main.py'],
    datas=[
        ('app/prompts/*.txt', 'app/prompts'),
        ('alembic/versions/*.py', 'alembic/versions'),
        ('alembic.ini', '.'),
    ],
    hiddenimports=['chromadb.utils.embedding_functions', 'aiosqlite', ...],
    ...
)
```

### 6.5 Tesseract/poppler Pfade

```python
# bin_paths.py
import os, sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    BIN_DIR = Path(sys._MEIPASS).parent / "bin"
else:
    BIN_DIR = Path(__file__).parent.parent / "bin"

if BIN_DIR.exists():
    os.environ["PATH"] = f"{BIN_DIR};{BIN_DIR / 'poppler' / 'Library' / 'bin'};{os.environ['PATH']}"
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = str(BIN_DIR / "tesseract.exe")
```

Wird ganz frueh im `main.py` importiert.

---

## 7. Migration von Docker-Installationen

Bestandskunden haben:
- Docker-Volumes mit Daten (`./data/`)
- ChromaDB-Volume (`zettelwirtschaft_chromadb-data`)
- `.env` mit Settings
- LLM-Modell in Ollama-Volume

### 7.1 Migrations-Strategie

**Schritt 1: Erkennung im Setup-Wizard**

```powershell
$dockerVolumesExist = (docker volume ls --format "{{.Name}}" 2>$null) -match "zettelwirtschaft"
$legacyDataDir = Test-Path "$env:USERPROFILE\zettelwirtschaft\data"
```

Wenn erkannt → Wizard zeigt Migrations-Schritt.

**Schritt 2: Daten extrahieren**

```bash
# DB + Archiv + Uploads (data/ Volume ist nur bind-mount)
robocopy "<alte-installation>\data" "<neuer-datenordner>\data" /E

# ChromaDB-Volume extrahieren
docker run --rm -v zettelwirtschaft_chromadb-data:/src -v "<neuer-datenordner>\data\chromadb":/dst alpine cp -r /src/. /dst/

# Ollama-Modelle bleiben, weil neuer nativer Ollama-Service sie nicht erbt.
# Workaround: ollama pull <modell> beim neuen Service als Background-Task.
```

**Schritt 3: `.env` → `config.toml`-Konversion**

Ein PowerShell-Skript `Convert-Env-To-Config.ps1` liest die `.env` zeilenweise und schreibt die entsprechenden TOML-Abschnitte. Spezialfall: `FRONTEND_PORT` (env) → `server.port` (toml), `PIN_CODE` → `auth.pin_code` etc.

**Schritt 4: Alte Installation stoppen**

```bash
docker compose down --remove-orphans
# Volumes NICHT loeschen (Sicherheit, User kann manuell aufraeumen)
```

**Schritt 5: ChromaDB-Format-Check**

ChromaDB hat zwischen 0.6 und 1.x sein Volume-Format geaendert. Migrationen sind nicht-trivial. Pragmatischer Pfad: **Re-Indexierung** beim ersten Start des nativen Backends. Dauert bei 1000 Dokumenten ~ 10-15 Min, ist aber sauber.

Wizard fragt: "ChromaDB-Volume migrieren? (Re-Index dauert ~10 Min, ist sicherer) [Empfohlen: Re-Index]".

### 7.2 SQLite-Schema-Konsistenz

Da das Backend identisch bleibt, gilt: `migrate.py` + Alembic 001..013 sind dieselben. Beim ersten Start mit der migrierten DB laeuft `alembic upgrade head` durch — keine Schema-Aenderungen, weil bereits aktuellster Stand.

Risiko: wenn der User Native ohne Update startet, dann zurueck zu Docker geht → Inkompatibilitaeten unwahrscheinlich (DB-Schema ist Cross-Plattform), aber **dokumentiert als "Einbahnstrasse" empfehlen**.

### 7.3 Rollback-Plan

Wenn die Migration im Wizard fehlschlaegt:
- Daten bleiben in alter Docker-Installation
- Native-Installation kann komplett deinstalliert werden (Programme & Features)
- Setup-Wizard schreibt **vor** Migration ein Backup nach `<datenordner>\data\backups\pre-migration_<timestamp>\`

---

## 8. Netzwerk-Zugriff aus dem LAN

Heute: nginx-Container exposed Port 8080.
Native: Backend selbst bindet auf `0.0.0.0:<port>`.

### 8.1 Windows-Firewall

Setup-Wizard fuegt automatisch eine Inbound-Rule hinzu:
```powershell
New-NetFirewallRule -DisplayName "Zettelwirtschaft" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
```

`-Profile Private` ist absichtlich gewaehlt — der Service ist NICHT im
oeffentlichen WLAN/Public-Profile erreichbar. User muss WLAN explizit als
"privat" eingestuft haben.

### 8.2 Hostname-Resolution

LAN-Clients erreichen das System ueber:
- IP: `http://192.168.1.42:8080`
- Hostname: `http://<rechnername>:8080` (Windows-Computer-Name, ueber NetBIOS/mDNS)

Fuer mDNS-Discovery in Smartphones (iOS/Android nutzen Bonjour): Bonjour-Service
ist auf Windows nicht von Haus aus aktiv. Empfehlung: User-Doku statt
gebundelte Bonjour-Installation (Apple-Lizenz-Frage).

### 8.3 TLS (optional, Phase 2)

Default: HTTP. Fuer User die TLS wollen:
- `mkcert`-Doku im Setup-Hinweis
- Oder: Caddy als optionaler Proxy (aber das macht den Wizard komplexer)

Native-Phase-1: bewusst HTTP-only. TLS ist Tutorial im Wiki.

---

## 9. Updates

### 9.1 Update-Strategie

Heute: `update.bat` macht `docker compose pull` + restart.
Native:

**Option A — In-App-Update:**
- Backend prueft GitHub-Release-Tag gegen lokale `VERSION`
- Settings-View zeigt "Update verfuegbar"-Banner
- Klick startet Updater-Process der das Service stoppt, ueberschreibt, Service startet

**Option B — Setup-EXE-Lauf:**
- User laedt neue Setup.exe runter
- Setup erkennt vorhandene Installation, macht "Upgrade" statt "Neuinstallation"
- Service stoppen → Files ueberschreiben → DB-Migrationen → Service starten

**Option C — Winget:**
- Veroeffentlichen in Windows Package Manager: `winget install Zettelwirtschaft`
- User updated mit `winget upgrade`

**Empfehlung Phase 1: Option B.** Option A spaeter (mehr UX, mehr Komplexitaet). Option C als Bonus wenn Winget-Submission gemacht ist.

### 9.2 Migrationen bei Update

`migrate.py` + Alembic-Chain laeuft beim Service-Start automatisch (analog zu Docker). Pre-Update-Backup analog `update.bat`:
- DB nach `<datenordner>\data\backups\pre-update_<timestamp>\`
- ChromaDB nach gleichem Ordner (rsync der `chromadb/`-Files)

---

## 10. Aenderungen die Phase 1-6 obsolet machen

Diese gefixten Findings werden in der Native-Welt redundant oder anders:

| Finding | Status bei Native |
|---|---|
| F-04 ChromaDB-Auth (internal-net) | **Obsolet** — embedded, kein HTTP-Service |
| F-02 update.bat Port-Fix | **Obsolet** — kein Docker-Update mehr |
| B4 ChromaDB-Network-Isolation | **Obsolet** — embedded |
| F-12 nginx X-Real-IP | **Obsolet** — kein nginx mehr |
| M-1 CORS-Trade-off | **Neu zu bewerten** — Frontend kommt vom gleichen Origin (Backend selbst), CORS-Bedarf entfaellt |
| install.bat/install-gui.ps1 (Docker-spezifisch) | **Wird ersetzt** durch NSIS-Wizard |
| docker-compose.yml | **Bleibt** als Dev-Setup-Option, nicht mehr als Distributions-Pfad |

**Bleibt voll relevant:**
- Alle Backend-Code-Findings (R-01 PDF-Bug, K-2 PRAGMA FK, K-3 Decimal-Serializer, B3 Archive-Rollback, B5 Worker-Heartbeat, B6 Prompt-Injection, N-01 LLM-Singleton, alle Tests)
- Alle Frontend-Code-Findings (#1 Composable-Race, a11y, #2 EmailSettings-Mount)
- Alle Migration 012-013-Aenderungen (Schema-Level)
- Alle Pydantic-Schema-Aenderungen (Decimal-Serializer)

Sprich: **die ganzen 6 Phasen Code-Review-Fixes sind nicht weggeworfen.**
Nur die Distributions-Schicht (`install-gui.ps1`, `docker-compose.yml`,
`nginx.conf`) wird ersetzt.

---

## 11. Phasen-Plan fuer Native

### Phase 1 — Foundation (~ 2-3 Tage)
1. PyInstaller-Spec schreiben, Backend-Bundle bauen (cli `zettelwirtschaft-backend.exe --config <path>`)
2. ChromaDB-Embedded-Mode in `vectorize_service` (Config-Schalter)
3. Frontend `StaticFiles`-Mount in `main.py`
4. Settings auf TOML umstellen (env-Override beibehalten)
5. `bin_paths.py` fuer Tesseract+poppler
6. Lokaler Test: `dist/backend/zettelwirtschaft-backend.exe` laeuft + browser oeffnet auf localhost

### Phase 2 — Service + Installer (~ 2-3 Tage)
1. NSSM-basiertes Service-Install-Skript
2. NSIS-Installer ueberarbeiten:
   - Docker-Welcome-Page raus
   - Datenordner-Wahl-Page rein
   - Tesseract+poppler Bundle einbinden
   - Service-Install/Uninstall im Section-Block
3. Windows-Firewall-Rule
4. Startmenue-Eintraege (Open, Logs, Settings)

### Phase 3 — Migration + Ollama (~ 2 Tage)
1. Docker-Erkennung im Wizard
2. Migration-PowerShell (Daten + .env → config.toml)
3. ChromaDB-Re-Index als Strategie
4. Ollama-Sub-Installer (optional Download)
5. LLM-Modell-Pull im Setup oder bei erstem Start

### Phase 4 — Polish (~ 2 Tage)
1. Tray-Icon (optional)
2. Auto-Update-Check
3. Code-Signing
4. Test auf Win10 + Win11 + Win Server 2022
5. Doku im README

**Total: ~ 8-10 Tage** netto.

---

## 12. Risiken & offene Fragen

### Risiken

| Risiko | Severity | Mitigation |
|---|---|---|
| PyInstaller Antivirus-False-Positives | Hoch | Code-Signing + EICAR-Test mit Defender, Avast, Kaspersky vor Release |
| Tesseract-Lizenz (Apache 2.0) im Bundle | Niedrig | LICENSE-Datei mitliefern, Hinweis in About-Dialog |
| ChromaDB-Embedded-Performance bei 100k+ Dokumenten | Mittel | HNSW-Indexierung pruefen, ggf. Fallback auf HTTP-Mode dokumentieren |
| Ollama-Dependency-Groesse (600 MB Installer + 4.5 GB Modell) | Mittel | Optional-Download im Wizard, klare Erwartung kommunizieren |
| Service laeuft als LocalSystem ohne User-Netzlaufwerke | Mittel | NSSM kann ServiceAccount setzen — Wizard erlaubt User-Account-Option fuer Netzlaufwerke |
| Update-Pfad: Service-Stop bei laufender Verarbeitung | Niedrig | Queue-Recovery (B5) faengt das ab, processing_started_at-Heartbeat ist Schluessel |
| Datei-Locks beim Update (Service running) | Niedrig | Updater stoppt Service zuerst, wartet 30s, kann notfalls killen |

### Offene Fragen (Entscheidungsbedarf)

1. **Code-Signing-Cert?** Self-Sign (gratis, ueble UX) vs. OV-Cert (~100 €/Jahr) vs. EV-Cert (~300 €/Jahr)
2. **Ollama bundled oder Optional-Download?** 600 MB Setup-Groesse — fuer 1.0-Release sinnvoll als Optional-Download mit Fortschrittsanzeige.
3. **GPU-Support?** Ollama detected GPU automatisch. Wizard koennte Hardware-Check anzeigen (CUDA available? -> qwen2.5:7b OK, sonst llama3.2 empfehlen).
4. **Tray-Icon in Phase 1 oder spaeter?** Phase 2 reicht — Startmenue-Eintrag ist Mindeststandard.
5. **MSI statt NSIS fuer Enterprise/GPO-Faelle?** Aktuell aus Komplexitaetsgruenden Nein. Spaeter machbar wenn jemand das nachfragt.
6. **Auto-Update aktivieren by Default?** Privacy-Frage. Mein Vorschlag: Check by default, Download nur auf Klick.

---

## 13. Anhang: Beispiel-Wizard-Flow

```
┌──────────────────────────────────────────────────────┐
│ Zettelwirtschaft 1.3.0 - Setup                       │
├──────────────────────────────────────────────────────┤
│                                                      │
│   [1/6] Willkommen                                   │
│   [2/6] Lizenz                                       │
│   [3/6] Programmordner   C:\Programme\Zettelwirt...  │
│   [4/6] Datenordner      C:\Users\.\Documents\Z...   │
│         [√] Vorhandene Docker-Installation migrieren │
│   [5/6] Konfiguration                                │
│         Port:           [8080      ]                 │
│         PIN-Schutz:     [√] Aktiviert                │
│         Auto-Gen-PIN:   [829134] (notieren!)         │
│         LLM-Modell:     [qwen2.5:7b-instruct  ▼]     │
│         Watch-Ordner:   [_____________________] [..] │
│         Aus Netzwerk erreichbar: [√]                 │
│   [6/6] Installation                                 │
│         [████████████░░░░░] 73%                      │
│         > Tesseract installiert                      │
│         > Backend-Service registriert                │
│         > Datenbank-Migration laeuft (10/13)         │
│         > Ollama-Modell laden (1.2 GB / 4.5 GB)      │
│                                                      │
│              [< Zurueck] [Weiter >] [Abbrechen]      │
└──────────────────────────────────────────────────────┘
```

Nach Abschluss: Dialog "Fertig" mit Buttons:
- "Browser oeffnen" (`http://localhost:8080`)
- "Logs ansehen"
- "Schliessen"

---

**Naechster Schritt:** Sign-off vom User auf das Konzept, dann **Phase 1 Foundation**
starten — PyInstaller-Spec + ChromaDB-Embedded + StaticFiles-Mount + TOML-Settings
als POC, damit die kritischen Annahmen (PyInstaller mit chromadb+alembic, ChromaDB-
embedded Performance) validiert sind, bevor der Installer drumherum gebaut wird.
