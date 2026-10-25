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

Versionsnummer, Größen und Prüfsummen ersetzt `deploy/bump_site.py` in `download.html`,
`index.html`, `hilfe.html`, `latest.json` und `SHA256SUMS.txt` (JSON-LD, `llms.txt` und
`sitemap.xml` weiterhin von Hand prüfen):

```bash
# im Ordner mit den sechs neuen Downloads
sha256sum zettelwirtschaft-<NEW>-* Zettelwirtschaft-<NEW>-* > remote.sha
python -c "import json,os,sys; print(json.dumps({f: os.path.getsize(f) for f in sys.argv[1:]}))" \
  zettelwirtschaft-<NEW>-* Zettelwirtschaft-<NEW>-* > sizes.json   # neben remote.sha
python website/deploy/bump_site.py <OLD> <NEW> live/ out/ remote.sha "<Notes>" <YYYY-MM-DD>
```

`live/` = die aktuellen Seiten vom Server, `out/` = die angepassten Seiten zum Hochladen.
Erwartete Downloads: `zettelwirtschaft-<v>-source.tar.gz`/`.zip`,
`zettelwirtschaft-{backend,frontend}-<v>-docker-image.tar.gz`,
`Zettelwirtschaft-<v>-Native-Setup.exe`, `Zettelwirtschaft-<v>-native-update.zip`.

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
