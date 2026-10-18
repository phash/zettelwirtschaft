# 13 — Netzwerkordner (UNC + Zugangsdaten) und Scanner-Freigabe

Stand: 2026-10-18 · Branch `feat/netzwerkordner` · Basis v1.6.2

## 1. Ausgangslage und Entscheidung

Benutzer wählen Watch- und Export-Ordner selbst. Lokale Ordner bleiben wie bisher
erlaubt, auch ein **OneDrive-Ordner im Profil des installierenden Benutzers**
(z.B. Export nach `C:\Users\<name>\OneDrive\Belege` als externe Sicherung). Dafür ist
nichts Neues nötig. Hinweis in der Hilfe: beim Watch-Ordner in OneDrive „Immer auf
diesem Gerät behalten“ wählen, weil der Dienst (LocalSystem) reine Cloud-Platzhalter
nicht herunterladen kann.

Problem sind **Netzwerkziele**:

| Fall | Verhalten unter dem NSSM-Dienst (LocalSystem) |
|---|---|
| Netzlaufwerk `V:` | gilt nur für die Anmeldesitzung des Benutzers, für den Dienst nicht vorhanden |
| UNC `\\nas\scans` | meldet sich mit dem Computerkonto an, das die Heim-NAS meist ablehnt; außerdem NTLM-Relay-Risiko (FH-01) |
| SMB-Änderungsbenachrichtigung | über das Netz unzuverlässig, Ereignisse fehlen ohne Fehlermeldung |

Seit 1.6.0 werden Netzwerkpfade ohne `ALLOW_NETWORK_FOLDERS=true` abgelehnt
(FH-01 / INJ-03 / INFRA-02).

**Entscheidung des Eigentümers: beide Wege.**

- **(A) UNC + Zugangsdaten:** Der Benutzer trägt `\\server\freigabe\unterordner` sowie
  Benutzer und Passwort der Freigabe ein. Das Backend verbindet sich selbst
  (`WNetAddConnection2W`, ohne Laufwerksbuchstaben) und überwacht den Ordner per
  Polling.
- **(B) Lokale Scanner-Freigabe:** Der lokale Eingangsordner wird optional im Netzwerk
  freigegeben, damit ein Netzwerk-Scanner direkt hineinschreibt. Der Ordner bleibt
  lokal, der Dienst braucht keine Netzwerkanmeldung, und lokale Änderungs-Ereignisse
  sind zuverlässig. Für die meisten Haushalte ist **(B) der empfohlene Weg**.

Nur Native/Dev unter Windows. **Docker: keine Verhaltensänderung**, nur ein Hinweis
(Abschnitt 9).

## 2. Daten- und Einstellungsmodell

Es gibt **kein neues Schema und keine Migration**. Alles liegt im Key-Value-Speicher
`SystemSetting` (`settings_service.get_db_setting/set_db_setting`).

| Key | Inhalt | Default |
|---|---|---|
| `watch_dir` | wie bisher, darf jetzt `\\host\share\...` sein | `WATCH_DIR` |
| `export_dir` | wie bisher, darf jetzt UNC sein | `EXPORT_DIR` |
| `watch_dir_net_user` | Benutzer der Freigabe (`user`, `NAS\user`, `user@domain`) | `""` |
| `watch_dir_net_password` | **Fernet-Token** (nie Klartext) | `""` |
| `export_dir_net_user` / `export_dir_net_password` | dasselbe für den Export | `""` |
| `watch_poll_interval` | Sekunden, gilt nur für Netzwerk-Watch-Ordner, 10–600 | `30` |

- Für die Verschlüsselung wird `crypto_service.encrypt_password/decrypt_password` mit
  `EMAIL_ENCRYPTION_KEY` genutzt. Den Schlüssel erzeugt der Installer schon heute. Ist
  er leer, antwortet die API mit HTTP 503 und derselben Meldung wie bei E-Mail-Konten.
  Der Docstring von `crypto_service` wird verallgemeinert auf „Zugangsdaten“. Den
  Schlüssel umzubenennen ist **ausdrücklich nicht** Teil dieses Features
  (Abwärtskompatibilität von `config.toml`). Das wird als Follow-up notiert.
- Zugangsdaten gehören zum **Ordner**, nicht zum Server. Liegen Watch- und
  Export-Ordner auf demselben Host, müssen sie denselben Benutzer verwenden (SMB
  erlaubt pro Anmeldesitzung nur einen Satz Zugangsdaten je Server, Fehler 1219).
  Das wird beim Speichern geprüft (Abschnitt 7).
- Neue Settings in `config.py`, beide Experten-Overrides:
  - `WATCH_POLL_INTERVAL: int = 30` als Fallback, wenn die DB keinen Wert hat.
  - `WATCH_FORCE_POLLING: bool = False`, opt-in, z.B. für Docker mit CIFS-Volume.
    Der Default ändert nichts.
- `ALLOW_NETWORK_FOLDERS` bleibt als Experten-Override erhalten. Ist er gesetzt, ist
  UNC **ohne** Zugangsdaten wie bisher erlaubt (dann meldet sich das Computerkonto
  an). Für den normalen Ablauf wird er nicht mehr gebraucht.

## 3. API-Änderungen (`api/system.py`)

### `FolderSettings` / `FolderSettingsResponse`

```python
class FolderSettings(BaseModel):
    watch_dir: str
    export_dir: str
    watch_dir_host: str = ""
    export_dir_host: str = ""
    restart_required: bool = False
    watch_dir_net_user: str = ""
    watch_dir_net_password: str | None = None   # write-only: None = unverändert, "" = löschen
    export_dir_net_user: str = ""
    export_dir_net_password: str | None = None
    watch_poll_interval: int = Field(30, ge=10, le=600)

class FolderSettingsResponse(FolderSettings):   # Passwortfelder werden NIE befüllt
    deployment_mode: str
    watch_dir_net_password_set: bool = False
    export_dir_net_password_set: bool = False
    network_folders_supported: bool             # native/dev und sys.platform == "win32"
    scanner_share: ScannerShareStatus | None    # (B), nur native/dev unter Windows
```

`FolderSettingsResponse` überschreibt `*_net_password` mit `Field(None, exclude=True)`,
damit sie nicht serialisiert werden. Ein Test prüft, dass die Antwort-JSON keinen Key
mit `password` außer `*_password_set` enthält.

### `PUT /api/system/settings`

- Wie bisher gilt: erst alles validieren, dann speichern.
- Neu im Native/Dev-Zweig: Ist der Pfad UNC, läuft Abschnitt 7 (Validierung). Danach
  wird **mit den angegebenen Zugangsdaten verbunden**
  (`network_share.connect_and_probe`). Schlägt das fehl, gibt es HTTP 400 mit der
  deutschen Fehlermeldung, und nichts wird gespeichert.
