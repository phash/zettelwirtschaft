# Website (zettelwirtschaft.mr-development.de)

Statische Präsentations-, Anleitungs- und Download-Seite. Kein Build-Schritt — die Dateien
werden 1:1 ausgeliefert. Die Anwendung selbst ist local-first und wird **nicht** öffentlich betrieben.

| Datei | Inhalt |
|---|---|
| `index.html` | Startseite: Überblick, „Auf einen Blick“-Fakten, Funktionen, Betriebsmodi |
| `anleitungen.html` | Schritt-für-Schritt-Anleitungen (je eine `HowTo`-JSON-LD) |
| `hilfe.html` | FAQ (`FAQPage`-JSON-LD, aus dem sichtbaren Text erzeugt — bei Änderungen mitziehen) |
| `download.html` | Downloads + SHA-256-Prüfsummen |
| `latest.json` | Manifest der In-App-Update-Prüfung (`UPDATE_MANIFEST_URL`) |
| `llms.txt`, `sitemap.xml`, `robots.txt` | Hinweise für Suchmaschinen und KI-Crawler |
| `deploy/` | `docker-compose.yml` + `nginx.conf` des Containers, Caddy-Block |

Die Release-Artefakte unter `downloads/` liegen nur auf dem Server und sind nicht versioniert.

## Bei einem Release anpassen

- Versionsnummer, Datum und Dateinamen in allen HTML-Seiten, `latest.json` und `llms.txt`
- Prüfsummen in `download.html` (aus `downloads/SHA256SUMS.txt`)
- `softwareVersion`/`datePublished` in den JSON-LD-Blöcken von `index.html` und `download.html`
- `lastmod` in `sitemap.xml`
- Cache-Busting `assets/site.css?v=<datum>` erhöhen, wenn sich das CSS ändert (7 Tage Browser-Cache)

Das Inline-Script `classList.add('js')` ist per Hash in der CSP des Caddy-Blocks freigegeben —
bei Änderung den Hash in `deploy/caddy-block.txt` und `/opt/caddyserver/Caddyfile` anpassen.
JSON-LD-Blöcke (`type="application/ld+json"`) werden nicht ausgeführt und brauchen keinen Hash.

## Deploy

Server `musikersuche@musikersuche.org`, Dateien unter `/opt/zettelwirtschaft/html/`:

```bash
ssh musikersuche@musikersuche.org \
  'cd /opt/zettelwirtschaft && tar czf html-backup-$(date +%Y%m%d-%H%M).tgz --exclude=html/downloads html'
rsync -av --exclude downloads --exclude README.md --exclude deploy \
  website/ musikersuche@musikersuche.org:/opt/zettelwirtschaft/html/
```
