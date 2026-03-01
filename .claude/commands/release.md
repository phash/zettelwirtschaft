Erstelle ein neues Release für Zettelwirtschaft. Argument: neue Versionsnummer (z.B. `1.0.7`).

## Schritte

### 1. Versionsnummer bestimmen
Falls kein Argument übergeben wurde, aktuelle VERSION lesen und Patch-Version hochzählen.

```bash
cat VERSION
```

### 2. Tests ausführen
```bash
cd backend && python -m pytest tests/ -v --tb=short -x
```
Abbrechen wenn Tests fehlschlagen.

### 3. VERSION-Datei aktualisieren
```bash
echo "$ARGUMENTS" > VERSION
```
(oder inkrementierte Version wenn kein Argument)

### 4. README.md Changelog aktualisieren
Den Abschnitt `## Changelog` um einen neuen Eintrag für die neue Version ergänzen. Den Inhalt aus den Git-Commits seit dem letzten Tag ableiten:
```bash
git log $(git describe --tags --abbrev=0)..HEAD --pretty=format:"- %s"
```

### 5. CLAUDE.md aktualisieren
Falls Testanzahl oder Architektur sich geändert hat, entsprechend anpassen.

### 6. Committen
```bash
git add VERSION README.md CLAUDE.md
git commit -m "chore: Version $ARGUMENTS vorbereiten"
```

### 7. Tag setzen und pushen
```bash
git tag v$ARGUMENTS
git push origin main
git push origin v$ARGUMENTS
```

### 8. GitHub Actions abwarten
```bash
gh run list --limit 3
gh run watch <run-id>
```
Alle drei Jobs müssen grün sein: `test` → `build-images` → `create-release`

### 9. Release verifizieren
```bash
gh release view v$ARGUMENTS
```
Prüfen: Setup.exe, .tar.gz und .zip als Assets vorhanden.

## Bekannte Fallstricke

- **setup.nsi**: Jede neue Datei die der Installer braucht muss mit `File "dateiname"` eingetragen werden (VERSION-Datei war in v1.0.5 vergessen → immer "v1.0.3" angezeigt)
- **Fallback-Version** in `install-gui.ps1` Zeile ~13: Niemals eine echte Versionsnummer als Fallback, sondern `"unbekannt"`
- **release.yml** schreibt `echo "${VERSION}" > release/VERSION` ins Paket – das ist korrekt so
- **GHCR Images** bekommen Tag aus dem Git-Tag (ohne `v`-Präfix): `backend:1.0.6`, nicht `backend:v1.0.6`