- Das `mkdir` (heute für alle Ordner) läuft bei UNC erst nach erfolgreichem Verbinden.
- Der Watch-Task wird neu gestartet, wenn Pfad, Benutzer, Passwort oder Intervall sich
  ändern (bisher nur beim Pfad).
- Ändert sich der Export-Pfad, wird die alte Export-Verbindung getrennt (Abschnitt 4,
  Connection-Manager).

### Neu: `POST /api/system/settings/test-folder`

Request: `{target: "watch"|"export", path, net_user?, net_password?}`. Fehlt
`net_password`, wird das gespeicherte Passwort verwendet, falls Pfad und Benutzer dazu
passen.

Antwort:

```json
{
  "ok": true,
  "kind": "local|unc|network_drive|subst|missing_drive",
  "message": "…",
  "suggested_paths": ["\\\\nas\\scans\\eingang"]
}
```

- Ablauf: verbinden, Verzeichnis lesen, testweise eine Datei anlegen und löschen
  (`.zw-test-<uuid>`, nötig für `verarbeitet\` und `rejected\`), danach wieder trennen,
  falls die Verbindung nur für den Test aufgebaut wurde.
- Für Laufwerksbuchstaben läuft nur die Laufwerksauflösung (Abschnitt 5), ohne
  Netzwerkzugriff.
- Rate-Limit: eigenes, engeres Limit in `app/core/rate_limit.py` (z.B. 10/min pro
  Client, zusätzlich zum globalen 200/min; heute gibt es nur das globale), damit das
  Endpoint nicht zum Passwortraten gegen eine NAS taugt.
- Timeout insgesamt 30 s (`asyncio.wait_for` um `to_thread`).

### `GET /api/system/health`

Neue Komponente `watch_folder` (Abschnitt 6), optional auch `export_folder`.

## 4. Windows-Verbindungsmodul `app/core/network_share.py`

Plattformneutral importierbar. Unter Nicht-Windows wirft `connect()` die Ausnahme
`NetworkShareUnsupported`. Alle Aufrufe blockieren und laufen deshalb in
`asyncio.to_thread`.

### ctypes-Signaturen

```python
from ctypes import wintypes as w
mpr = ctypes.WinDLL("mpr.dll", use_last_error=True)
k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

class NETRESOURCEW(ctypes.Structure):
    _fields_ = [("dwScope", w.DWORD), ("dwType", w.DWORD), ("dwDisplayType", w.DWORD),
                ("dwUsage", w.DWORD), ("lpLocalName", w.LPWSTR), ("lpRemoteName", w.LPWSTR),
                ("lpComment", w.LPWSTR), ("lpProvider", w.LPWSTR)]

WNetAddConnection2W    (POINTER(NETRESOURCEW), LPCWSTR pw, LPCWSTR user, DWORD flags) -> DWORD
WNetCancelConnection2W (LPCWSTR name, DWORD flags, BOOL force)                       -> DWORD
WNetGetConnectionW     (LPCWSTR local, LPWSTR remote, POINTER(DWORD) len)            -> DWORD  # Dev: V: -> UNC
WNetGetLastErrorW      (POINTER(DWORD) err, LPWSTR buf, DWORD n, LPWSTR prov, DWORD m) -> DWORD  # bei 1208
GetDriveTypeW          (LPCWSTR root)                                                -> UINT
QueryDosDeviceW        (LPCWSTR dev, LPWSTR buf, DWORD n)                            -> DWORD
```

- Aufruf: `dwType=RESOURCETYPE_DISK (1)`, `lpLocalName=None` (verbindet ohne
  Laufwerksbuchstaben), `lpRemoteName=r"\\host\share"` (**nur die Freigabe-Wurzel**,
  nicht der Unterordner), `flags=CONNECT_TEMPORARY (0x4)`.
- Die WNet-Funktionen geben den Fehlercode direkt zurück, nicht über
  `GetLastError`. Nur bei `ERROR_EXTENDED_ERROR` folgt `WNetGetLastErrorW`.
- Das Passwort wird nur als lokale Variable übergeben und nie in Ausnahme-Texten oder
  `repr` verwendet. `NetworkCredential.__repr__` maskiert es.
- Verbunden wird über die **geprüfte IPv4-Adresse** (`\\<ip>\share`, NET-SEC-08): Der
  Host wird je Verbindungsaufbau genau einmal aufgelöst und geprüft, und alle
  Dateizugriffe nutzen dieselbe Adresse (`ShareConnections.access_path`). Der
  Redirector kann den Namen so nicht erneut (und anders, DNS-Rebinding) auflösen.
  Nicht auflösbare Hosts werden abgelehnt (fail closed); Hosts ohne IPv4-Adresse
  ebenso. Eigene Namen/Adressen werden bei jeder Prüfung neu ermittelt (kein Cache,
  DHCP/VPN).
- `lpProvider` ist der SMB-Provider (`WNetGetProviderNameW(WNNC_NET_LANMAN)`), nie
  WebDAV (NET-SEC-10).
- Nach dem Verbinden prüft `NetShareGetInfo(\\<ip>, share, 1005)` über die schon
  bestehende SMB-Sitzung die DFS-Flags — DFS-Freigaben werden abgelehnt, ohne der
  Weiterleitung zu folgen (NET-SEC-09). Das läuft bei jedem (Wieder-)Verbinden.
- Trennen: `WNetCancelConnection2W(r"\\<ip>\share", 0, False)` (routinemäßig ohne
  `force`; nur beim Beenden mit `force`). Fehler 2250 (`ERROR_NOT_CONNECTED`) wird
  ignoriert.
- Nur vor dem **ersten** Verbinden eines Eintrags wird einmal getrennt (idempotent,
  Reste nach einem Absturz) — nie beim Wiederverbinden unter laufenden Zugriffen.

### Connection-Manager

`ShareConnections`, ein Singleton pro Prozess:

- Schlüssel ist `\\<ip>\share` (casefold): zwei Namen für denselben Server teilen
  sich die Verbindung, statt sie sich gegenseitig zu trennen.
- Referenzzähler für Watch und Export, `acquire(target, root, cred)` / `release(target)`.
- **Leases** (`open_lease`/`lease`) für jeden laufenden Zugriff: Jeder Thread, der den
  Pfad anfasst (Observer, Import, Export-Kopie, Probe), hält eine Lease. Getrennt wird
  erst, wenn das letzte Ziel freigegeben **und** die letzte Lease beendet ist —
  unabhängig davon, ob der wartende asyncio-Task abgebrochen wurde (NET-SEC-02/03, R6).
- `check_connected(target)` prüft per `NetUseEnum`, verbindet aber **nicht** neu: Fehlt
  der Eintrag, gilt die Verbindung als defekt, neue Leases werden abgelehnt, und
  `acquire` verbindet erst wieder, wenn alle Zugriffe beendet sind.
- Je Server (Adresse) höchstens ein Benutzer. Kommt ein abweichender Benutzer, wird
  ohne WNet-Aufruf `ShareError(1219)` geworfen.
- Die globale Sperre schützt nur die Buchhaltung; WNet-Aufrufe laufen unter einer
  Sperre je Freigabe (R7) — ein toter Host hält die anderen nicht auf.
- Beim Herunterfahren: erst alle Tasks (auch ein über die Einstellungen neu
  gestarteter Watch-Task) und Hintergrund-Vorgänge beenden, dann `shutdown()`: keine
  neuen Verbindungen mehr, bis zu 30 s auf laufende Leases warten, dann trennen
  (NET-SEC-04, R1).
- Der Export läuft **nach dem Commit** (`archive_service.export_copy`, R2): erst
  `acquire`, dann Kopie samt Aufräumen unter einer Lease. Nach einem
  Verbindungsfehler wird der Export mit Backoff (60 s … 15 min) übersprungen. Ein
  Fehler bleibt graceful (Warnung im Log, das Dokument ist archiviert). Das Ergebnis
  steht mit `checked_at` in der Health-Anzeige.

### Fehlercodes und deutsche Meldungen

Alle Meldungen beginnen mit „Netzwerkordner: “. Unbekannte Codes ergeben
„Verbindung fehlgeschlagen (Windows-Fehler N).“

| Code | Konstante | Meldung |
|---|---|---|
| 5 | ACCESS_DENIED | Zugriff verweigert – der Benutzer hat keine Rechte auf diese Freigabe. |
| 53 | BAD_NETPATH | Server nicht gefunden – Name/IP-Adresse prüfen, ist das Gerät eingeschaltet? |
| 67 | BAD_NET_NAME | Die Freigabe gibt es auf dem Server nicht – Schreibweise prüfen. |
| 64 | NETNAME_DELETED | Die Verbindung wurde unterbrochen. |
| 86 | INVALID_PASSWORD | Das Passwort ist falsch. |
| 121 / 1236 | SEM_TIMEOUT / CONNECTION_ABORTED | Zeitüberschreitung beim Verbinden. |
| 1203 | NO_NET_OR_BAD_PATH | Netzwerkpfad nicht erreichbar. |
| 1219 | SESSION_CREDENTIAL_CONFLICT | Zu diesem Server besteht schon eine Verbindung mit einem anderen Benutzer – für Watch- und Export-Ordner auf demselben Server denselben Benutzer verwenden. |
| 1222 | NO_NETWORK | Kein Netzwerk verfügbar. |
| 1244 | NOT_AUTHENTICATED | Nicht angemeldet. |
| 1326 | LOGON_FAILURE | Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch. |
| 1327 | ACCOUNT_RESTRICTION | Konto-Einschränkung (z.B. Konto ohne Passwort ist für Netzwerkzugriff gesperrt). |
| 1330 / 1331 / 1909 | PASSWORD_EXPIRED / ACCOUNT_DISABLED / ACCOUNT_LOCKED_OUT | Passwort abgelaufen / Konto deaktiviert / Konto gesperrt. |
| 2202 | BAD_USERNAME | Ungültiger Benutzername. |
| 1208 | EXTENDED_ERROR | Text des Providers aus `WNetGetLastErrorW` (gekürzt, ohne Zugangsdaten). |

## 5. Laufwerksbuchstaben

Neue Funktion `classify_drive(path) -> DriveInfo` in `network_share.py`. Sie macht keinen
SMB-Zugriff, sondern liest nur Kernel und Registry.

1. Gilt für `X:\...`. `GetDriveTypeW("X:\\")` liefert:
   - `DRIVE_FIXED (3)` / `DRIVE_REMOVABLE (2)`: lokal, weiter mit der bisherigen
     Validierung.
   - `DRIVE_REMOTE (4)`, nur im Dev-Betrieb sichtbar: `WNetGetConnectionW("X:")`
     liefert den UNC-Pfad als Vorschlag. Der Pfad wird abgelehnt.
   - `DRIVE_NO_ROOT_DIR (1)`, der typische Fall unter dem Dienst: Der Vorschlag kommt aus
     `HKEY_USERS\<SID>\Network\<X>\RemotePath` **nur des an der Konsole angemeldeten
     Benutzers** (`WTSGetActiveConsoleSessionId` → SID, SEC-NF-03). Die Netzlaufwerke
     anderer Windows-Konten werden nie angezeigt. Der Pfad wird abgelehnt.
   - `DRIVE_CDROM (5)` / `DRIVE_RAMDISK (6)`: abgelehnt („kein dauerhafter Ordner“).
2. **subst**: Liefert `QueryDosDeviceW("X:")` `\??\C:\…`, ist das ein lokales
   Ersatzlaufwerk. Der Pfad wird auf das echte Ziel umgeschrieben, dieses durchläuft
   die bisherige Validierung (kanonisch), und **gespeichert wird das echte Ziel**. Der
   Dienst sieht Ersatzlaufwerke aus der Benutzersitzung nicht. Ein subst, das der Dienst
   nicht sieht, fällt unter Fall `NO_ROOT_DIR` ohne Registry-Treffer.
3. Meldungen:
   - Netzlaufwerk: „Laufwerk V: ist ein Netzlaufwerk. Der Hintergrunddienst sieht
     verbundene Netzlaufwerke nicht – bitte den Netzwerkpfad (z.B. \\nas\scans) mit
     Benutzer und Passwort der Freigabe angeben.“ Gibt es einen Vorschlag, folgt:
     „Vorschlag: \\nas\scans\eingang“.
   - Kein Treffer: „Laufwerk X: ist für den Dienst nicht vorhanden (nur in Ihrer
     Anmeldung verbunden oder per subst angelegt) – bitte den echten Pfad angeben.“
4. Die Prüfung läuft in `_validate_local_dir` **vor** `_canonical()`. Die UI holt die
   Vorschläge über `test-folder` (`suggested_paths`) und bietet die Schaltfläche
   „Diesen Netzwerkpfad verwenden“ an.

## 6. Watch-Ordner: Polling, Wiederverbinden, Status

Umbau von `watch_folder_service.run_watch_folder`. Lokale und Netzwerkordner nutzen
dieselbe Schleife (`_watch_loop`, R9): Fällt der Observer aus (Polling nach einem
Lesefehler) oder ist der Ordner nicht verfügbar, meldet der Status das (`fehler` bzw.
`getrennt`) und die Überwachung startet mit Backoff neu.

- Stabilität (R5): Größe **und** Änderungszeit müssen über drei Abfragen gleich
  bleiben, und die Datei darf von keinem anderen Prozess mehr geöffnet sein
  (exklusives Öffnen). Wächst sie nach 10 min noch, bleibt sie liegen; Änderungs-
  ereignisse und ein Neu-Einlesen alle 5 min holen sie später. Das Einlesen beim
  Start läuft über denselben Handler (gleiche Prüfung, kein Doppelimport).
- Import (R3): Die Quelle wird einmal gelesen (Kopie nach `UPLOAD_DIR`, im Thread),
  validiert wird die Kopie. Ein Lesefehler der Quelle ist vorübergehend — die Datei
  bleibt liegen statt nach `rejected\`.
- Reihenfolge (R4): erst den Observer starten (Ausgangszustand), dann einlesen.
- Importe werden beim Trennen abgewartet, aber nicht abgebrochen (R6); jeder hält
  seine Lease.

- `resolve_watch_target()` liefert `WatchTarget(path, network: bool, credential | None,
  poll_interval)`.
- **Lokal:** wie heute `Observer()`, außer `WATCH_FORCE_POLLING` ist gesetzt. Dann
  `PollingObserver(timeout=interval)`.
- **Netzwerk:** Zustandsmaschine:
  1. `VERBINDE`: `ShareConnections.acquire`.
  2. `OK`: `_scan_existing_files` (holt Dateien, die während einer Unterbrechung
     angekommen sind), dann `PollingObserver(timeout=poll_interval)` mit
     `recursive=False`. Alle `poll_interval` Sekunden läuft eine Erreichbarkeitsprobe
     (`os.scandir` im Thread, eigener Timeout).
  3. `GETRENNT` bei einer Ausnahme in Probe oder Observer: Observer stoppen, Verbindung
     freigeben, Backoff 30 s, 60 s, 120 s, 240 s, … bis maximal 15 min, dann zurück zu 1.
     Ein Erfolg setzt den Backoff zurück.
  4. Abbruch (`CancelledError`): Observer stoppen, `release` im `finally`.
- Der Status liegt im Modul in `WatchStatus` (thread-sicher, nur lesbare Kopie nach
  außen):
  - `mode`: `lokal` | `netzwerk`
  - `state`: `ok` | `verbinde` | `getrennt` | `fehler`
  - `path`: Pfad, nie Zugangsdaten
  - `user`: nur der Benutzername
  - `reason`: deutscher Text aus Abschnitt 4
  - `last_ok_at`, `next_retry_at`, `poll_interval`
- Health-Komponente `watch_folder`:
  - `{status: "ok"|"warning", message, path, mode, user, last_ok_at, next_retry_at}`
  - `warning` bedeutet nicht erreichbar, mit Grund. Der Gesamtstatus wird höchstens
    `warning`.
  - Lokal fehlender Ordner ergibt ebenfalls `warning`.
- Log: Zustandswechsel auf INFO/WARNING mit Pfad und Code, **ohne Passwort**. Wiederholte
  gleiche Fehler werden nur beim Zustandswechsel und dann höchstens alle 15 min
  geloggt.

## 7. Sicherheitsregeln

Erweiterung von `_validate_local_dir`. Neu ist die Funktion
`_validate_network_dir(value, label, cred, settings)`.

1. Nach `_normalize_path_input` und `_reject_device_prefix` gilt weiterhin: `\\?\`,
   `\\.\`, `//?/`, `//./` werden abgelehnt, ebenso `..`-Segmente.
2. Die Form muss `\\host\share[\sub…]` sein (`//` wird zu `\\`), mit nicht leerem Host
   und nicht leerer Freigabe. **Host** ist ein Name nach dem Muster
   `[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*` oder eine IPv4-Adresse.
   - Abgelehnt werden `@` (WebDAV `\\host@SSL@443\…` würde den WebClient-Dienst mit
     HTTP-Authentifizierung auslösen), `DavWWWRoot`, IPv6-Literale und Ports.
3. Weiter gilt `_reject_local_or_admin_share`: localhost, `127.*`, eigener PC-Name und
   FQDN sowie eigene IPs werden abgelehnt, ebenso alle Freigaben, die auf `$` enden
   (`C$`, `ADMIN$`, `IPC$`, `print$`).
   - Neu: Der Host wird per `getaddrinfo` aufgelöst. Das ist DNS, keine
     SMB-Anmeldung. Ergibt sich eine Loopback-Adresse oder eine eigene Adresse, wird
     der Pfad abgelehnt (ein DNS-Alias wie `meinpc.fritz.box` zeigt auf diesen
     Rechner).
4. **Zugangsdaten sind Pflicht** (Benutzer und Passwort nicht leer). Ohne Zugangsdaten
   ist UNC nur mit `ALLOW_NETWORK_FOLDERS=true` erlaubt (bisheriges Verhalten). So
   meldet sich SYSTEM nie mit seiner eigenen Identität an einem beliebigen Host an:
   Die Zugangsdaten liefert der PIN-Inhaber selbst.
5. **Vor dem Verbinden gibt es keinen Dateisystemzugriff** auf den UNC-Pfad, also kein
   `resolve()`, `exists()` oder `mkdir()` — auch nicht beim Start (`WATCH_DIR`/
   `EXPORT_DIR` aus config.toml, NET-SEC-06) und nicht für die Statusanzeige der
   Scanner-Freigabe (NET-SEC-01). `_canonical()` verweigert UNC-Pfade ohne
   ausdrückliche Erlaubnis. DFS wird über die Freigabe-Flags erkannt (Abschnitt 4).
   Lokale Pfade werden **vor** `realpath` Komponente für Komponente auf
   Verknüpfungen zu Freigaben geprüft (`find_remote_link`, NET-SEC-07) — auch zur
   Laufzeit (Watch-Ordner jede Sekunde, Export vor jeder Kopie); Polling nutzt
   `lstat`.
6. Für den Export gelten dieselben Regeln wie für den Watch-Ordner. UNC ohne
   Zugangsdaten bleibt dort ebenfalls gesperrt.
7. Gleicher Host für Watch und Export erfordert denselben Benutzer (sonst Fehler 1219,
   Meldung aus Abschnitt 4).
8. Wohin das Passwort **nie** gelangt:
   - Logs, Health, API-Antworten (nur `*_password_set`), `test-folder`-Antworten und
     Task-Ergebnisse
   - Exception-Texte und `repr`
   - Backups im Klartext: Die DB-Backups enthalten wie bei E-Mail-Konten nur das
     Fernet-Token. Der Schlüssel liegt in `config.toml`/`.env` und ist nicht im Backup.
   - Ein Test prüft das mit einer Sentinel-Zeichenkette (`TEST_SMB_PASSWORD`) über Log
     (caplog), Health, GET/PUT und `test-folder`.
9. Rate-Limit für `test-folder` und für PUT mit Netzwerkpfad (Abschnitt 3) — im PUT
   **vor** jeder Prüfung. Die Prüfung (DNS, realpath, Registry) läuft im Thread mit
   Zeitlimit, nie im Event-Loop (SEC-NF-02).
10. Ein gespeichertes Passwort wird **nie** mit einem eingegebenen verglichen
    (SEC-NF-01): Ist für denselben Server eine Seite neu eingegeben und die andere
    gespeichert, gelten die eingegebenen Zugangsdaten für beide und werden an der NAS
    geprüft. `test-folder` mit eingegebenen Zugangsdaten für eine schon benutzte
    Freigabe vergleicht nichts („beim Speichern geprüft“).
11. Administrative Freigaben auch mit Punkt/Leerzeichen am Ende (`C$ `, `C$.`) und
    Segmente mit Punkt/Leerzeichen am Ende werden abgelehnt (NET-SEC-10).

## 8. (B) Scanner-Freigabe

### Skript `scripts/freigabe-einrichten.ps1` + `freigabe-einrichten.bat`

Das Skript braucht Admin-Rechte (die `.bat` fordert per UAC an, wie `update-wizard.bat`).
Es ist kompatibel mit Windows PowerShell 5.1, kommt ins Bundle (`build-native.ps1`) und
bekommt den Startmenü-Eintrag „Scanner-Freigabe einrichten“.

Parameter:

- `-WatchDir <pfad>`: ohne Angabe wird der Ordner per
  `zettelwirtschaft-backend.exe --config <cfg> --print-watch-dir` ermittelt.
  `--print-watch-dir` ist ein neues Offline-CLI-Flag in `entrypoint.py`, analog zu
  `--backup`. Es liest das DB-Setting und fällt auf `WATCH_DIR` zurück. Der Pfad zur
  Exe kommt aus `HKLM\SOFTWARE\Zettelwirtschaft`.
- `-Remove`: alles entfernen.
- `-NewPassword`: neues Passwort für den bestehenden Benutzer.
- `-ResultFile <ini>`: für NSIS.
- `-ShareName` (Default `Zettelwirtschaft-Eingang`) und `-UserName` (Default
  `zw-scanner`).

Schritte beim Einrichten (idempotent, jeder Schritt mit eigener Meldung):

1. Der Watch-Ordner muss lokal sein, auf einem festen Laufwerk liegen und existieren.
   Ein UNC-Pfad oder Netzlaufwerk führt zum Abbruch mit Hinweis.
2. Das Passwort erzeugt `new-secret.ps1 -Kind password` (neue Art): 16 Zeichen aus
   `A-Z a-z 2-9` ohne verwechselbare Zeichen. Viele Scanner erlauben keine
   Sonderzeichen und höchstens 32 Zeichen.
3. Benutzer anlegen:
   - `New-LocalUser zw-scanner -Password … -PasswordNeverExpires
     -UserMayNotChangePassword -AccountNeverExpires -Description "Zettelwirtschaft
     Netzwerk-Scanner"`. Das fügt den Benutzer keiner Gruppe hinzu.
   - Rechte `SeDenyInteractiveLogonRight` und `SeDenyRemoteInteractiveLogonRight`
     (LSA `LsaAddAccountRights` per `Add-Type`) vergeben.
   - Den Benutzer im Anmeldebildschirm ausblenden
     (`Winlogon\SpecialAccounts\UserList`).
   - Gibt es den Benutzer schon, wird nur das Passwort gesetzt, wenn `-NewPassword`
     angegeben ist.
4. NTFS (enger als ursprünglich geplant, Review-Befund 4):
   - Auf dem Watch-Ordner selbst bekommt `zw-scanner` nur `ListDirectory, CreateFiles,
     ReadAttributes, ReadExtendedAttributes, Traverse, Synchronize`, **ohne Vererbung**.
     Kein Unterordner-Anlegen (keine Junctions), kein Löschen oder Lesen fremder Dateien.
   - `CREATOR OWNER`: `Modify` mit `(OI)(IO)(NP)`, damit der Scanner seine eigene
     Temp-Datei umbenennen kann.
   - `verarbeitet\` und `rejected\`: explizites Deny für `zw-scanner`.
   - Gesetzt per .NET-ACL-Objekten (kein zusammengebauter `icacls`-String). Der
     Datenordner bleibt durch `secure-acl.ps1` geschützt.
   - Restrisiko: Eine Datei, die der Scanner selbst angelegt hat und die nach
     `rejected\` verschoben wurde, behält beim Verschieben ihre ACL. Der Scanner könnte
     sie bei bekanntem Namen weiter lesen (Bypass Traverse Checking). Im
     Standard-Eingangsordner werden importierte Dateien gelöscht, nicht verschoben.
5. Freigabe: `New-SmbShare -Name Zettelwirtschaft-Eingang -Path <watch> -ChangeAccess
   "$env:COMPUTERNAME\zw-scanner" -FolderEnumerationMode AccessBased`.
   - Nur dieses eine ACE, kein `Everyone`.
   - Keine `EncryptData` (ältere Scanner), **kein SMB1** aktivieren. Die Ausgabe weist
     darauf hin, dass Scanner mindestens SMB2 brauchen.
6. Firewall: eine eigene Regel `New-NetFirewallRule -DisplayName "Zettelwirtschaft
   Scanner-Freigabe (SMB)" -Group Zettelwirtschaft -Direction Inbound -Protocol TCP
   -LocalPort 445 -RemoteAddress LocalSubnet -Profile Private -Action Allow`. Die
   System-Gruppe „Datei- und Druckerfreigabe“ wird **nicht** verändert, damit die
   Deinstallation sauber bleibt.
7. Prüfungen mit Warnung, ohne etwas zu ändern: läuft der Dienst `LanmanServer`, und hat
   das aktive Netzwerkprofil `Private` (`Get-NetConnectionProfile`)?
8. Registry `HKLM\SOFTWARE\Zettelwirtschaft`: `ScannerShareName`, `ScannerShareUser`,
   `ScannerSharePath`. **Kein Passwort** in der Registry.
9. Ausgabe genau einmal:
   - In der Konsole steht „Im Scanner eintragen:“ mit `\\<PC>\Zettelwirtschaft-Eingang`,
     Benutzer `<PC>\zw-scanner` und Passwort, dazu der Hinweis „Das Passwort wird nicht
     gespeichert – bei Verlust mit -NewPassword neu setzen“.
   - Mit `-ResultFile` wird dasselbe als INI geschrieben (Abschnitt `[share]`: `unc`,
     `user`, `password`). Die Datei bekommt eine ACL nur für Administratoren und SYSTEM.

`-Remove` entfernt die Freigabe, den ACE, die Firewall-Regel, den Benutzer, die
LSA-Rechte (fallen mit dem Benutzer weg), den `SpecialAccounts`-Eintrag und die
Registry-Werte. Jeder Schritt ist tolerant gegenüber „nicht vorhanden“, Exit-Code 0.

### Setup (`setup-native.nsi`)

- `DataDirPage`, nur bei Erstinstallation: Checkbox „Eingangsordner im Netzwerk
  freigeben (für Netzwerk-Scanner)“, Default **aus**, Variable `$SCANNER_SHARE`. Für
  stille Installation gibt es zusätzlich den Parameter `/SCANNERSHARE=1`.
- `SecMain` nach `secure-acl.ps1`:
  `nsExec::ExecToStack '${POWERSHELL} -File "$INSTDIR\freigabe-einrichten.ps1"
  -WatchDir "$DATA_DIR\data\watch" -ResultFile "$PLUGINSDIR\share.ini"'`.
  - **ExecToStack statt ExecToLog**, damit das Passwort nicht im Installationsprotokoll
    landet.
  - Danach `ReadINIStr` und sofort `Delete`. Das Ergebnis kommt in `$FINISH_SHARE_TEXT`
    auf der Abschlussseite.
  - Ein Fehler stoppt die Installation nicht: Es erscheint eine MessageBox mit Verweis
    auf den Startmenü-Eintrag.
- Upgrade/Übernahme: Am Upgrade wird nichts geändert. Eine bestehende Freigabe bleibt.
  Neu sind der Startmenü-Eintrag sowie die Dateien im Bundle und in der Uninstall-Liste.
- Uninstall: vor `service-uninstall.bat` immer `freigabe-einrichten.ps1 -Remove`
  (idempotent). Danach werden die `.lnk`-Datei und die Skript-Dateien gelöscht.

### Backend-Anzeige

`scanner_share` in `FolderSettingsResponse`:

```json
{
  "configured": true,
  "exists": true,
  "unc_path": "\\\\PC\\Zettelwirtschaft-Eingang",
  "user": "PC\\zw-scanner",
  "path": "...",
  "matches_watch_dir": true
}
```

- Die Werte kommen aus den Registry-Einträgen, verifiziert per
  `NetShareGetInfo(None, name, 2)` (netapi32, ctypes) über `shi2_path`.
- `matches_watch_dir=false` erzeugt eine Warnung in der UI: „Die Freigabe zeigt auf
  einen anderen Ordner als den aktuellen Eingangsordner – Skript erneut ausführen.“
- Ein Passwort kommt nie vor.

## 9. Docker-Modus

Das Verhalten bleibt unverändert: Host-Pfade gehen wie bisher durch
`_validate_host_path` (UNC als Host-Pfad war und bleibt dort möglich, Docker bindet
es selbst ein). UNC als Watch-/Export-Ordner im Container wird abgelehnt, mit Hinweis
auf ein CIFS-Volume. Eigene SMB-Verbindungen gibt es im Container nicht.

`FolderSettings.vue` zeigt im Docker-Modus den aufklappbaren Hinweis „Netzwerkordner
(NAS) im Docker-Betrieb“ mit einem Beispiel für eine **eigene** Datei
`docker-compose.cifs.yml`. Die von `generate-mounts.ps1` erzeugte Override-Datei würde
überschrieben. Die Zugangsdaten stehen in einer `credentials=`-Datei auf dem Host
(Review-Befund 7), nicht inline:

```yaml
services:
  backend:
    volumes: ["scans:/app/external/watch"]
    environment: ["WATCH_FORCE_POLLING=true"]   # inotify sieht keine CIFS-Änderungen
volumes:
  scans:
    driver: local
    driver_opts: {type: cifs, device: "//nas/scans", o: "credentials=/root/.smb-scans,vers=3.0,uid=1000,gid=1000"}
```

## 10. Frontend

- `FolderSettings.vue`, Native/Dev:
  - Je Ordner zeigt ein UNC-Pfad (beginnt mit `\\`) die Felder „Benutzer der Freigabe“
    und „Passwort“ (Platzhalter „gespeichert – leer lassen für unverändert“, wenn
    `*_password_set`).
  - Schaltfläche „Verbindung testen“ (`test-folder`), zeigt Erfolg oder Grund an.
  - Beim Watch-Ordner zusätzlich „Prüfintervall (Sekunden)“.
  - Bei `suggested_paths` erscheint die Schaltfläche „Netzwerkpfad übernehmen“.
  - Box „Scanner-Freigabe“: Status, UNC-Pfad und Benutzer (zum Kopieren), oder der
    Hinweis „Nicht eingerichtet – Startmenü › Zettelwirtschaft › Scanner-Freigabe
    einrichten (Administratorrechte)“.
- `SystemHealth.vue`: Zeile „Eingangsordner“ mit „erreichbar“ / „nicht erreichbar:
  <Grund>“, Netzwerkpfad, „zuletzt erreichbar“ und „nächster Versuch“.
- `api.js`: `testFolderSettings()`. `apiErrors.js`: Feld-Labels für die neuen Felder.
- Hilfe/Onboarding: Abschnitt „Netzwerk-Scanner und NAS“ mit Empfehlung (B) vor (A),
  dazu der OneDrive-Hinweis.

## 11. Tests

Credentials kommen aus `tests/credentials.py`. Neu: `TEST_SMB_PASSWORD` und
`TEST_SMB_USER`, mit Einträgen in `backend/.env.test.example`, `e2e/.env.test.example`,
`e2e/helpers/credentials.ts`, sowie `TEST_SCANNER_PASSWORD` für Skripte über
`Get-TestCredential`. Es gibt keine Literale.

**Backend (pytest)**: kein echtes Netz. `network_share` wird über die injizierbare
Fassade `_api` (Klasse mit `add/cancel/get_connection/drive_type/query_dos_device/
console_user_sid/read_user_remote_path/share_flags/net_share_get_info`) per `monkeypatch` gefälscht.

- `test_network_share.py`:
  - Fehlercode-Mapping für alle Codes der Tabelle, unbekannter Code, 1208 mit
    Provider-Text.
  - `connect` übergibt `CONNECT_TEMPORARY`, `lpLocalName=None` und die Freigabe-Wurzel,
    trennt vorher einmal und ignoriert dabei 2250.
  - Connection-Manager: Referenzzähler, Trennen beim letzten `release`, Konflikt
    gleicher Host mit anderem Benutzer ergibt 1219 ohne WNet-Aufruf, `shutdown` trennt
    alles.
  - `classify_drive`: fest/wechselbar erlaubt, remote mit Vorschlag (Dev),
    `NO_ROOT_DIR` mit HKU-Vorschlägen (mehrere, `_Classes` ignoriert), subst auf den
    echten Pfad umgeschrieben, CD-ROM abgelehnt. Nicht-Windows meldet „nicht
    unterstützt“.
- `test_system_folder_settings_network.py`:
  - PUT mit UNC und Zugangsdaten wird gespeichert. Das Passwort steht in der DB als
    Fernet-Token, ist entschlüsselbar und steht nicht in der Antwort.
  - UNC ohne Zugangsdaten ergibt 400, außer `ALLOW_NETWORK_FOLDERS` ist gesetzt.
  - `\\?\UNC\…`, `C$`, `IPC$`, localhost, eigene IP, DNS-Alias auf eigene IP
    (`getaddrinfo` gefälscht), `@SSL` und `DavWWWRoot` ergeben 400.
  - Kein `resolve()`/`stat()` vor `connect` (Spion auf `os.path.realpath`).
  - Ein fehlgeschlagener Connect speichert nichts.
  - Gleicher Host mit anderem Benutzer ergibt 400.
  - `password=None` lässt das Passwort unverändert, `""` löscht es.
  - Ein leerer `EMAIL_ENCRYPTION_KEY` ergibt 503.
  - Netzlaufwerk V: ergibt 400 mit Vorschlag.
  - Docker-Modus ist unverändert (bestehende Tests grün).
  - Sentinel-Test: `TEST_SMB_PASSWORD` taucht in keinem Log (caplog), in Health, GET,
    PUT oder `test-folder` auf.
- `test_watch_folder_network.py` mit gefälschter Verbindung, `PollingObserver` und
  `tmp_path` als „Freigabe“:
  - Polling erkennt eine neue Datei.
  - Eine fehlschlagende Probe führt zu `getrennt`, Backoff-Folge 30/60/120 bis 15 min
    (Zeit gefälscht), Wiederverbinden mit Rescan.
  - Abbrechen gibt die Verbindung frei. Ein Pfadwechsel trennt die alte Verbindung.
  - Health zeigt `watch_folder` mit Grund.
- Export auf UNC: `acquire` vor dem Kopieren, ein Fehler bleibt graceful.
- `--print-watch-dir` in `entrypoint` (DB-Setting oder Fallback).

**PowerShell (`scripts/test-scripts.ps1`)**: Es wird nie etwas real angelegt. Das Skript
kapselt alle Systemeingriffe in Funktionen (`Invoke-ZwNewLocalUser`, `Invoke-ZwSmbShare`,
`Invoke-ZwFirewallRule`, `Invoke-ZwAcl`, `Invoke-ZwLsaRight`, `Invoke-ZwRegistry`). Mit
`-DryRun` bzw. per Dot-Source werden sie durch Stubs ersetzt, die Aufrufe
protokollieren.

- Setup-Reihenfolge und Parameter: nur `ChangeAccess`, kein Everyone,
  `RemoteAddress LocalSubnet`, `Profile Private`.
- Ein UNC-Watch-Ordner bricht ab.
- Idempotenz bei vorhandenem Benutzer, `-NewPassword`.
- Das INI-ResultFile wird geschrieben, das Passwort steht nicht in stdout bei
  `-ResultFile`, und keine Registry-Werte enthalten das Passwort.
- `-Remove` ist tolerant gegenüber Fehlendem.
- `new-secret.ps1 -Kind password`: Länge, Zeichensatz, keine verwechselbaren Zeichen.
- NSIS-Check (bestehende NSIS-Sektion):
  - `freigabe-einrichten.*` sind in der Bundle-Liste, im Uninstall-`Delete` und in der
    Startmenü-Löschung.
  - Uninstall ruft `-Remove`.
  - Für das Freigabe-Skript wird **ExecToStack** verwendet (kein ExecToLog).

**E2E (Playwright, typisierte Mocks)**:

- `api-types.ts` bekommt die neuen Felder (`FolderSettingsResponse`,
  `TestFolderResponse`, `ScannerShareStatus`, Health `watch_folder`).
- Neue Spec `3x-network-folders.spec.ts`:
  - UNC-Eingabe blendet die Zugangsdaten-Felder ein.
  - „Verbindung testen“ zeigt Erfolg oder Fehlertext.
  - Gespeichertes Passwort zeigt den Platzhalter und wird nie als Wert vorbefüllt
    (Request-Body ohne Passwort bei unverändert).
  - Netzlaufwerk-Vorschlag übernehmen.
  - Box „Scanner-Freigabe“ (eingerichtet, fehlt, anderer Ordner).
  - Docker-Modus zeigt den CIFS-Hinweis und keine Zugangsdaten-Felder.
  - SystemHealth zeigt „nicht erreichbar: <Grund>“.
- Beide Projekte (chromium und mobile).

## 12. Akzeptanzkriterien

1. Native: `\\nas\scans\eingang` mit Benutzer und Passwort lässt sich speichern. Ein in
   die Freigabe kopiertes PDF wird spätestens nach Prüfintervall plus Stabilitätszeit
   importiert und nach `verarbeitet\` verschoben. Das wird manuell gegen eine Test-NAS
   oder VM geprüft, nicht gegen die SMB-Konfiguration dieses PCs.
2. Falsches Passwort, unbekannter Server und unbekannte Freigabe ergeben je eine
   eindeutige deutsche Meldung beim Testen oder Speichern. Gespeichert wird nichts.
3. Ist die NAS aus, zeigt die Health-Anzeige „nicht erreichbar“ mit Grund und nächstem
   Versuch. Kommt die NAS zurück, verbindet sich das Backend selbst wieder und importiert
   liegen gebliebene Dateien, ohne Dienst-Neustart.
4. `V:` (Netzlaufwerk) wird abgelehnt, mit UNC-Vorschlag, wenn er auflösbar ist. Lokale
   feste Laufwerke, USB und ein OneDrive-Ordner im eigenen Profil bleiben erlaubt. subst
   wird auf den echten Pfad umgeschrieben.
5. UNC ohne Zugangsdaten, administrative Freigaben, Loopback/eigener Rechner,
   Geräte-Präfixe und WebDAV-Formen werden abgelehnt, auch für den Export. Mit
   `ALLOW_NETWORK_FOLDERS=true` gilt das bisherige Verhalten.
6. Das Passwort erscheint in keinem Log, keiner API-Antwort, keiner Health-Anzeige,
   keinem Task-Ergebnis und keinem Backup im Klartext (Sentinel-Test grün).
7. Herunterfahren oder Pfadwechsel trennt die Verbindung. Nach dem Neustart des Dienstes
   gibt es keine doppelten oder verwaisten Verbindungen.
8. (B) Setup mit Checkbox oder das Startmenü-Skript richtet Benutzer, Freigabe
   (nur Ändern-Recht für diesen Benutzer), NTFS-Recht nur auf dem Eingangsordner und
   eine Firewall-Regel für das lokale Subnetz ein. UNC, Benutzer und Passwort werden
   einmal angezeigt, das Passwort steht nicht im Installationsprotokoll. Die UI zeigt
   den Freigabestatus. Die Deinstallation entfernt alles.
9. Docker: das Verhalten ist unverändert, der CIFS-Hinweis ist sichtbar.
10. Grüne Läufe: `python -m pytest -q`, `pwsh scripts/test-scripts.ps1`,
    `npx playwright test` (beide Projekte) und `npm run typecheck`.

## 12a. Umsetzung der Review-Befunde

1. **Credential-Retargeting (SEC-DATA-02):** Das gespeicherte Passwort wird nur
   wiederverwendet, wenn `\\host\share` (case-insensitive) und der Benutzer gleich
   bleiben. Sonst gibt es 400 „bitte das Passwort der Freigabe erneut eingeben“. Das
   gilt für PUT und `test-folder`, für Watch und Export. Ein anderer Unterordner auf
   derselben Freigabe ist erlaubt.
2. **Kein Fallback auf das Computerkonto:** Jeder UNC-Zugriff läuft über
   `connections.acquire` und Leases. `check_connected` prüft per
   `NetUseEnum`, ob die Verbindung noch besteht (ohne unter laufenden Zugriffen neu zu
   verbinden, siehe 12b). Vor dem
   Trennen werden der Observer gestoppt und laufende Importe abgewartet (`drain`). Der
   Export überspringt die Kopie, wenn die Verbindung fehlt.
3. **DNS bei jedem Verbinden:** `connect()` prüft den Host jedes Mal erneut
   (`check_host_not_local`), auch beim Wiederverbinden nach einer Unterbrechung.
4. **NTFS enger:** siehe 8.4.
5. **Helfer teilt nur geprüfte Ordner:** nur lokale feste Laufwerke, keine Laufwerkswurzel,
   keine System-/Programmordner, kein fremdes Profil, keine Junction. Liegt der Ordner
   außerhalb des Datenordners, kommt eine Rückfrage (oder `-Yes`). Im Setup-Modus
   (`-ResultFile`) wird so ein Ordner nie freigegeben.
6. **Injection:** `-ShareName`/`-UserName` per `ValidatePattern`, kein
   `Invoke-Expression`, kein `cmd /c`, kein `icacls`-String. `-Remove` entfernt ACEs und
   LSA-Rechte vor dem Konto. Ohne `-ResultFile` steht das Passwort nicht in stdout,
   und die INI wird vom Setup sofort gelöscht.
7. **Docker-Hinweis:** `credentials=`-Datei statt `password=` (siehe 9).

Weitere Abweichungen:

- Das Skript startet sich aus 32-Bit-PowerShell als 64-Bit-Prozess neu. Das 32-Bit-Setup
  ruft SysWOW64-PowerShell auf, und LocalAccounts gibt es dort nicht.
- `ALLOW_NETWORK_FOLDERS` ohne Zugangsdaten überwacht wie bisher (Computerkonto), jetzt
  aber per Polling.
- `-DataDir` als Parameter: Beim Setup ist die Registry noch nicht geschrieben.

## 12b. Zweite Review-Runde (Sicherheit/Robustheit)

| Befund | Umsetzung |
|---|---|
| NET-SEC-01 | Scanner-Status löst einen UNC-Watch-Ordner nie auf; `_canonical` verweigert UNC |
| NET-SEC-02 | Leases je Zugriff, `check_connected` ohne Neuverbinden, Observer liest nur mit Verbindungseintrag, Freigabe erst nach dem Ende aller Threads |
| NET-SEC-03 | Export-Kopie unter Lease, routinemäßiges Trennen ohne `force` |
| NET-SEC-04 / R1 | Lifespan beendet auch den neu gestarteten Watch-Task, dann `shutdown_tasks`, dann `connections.shutdown` (wartet auf Leases, danach keine Verbindungen) |
| NET-SEC-06 | Start legt UNC-`WATCH_DIR`/`EXPORT_DIR` nie an (`app/core/data_dirs.py`); UNC-Standard wird beim Speichern geprüft |
| NET-SEC-07 | `find_remote_link` vor `realpath` und zur Laufzeit, Polling mit `lstat` |
| NET-SEC-08 | IP-Pinning, fail closed bei nicht auflösbaren Hosts, keine Adress-Caches |
| NET-SEC-09 | DFS über `NetShareGetInfo` Level 1005, bei jedem Verbinden |
| NET-SEC-10 | Punkt/Leerzeichen am Ende abgelehnt, SMB-Provider fest |
| SEC-NF-01 | kein Vergleich gespeichert ↔ eingegeben, Limit vor der Prüfung |
| SEC-NF-02 | Prüfung im Thread mit Zeitlimit, Limit vor DNS |
| SEC-NF-03 | Laufwerksvorschläge nur vom Konsolenbenutzer |
| R2 | Export nach dem Commit, Backoff nach Verbindungsfehlern |
| R3 | Kopie zuerst, Validierung der Kopie, Lesefehler = vorübergehend |
| R4 / R5 | Observer vor dem Einlesen, Einlesen über den Handler, strengere Stabilitätsprüfung |
| R6 | Importe nicht abbrechen, Lease bis zum Thread-Ende |
| R7 | Sperre je Freigabe für WNet-Aufrufe |
| R8 | UI-Zeitlimit 120 s für Speichern/Test, Backend-Budget kleiner, Proben parallel |
| R9 | gemeinsame Schleife mit Neustart für lokale/Polling-Ordner |
| R10 | Export immer über `acquire` mit den aktuellen Zugangsdaten |
| R11 | `verbinde` nach einem Fehler bleibt Warnung, Export mit `checked_at` |
| R12 | E2E für Fehler-, Export-, Intervall-, Rate-Limit- und subst-Fälle |
| F1 | breite (auch geerbte) Rechte auf dem Ordner werden entfernt; NSIS teilt nur nach erfolgreichem `secure-acl` |
| F2 | Junction/Symlink an beliebiger Stelle im Pfad, Datenordner und verwaltete Ablagen werden nicht freigegeben |
| F3 | `-ScannerAddress`, Hinweis in Ausgabe, Hilfe und Abschlussseite |
| F4 | Wortlaut korrigiert, Warnung bei offenen Freigaben, Batch-/Dienstanmeldung gesperrt |
| F5 | Ordnerwechsel nimmt die Rechte auf dem alten Ordner zurück |
| F6 | `-Remove` nutzt die Namen aus der Registry; Registry erst nach erfolgreichem Entfernen löschen |
| F7 | Setup setzt für ein vorhandenes Konto ein neues Passwort; Konto in einer Gruppe wird abgelehnt; stille Installation ohne Freigabe |
| F8 | `PositionalBinding=$false`, Rechteanforderung im Skript (`-Elevate`) mit sauber gequoteter Befehlszeile |
| F9 | Passwort mit allen Zeichenklassen, NTFS/ReFS-Pflicht, Freigabe anpassen statt löschen, Rollback |
| F10 | Dokumentation: alle Geräte teilen sich das Konto |

## 13. Offen / Follow-up

- Restrisiko (FH-01): Ein bereits laufender Thread (z.B. eine hängende SMB-Kopie) kann
  nicht abgebrochen werden. Hängt er länger als die 30 s beim Beenden, wird trotzdem
  getrennt; seine restlichen Zugriffe liefen dann ohne Verbindungseintrag.
- Mehrere Scanner-Konten für eine Freigabe richtet das Skript nicht ein.

- `EMAIL_ENCRYPTION_KEY` in einen allgemeinen Namen für Zugangsdaten umbenennen (mit
  Alias), eigener Task.
- Gast-Freigaben ohne Passwort werden bewusst nicht unterstützt (Windows blockiert
  unsichere Gastanmeldung ohnehin).
- Scanner, die nur SMB1 können: Es gibt keine Unterstützung. Alternative ist FTP oder
  E-Mail-Import.
