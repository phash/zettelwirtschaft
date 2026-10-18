# Prompt 12 - Mehrdokument-Scans aufteilen ("Dokument aufteilen")

## Kontext

Ein Einzugsscanner (ADF, z.B. Brother) legt oft einen ganzen Stapel als **eine** PDF ab:
mehrere Briefe, Rechnungen und Anlagen hintereinander, nur Bildseiten, kein Text-Layer.
Heute wird so ein Stapel als ein einziges Dokument analysiert. Die KI sieht dabei nur
die ersten 2000 und letzten 2000 Zeichen, OCR läuft nur über die ersten `MAX_OCR_PAGES=10`
Seiten, und am Ende entsteht ein falsch klassifiziertes Sammeldokument.

Neu: Solche PDFs werden erkannt und in einzelne Dokumente aufgeteilt. Die Erkennung
schlägt die Schnittstellen vor, der Benutzer bestätigt oder korrigiert sie, danach läuft
jeder Teil als eigenes Dokument durch die normale Pipeline (Prompt 03/04).

Stand der Codebasis: v1.6.2, letzte Migration `017_review_data_fixes`.

## Ziel

- Stapel-PDFs zuverlässig in Einzeldokumente zerlegen, **ohne** dass ein falscher Schnitt
  oder ein falsches Zusammenfassen unbemerkt archiviert wird.
- Keine Seite wird zweimal per OCR erkannt: der Text aus der Erkennung wird für die Teile
  wiederverwendet.
- Läuft auf Heim-Hardware (8 GB RAM, CPU-only möglich) im bestehenden Single-Worker,
  ohne zweite parallele OCR-Pipeline.
- Verlustfrei: die Teile sind exakte Seitenkopien (keine Neukodierung der Scans), und das
  Original bleibt bis zum Abschluss (und per Default 30 Tage danach) erhalten.

## Entscheidungen (Owner, verbindlich)

1. **Erkennung = KI-Vorschlag + Bestätigung durch den Benutzer.** Jede Seite wird per OCR
   erkannt; Heuristiken (neuer Briefkopf, "Seite 1 von", Datums-/Absenderwechsel,
   Leerseiten als Trennblatt, Wechsel des Seitenformats) und optional das LLM schlagen
   Schnittstellen vor. Der Benutzer sieht Seiten-Vorschaubilder, verschiebt, ergänzt oder
   entfernt Schnitte per Klick und bestätigt. Danach läuft jeder Teil als eigenes Dokument
   durch die normale Analyse.
2. **Auslöser: beides.**
   - Automatisch: PDFs mit mindestens N Seiten aus Watch-Ordner, Upload oder E-Mail werden
     **vor** der Analyse geprüft. Werden mehrere Dokumente erkannt, geht der Stapel mit
     einem Aufteilungsvorschlag in die Prüfung.
   - Auf Anforderung: Button "Dokument aufteilen" in der Dokument-Detailansicht und eine
     Option beim Upload.

## Technische Grundentscheidungen (aus Design-Review)

| Thema | Entscheidung | Begründung |
|---|---|---|
| Speicherort des Vorschlags | Eigene Tabelle `split_batches` | `ReviewQuestion.document_id` ist NOT NULL, ein Stapel hat aber noch kein Dokument. Ein JSON-Blob am Job wäre schwer abfragbar und ginge beim Löschen des Jobs verloren. |
| Wo läuft die Erkennung | **Immer im Queue-Worker**, auch bei "Dokument aufteilen" | Ein Background-Task über `/api/tasks` würde parallel zum Worker eine zweite Tesseract/poppler-Pipeline (plus ggf. LLM) starten – auf 8 GB / CPU-only nicht tragbar. Der Worker serialisiert, hat Heartbeat, Retry und Pause. `/api/tasks` wird für dieses Feature **nicht** genutzt; der Fortschritt kommt über den Job-Status. |
| OCR-Qualität | Produktionspfad `ocr_service._ocr_pdf_page` (300 dpi, poppler-Timeout, Pixel-Limit, Pre-Processing) | Text wird für die Teile wiederverwendet; `Document.ocr_confidence` bleibt mit heute vergleichbar. 200 dpi wären ca. 25 % schneller, aber dann müssten Teile erneut erkannt werden. |
| Schneiden | pypdfium2 (`PdfDocument.new()` + `import_pages` + `save`) | Verlustfrei: CCITT-Bildströme werden unverändert kopiert (geprüft: 3 Seiten = 216 KB). pypdfium2 ist im venv bereits als transitive Abhängigkeit von pdfplumber vorhanden (5.14.0) und wird explizit gepinnt. |
| Seiten verwerfen | **Nie automatisch** | Leerseiten werden nur markiert; nur ein expliziter Klick schließt eine Seite aus. |
| Im Zweifel | **Prüfung statt Zusammenfassen** | Eine unnötige Prüfung kostet einen Klick, ein falsch zusammengefasster Stapel bleibt unbemerkt im Archiv. |
| Duplex | Weiche Regel (Abzug + Rückfrage in der UI), keine harte Sperre | Gemischte Simplex/Duplex-Stapel dürfen nicht zwangsweise zusammengefasst werden. |
| FK-Richtung | Nur `processing_jobs` und `documents` zeigen per FK auf `split_batches`, nicht umgekehrt | Kein FK-Zyklus, `create_fresh_schema` (create_all) bleibt sauber. |

## Ablauf und Zustandsmodell

### Gesamtablauf

```
Eingang (Upload / Watch-Ordner / E-Mail-Anhang / "Dokument aufteilen")
  -> create_job (PENDING, split_mode = AUTO | FORCE)
  -> Worker: Validierung -> check_duplicate (inkl. Stapel-Hash, siehe Sonderfälle)
  -> NEU: Aufteilungs-Stufe (nur wenn geeignet)
       1. Geometrie + Tinte aller Seiten (pypdfium2, 50 dpi, ~0,6 s für 44 Seiten)
       2. Pro Seite: Text (digital via pdfplumber, sonst OCR 300 dpi; Leerseiten ohne OCR)
          -> Checkpoint alle 5 Seiten, Heartbeat pro Seite
       3. Merkmale + Scoring der Seitenübergänge, Duplex-Erkennung
       4. Optional: ein LLM-Aufruf für unsichere Übergänge
       5a. Ein Dokument (AUTO)   -> normale Pipeline mit Seitentext (keine zweite OCR)
       5b. Mehrere / FORCE       -> AWAITING_SPLIT + Benachrichtigung
  -> Benutzer im Aufteilungs-Editor: bestätigen | als ein Dokument behalten | verwerfen
  -> Bestätigen: Teildateien schreiben -> N Teil-Jobs (PENDING) -> je normale Pipeline
     (Thumbnail, LLM-Analyse mit Seitentext, Archivierung, FTS, Vektorisierung, AuditLog)
```

### Eignung für die Aufteilungs-Stufe

Alle Bedingungen müssen erfüllt sein:

- `is_pdf_type(job.file_type)`
- `job.split_role` ist NULL (Teil-Jobs werden nie erneut geprüft) und `job.split_mode != 'OFF'`
- `job.ocr_text is None` (schließt E-Mail-Body-Jobs aus)
- Modus AUTO: automatische Prüfung aktiv **und** `page_count >= split_min_pages`
- Modus FORCE: `page_count >= 2`
- `page_count <= SPLIT_MAX_PAGES`. Sonst: keine Erkennung, normale Pipeline,
  Benachrichtigung "Zu viele Seiten für die automatische Aufteilung".

### Stapel-Job (`ProcessingJob`, `split_role='SOURCE'`)

```
PENDING -> PROCESSING --(nicht geeignet / 1 Dokument erkannt)--> normale Pipeline -> COMPLETED | NEEDS_REVIEW | FAILED
                      \--(Schnitt vorgeschlagen, unsicher, oder FORCE)--> AWAITING_SPLIT
AWAITING_SPLIT --bestätigen--------------> SPLIT (terminal, document_id NULL)
AWAITING_SPLIT --als ein Dokument--------> PENDING (split_mode=OFF, Seitentext bleibt) -> normale Pipeline
AWAITING_SPLIT --verwerfen---------------> Job + Quelldatei + Vorschaubilder gelöscht
SPLIT          --rückgängig (nur AUTO)---> AWAITING_SPLIT (letzte Aufteilung als Vorschlag)
```

- `recover_interrupted_jobs` setzt weiterhin nur `PROCESSING` zurück. `AWAITING_SPLIT` und
  `SPLIT` sind stabil (Test ergänzen).
- `POST /api/jobs/{id}/retry` liefert **409** für `AWAITING_SPLIT` und `SPLIT`.
- Absturz während der Erkennung: Job geht beim Neustart auf PENDING, die Erkennung setzt
  am Checkpoint `pages_done` fort und verbraucht einen der `MAX_RETRIES=3`.

### Stapel (`SplitBatch`)

```
DETECTING -> PROPOSED -> SPLITTING -> DONE | DONE_WITH_ERRORS
DETECTING -> (gelöscht)              AUTO und nur 1 Dokument erkannt (kein Ballast für normale Mehrseiter)
DETECTING -> FAILED                  verschlüsselt/defekt; AUTO: Job läuft normal weiter, FORCE: Fehlermeldung
PROPOSED  -> KEPT_WHOLE | DISCARDED
DONE | DONE_WITH_ERRORS -> PROPOSED (AUTO) | UNDONE (MANUAL)       [rückgängig]
DONE_WITH_ERRORS -> DONE             nach erfolgreichem Retry der fehlgeschlagenen Teile
```

- **Atomare Übergänge (optimistisches Locking):**
  `UPDATE split_batches SET status=:neu, version=version+1 WHERE id=:id AND status=:alt AND version=:v`.
  rowcount 0 -> HTTP 409 "Der Stapel wurde inzwischen geändert". Schützt gegen
  Doppelklick und zwei Geräte gleichzeitig.
- **SPLITTING** umfasst das Schreiben der Teildateien und die Laufzeit der Teil-Jobs. Das
  Flag `parts_written` (in `final_parts`) unterscheidet die beiden Phasen für die Recovery.
- **Teil-Jobs** (`split_role='PART'`) durchlaufen die normalen Zustände. Wird ein Teil
  terminal (in `_process_job` und `_fail_job`), ruft der Worker
  `split_service.on_part_finished(session, batch_id)` auf. Sind alle Teile terminal:
  `DONE` (alle COMPLETED/NEEDS_REVIEW oder Duplikat) bzw. `DONE_WITH_ERRORS`.

### Recovery beim Start (`split_service.recover_interrupted_batches`)

- `DETECTING` ohne zugehörigen PROCESSING/PENDING-Job -> `FAILED`.
- `SPLITTING` mit `parts_written=false` -> zurück auf `PROPOSED`. Liegengebliebene Dateien
  `data/split/<batch_id>/teil-*.pdf(.tmp)` werden gelöscht (deterministische Namen,
  Pfad-Guard auf `data/split/`).
- `SPLITTING` mit `parts_written=true` -> fehlende Teil-Jobs werden idempotent angelegt
  (eindeutiger Index `(split_batch_id, split_part_index)`, siehe Datenmodell).

## Datenmodell und Migration

### Neues Modell `backend/app/models/split_batch.py`

```python
class SplitOrigin(str, enum.Enum):
    AUTO = "AUTO"        # automatische Erkennung oder Upload-Option
    MANUAL = "MANUAL"    # "Dokument aufteilen" an einem archivierten Dokument

class SplitBatchStatus(str, enum.Enum):
    DETECTING = "DETECTING"
    PROPOSED = "PROPOSED"
    SPLITTING = "SPLITTING"
    DONE = "DONE"
    DONE_WITH_ERRORS = "DONE_WITH_ERRORS"
    KEPT_WHOLE = "KEPT_WHOLE"
    DISCARDED = "DISCARDED"
    UNDONE = "UNDONE"
    FAILED = "FAILED"
```

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | String(36) PK | uuid4 |
| `origin` | Enum(SplitOrigin, native_enum=False) | AUTO / MANUAL |
| `status` | Enum(SplitBatchStatus, native_enum=False), Index | siehe Zustandsmodell |
| `version` | Integer, default 0 | optimistisches Locking |
| `source_job_id` | String(36), Index, **kein FK** | Stapel-Job |
| `source_document_id` | String(36), Index, **kein FK** | MANUAL: Originaldokument (wird nur soft-deleted) |
| `email_account_id` | Integer, nullable | Herkunft für Teile (Ablagebereich des Kontos) |
| `job_source` | String(20) | JobSource des Eingangs, wird an Teile vererbt |
| `original_filename` | String(500) | |
| `file_path` | String(1000) | `data/split/<id>/source.pdf` |
| `file_hash` | String(64), Index (nicht unique) | SHA-256 der Stapeldatei (H) |
| `page_count` | Integer | |
| `pages_done` | Integer, default 0 | Checkpoint der Erkennung |
| `pages` | Text (JSON) | Merkmale pro Seite, siehe unten |
| `proposal` | Text (JSON) | `{boundaries:[…], duplex:bool, duplex_conf:float, partial_from:int|null}` |
| `final_parts` | Text (JSON) | bestätigt: `{parts:[{index, pages:[…]}], excluded:[…], parts_written:bool}` |
| `detector_version` | String(20) | z.B. `"h1"`, `"h1+llm1"` – für spätere Auswertung |
| `llm_used` | Boolean | |
| `detection_ms` | Integer | Messwert für die Auswertung |
| `error_message` | Text | |
| `created_at`, `updated_at`, `confirmed_at`, `finished_at` | UTCDateTime | |

Index `ix_split_batches_status_created (status, created_at)`.

**Bewusst ohne FK** auf `processing_jobs`/`documents`: vermeidet den FK-Zyklus. Dokumente
werden nur soft-deleted (Architekturregel), Jobs nur im terminalen Zustand gelöscht; jede
Stelle, die `source_*_id` liest, muss mit einem fehlenden Datensatz umgehen können
(Test). Wird jemals ein Hard-Delete für Dokumente eingeführt, muss er `split_batches`
mitbereinigen – als Kommentar im Modell festhalten.

**`pages`-JSON** (ein Eintrag pro Seite, nur Merkmale, **kein Volltext**):

```json
{"n":12,"w":591.1,"h":843.2,"rot":0,"ink":0.0028,"top_ink":0.0,"blank":true,
 "source":"ocr|digital|none","conf":0.27,
 "sig":{"counter":[10,null],"refs":["a3f9c1"],"ibans":["7be210"],"addr":false,"salut":false,
        "dates":["2026-03-01"],"header":["…normalisierte Zeilen…"],"margin":[12,44]}}
```

- Aktenzeichen, Vertrags-/Kunden-Nummern und IBANs werden nur als **gekürzter Hash**
  (SHA-256 normalisiert, 12 Hex-Zeichen) gespeichert. Für den Vergleich "gleich/verschieden"
  reicht das; im Klartext stehen sie dann nur im OCR-Text des Dokuments, wie heute.
- `header` enthält normalisierte Kopfzeilen (ohne Wiederholungs-Briefkopf, siehe Erkennung).
  Wird beim Erreichen eines terminalen Zustands auf `[]` gekürzt.

### Neue Spalten `processing_jobs`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `split_batch_id` | String(36), FK `split_batches.id` ON DELETE SET NULL, Index | Stapel- und Teil-Jobs |
| `split_role` | String(10), nullable | `SOURCE` / `PART` |
| `split_part_index` | Integer, nullable | 1-basiert |
| `page_from`, `page_to` | Integer, nullable | Anzeige; maßgeblich ist `final_parts` |
| `split_mode` | String(10), NOT NULL, server_default `'AUTO'` | `AUTO` / `FORCE` / `OFF` |
| `page_ocr` | Text (JSON), nullable | `[{page, text, confidence, source}]` |

- Partieller Unique-Index `ux_processing_jobs_split_part` auf
  `(split_batch_id, split_part_index)` mit `sqlite_where="split_role = 'PART'"` – macht das
  Anlegen der Teil-Jobs idempotent.
- `page_ocr` liegt am Stapel-Job während der Erkennung, wird beim Bestätigen auf die
  Teil-Jobs verteilt und nach erfolgreicher Archivierung eines Teils auf NULL gesetzt (der
  Text steht dann in `Document.ocr_text`). So entsteht keine dauerhafte zweite Textkopie.

### Neue Spalten `documents`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `split_batch_id` | String(36), FK `split_batches.id` ON DELETE SET NULL, Index | Herkunft |
| `split_part_index` | Integer, nullable | |
| `source_pages` | String(40), nullable | z.B. `"3-11,13-44"` (Seiten im Original) |

Partieller Unique-Index `ux_documents_split_part_active` auf
`(split_batch_id, split_part_index)` mit
`sqlite_where="status != 'DELETED' AND split_batch_id IS NOT NULL"` – ein Teil kann nicht
doppelt aktiv archiviert werden.

### Enum-Erweiterungen (VARCHAR mit `native_enum=False`, keine DDL nötig)

- `JobStatus`: `AWAITING_SPLIT`, `SPLIT`. Achtung: `AWAITING_SPLIT` ist länger als der
  bisher längste Wert; SQLite erzwingt VARCHAR-Längen nicht und SQLAlchemy legt ohne
  `create_constraint=True` keinen CHECK an – im Migrationstest gegen eine Bestands-DB prüfen.
- `JobSource`: `SPLIT_REQUEST` (Stapel-Job aus "Dokument aufteilen").
- `AuditAction`: `SPLIT` (am Originaldokument, MANUAL), `SPLIT_CREATED` (an jedem
  Teildokument: "Teil 2/3, Seiten 3–44 aus <Datei>"), `SPLIT_UNDONE`.
- `NotificationType`: `SPLIT_NEEDED` (`document_id` NULL; Frontend leitet auf `/pruefen`).
- Neuer `error_code`: `SPLIT_SOURCE_MISSING` (Quelldatei fehlt beim Bestätigen).

### Migration `backend/alembic/versions/018_document_split.py`

- `down_revision = "017_review_data_fixes"`.
- `op.create_table("split_batches", …)`, danach `op.batch_alter_table` für
  `processing_jobs` und `documents` (Muster wie 013/016 für FKs auf SQLite) inkl. der
  partiellen Unique-Indizes.
- Fresh-DB: Modelle tragen alle Felder, `create_fresh_schema` braucht keine Änderung.
- `migrate.py:detect_stamp`: existiert Tabelle `split_batches` -> Stamp `018` (Prüfung vor 017).
- Downgrade: Indizes, Spalten, Tabelle entfernen.

### Dateiablage

- `data/split/<batch_id>/source.pdf` – Stapeldatei (aus `UPLOAD_DIR` per Kopieren ->
  Commit -> Löschen übernommen, gleiche Rollback-Sicherheit wie die Archivierung).
- `data/split/<batch_id>/teil-01.pdf …` – Teildateien; werden bei der Archivierung des
  Teils nach `archive/…` kopiert und nach dem DB-Commit gelöscht. `remove_upload_copy`
  wird zu einem Helper verallgemeinert, der nur Pfade unter `UPLOAD_DIR` **oder**
  `data/split/` löschen darf.
- `THUMBNAIL_DIR/split/<batch_id>/p001.png` (200 px breit) und `v001.jpg` (1000 px,
  Großansicht).
- `data/split/` liegt im Datenordner: Vollbackup (`--full`) enthält es, DB-Backup nicht.
- Aufbewahrung: `SPLIT_KEEP_SOURCE_DAYS` (Default 30) nach `DONE`/`KEPT_WHOLE`, danach
  löscht der tägliche Wartungslauf Quelle und Vorschaubilder; `DISCARDED` sofort.

## Erkennung

### Messwerte aus der Beispielanalyse (44-seitiger ADF-Scan)

Das Beispiel ist eine einzige Sendung **eines** Absenders (Versicherer): ein Anschreiben
(2 Seiten) und ein Vertragsnachtrag mit Beitragsrechnung (42 Seiten). Keine
personenbezogenen Daten in diesem Dokument; das Beispiel selbst kommt nicht ins Repo.

| Merkmal | Messwert |
|---|---|
| Format | 44 Bildseiten, 1-bit CCITT, 300 dpi, Breite immer 591,12 pt |
| Seitenhöhe | 835,9–844,6 pt: der Einzug misst jedes Blatt, Höhe ist **kein** Grenzsignal |
| Duplex | Höhendifferenz innerhalb eines Blatts (1/2, 3/4 …) ≤ 0,72 pt, zwischen Blättern bis 7,92 pt -> 22 Blätter duplex |
| Tinte Inhaltsseiten | 1,5–9 % dunkle Pixel (3 % Rand ignoriert) |
| Leerseiten | 2 Seiten mit 0,25–0,3 % Tinte, < 80 Zeichen, OCR-Konfidenz ~27 |
| Leerseiten = Trenner? | **Nein.** Beide sind geplante leere Rückseiten mitten im Dokument; der Seitenzähler läuft über sie hinweg weiter. "Leerseite = neues Dokument" hätte zweimal falsch geschnitten. |
| Randcode | Senkrechter Druckerei-Code (DataMatrix + `<job-id>/<i>/<n>/`) im linken Rand, auf 40/44 Seiten lesbar, läuft 1..44 lückenlos: kennzeichnet die **Sendung**, nicht Dokumentgrenzen. Erzeugt Einzeichen-Müllzeilen im OCR; Filter x < 6 % der Breite entfernt sie. |
| OCR-Zeit (Build-Host, 12 Kerne) | 300 dpi: 0,21 s Rendern + 1,70 s OCR pro Seite, ~85 s für 44 Seiten (hochgerechnet). 200 dpi: 65 s. Tesseract ist faktisch single-threaded. |
| Leerseite OCR | 0,47 s – wird künftig übersprungen |
| Heim-Hardware | erwartet 2–4× langsamer: **3–6 Minuten** für 44 Seiten |
| OCR-Konfidenz | Inhaltsseiten 71–81 (300 dpi) |

Trennschärfe der Signale im Beispiel:

| Signal | Ergebnis |
|---|---|
| Seitenzähler "Seite x von y" / "Seite x" | **stärkstes Signal**: auf allen 42 Nicht-Leerseiten gefunden; Neustart "Seite 1" genau an der echten Grenze, davor "2 von 2" |
| Gleiche Referenznummer | ab der Grenze auf allen Seiten identisch (Überlappung 1,0) -> starkes "gleiches Dokument" |
| Anschriftblock + Anrede (obere 35 %) | genau auf den zwei echten Dokumentanfängen, keine Fehltreffer |
| Kopfzeilen-Überlappung | 0,43–0,80 innerhalb, 0,00 an der Grenze – aber auch 0,00 zwischen Seite 1 und 2 eines Briefs (kürzerer Folgekopf). Nur in Kombination nutzbar. |
| TF-IDF-Ähnlichkeit | unbrauchbar (innerhalb 0,03–0,45, Grenze 0,12) – wird nicht verwendet |
| Schlüsselwörter erste Seite | feuern auf jeder Seite, weil "Nachtrag" im wiederholten Briefkopf steht -> erst wiederholte Zeilen entfernen |
| IBAN / USt-ID | nur im Fuß der ersten Seiten; bei einem Absender nicht bewertbar |

Ein grob gewichteter Score ergab genau eine klare Grenze (+6,4); alle anderen Übergänge lagen
zwischen −0,1 und −6,5. Knappste Stellen: rund um die Leerseiten und vor dem ersten
Leerblatt. Eine weitere mögliche Trennung (Beitragsrechnung "Seite 1–4" innerhalb des
fortlaufenden Zählers) erkennt nur Inhalt oder LLM – Konfidenz ~0,3, bleibt dem Benutzer
überlassen (siehe offene Frage 4).

### Merkmale pro Seite (`split_detection_service.extract_features`, reine Funktion)

- **Tinte**: pypdfium2-Render 50 dpi graustufen, Binarisierung < 128, 3 % Rand und linker
  Randstreifen (x < 6 %) ignoriert; `ink`, `top_ink` (obere 15 %). Über
  `Image.histogram()`, kein numpy.
- **Leerseite**: `ink < SPLIT_BLANK_INK` (0,005). Keine Inhalts-OCR (`source="none"`).
- **Text**: digitale Seiten (pdfplumber ≥ `_MIN_DIGITAL_PAGE_CHARS`) direkt mit Konfidenz
  1,0; Scans über `_ocr_pdf_page`. Zeilen mit x < 6 % der Breite werden aus dem
  Inhaltstext gefiltert.
- **Boilerplate**: Zeilen, die auf ≥ 30 % der Seiten vorkommen (wiederholter Briefkopf/Fuß),
  werden vor Kopfzeilen- und Schlüsselwortvergleich entfernt.
- **Seitenzähler**: `Seite\s+(\d{1,3})(?:\s*(?:von|/)\s*(\d{1,3}))?`, zusätzlich
  `(\d{1,3})\s*/\s*(\d{1,3})` nur in den unteren 15 % der Zeilen.
- **Referenzen**: `(Kunden|Vertrags|Rechnungs|Versicherungs|Policen|Mitglieds)[- ]?(Nr|Nummer)\.?:?\s*…`,
  `Aktenzeichen`, `Steuernummer`, `USt-IdNr`; IBAN; normalisiert (Leerzeichen/Punkte weg),
  dann gehasht.
- **Anschrift + Anrede**: PLZ+Ort-Zeile und `Sehr geehrte|Guten Tag|Hallo` in den oberen
  35 % der Zeilen.
- **Datum**: `\d{1,2}\.\d{1,2}\.\d{2,4}`, Hauptdatum = erstes Datum im Kopfbereich.
- **Randcode**: `i/n` aus dem Randstreifen (nur zur Erkennung von Sendung und Seitenreihenfolge).
- **Duplex** (`detect_duplex`): Paare (1/2, 3/4, …) mit Höhendifferenz ≤ 1,0 pt, zwischen
  Paaren häufig > 1,0 pt; gilt bei ≥ 80 % passender Paare (`duplex_conf`).

### Scoring eines Übergangs (Seite i | i+1)

Log-Odds-Summe; Startgewichte in `app/services/split_weights.py`, kalibriert mit dem
Auswerteskript (siehe Tests). Bei Leerseiten wird der Zähler über die Leerseite hinweg
mit der nächsten Nicht-Leerseite verglichen.

| Signal | Beitrag |
|---|---|
| Grundwert (die meisten Übergänge sind keine Grenze) | −2,0 |
| Zähler auf i+1 ist "Seite 1" | +4,0 |
| Zähler auf i ist letzte Seite (x == y) | +2,0 |
| Zähler läuft weiter (x+1, gleiches y, Leerseiten übersprungen) | −4,0 |
| Anschrift + Anrede auf i+1 | +3,0 |
| Gleiche Referenz (beide vorhanden, Überlappung) | −3,0 |
| Referenzen auf beiden Seiten, disjunkt | +3,0 |
| IBAN / USt-ID wechselt (beide vorhanden) | +2,5 |
| Kopfzeilen-Jaccard ≥ 0,4 (ohne Boilerplate) | −1,5 |
| Kopfzeilen-Jaccard = 0 und auf keiner Seite ein Zähler | +1,0 |
| Hauptdatum > 31 Tage auseinander | +1,0 |
| Breiten- oder Ausrichtungswechsel (> 3 pt; Höhe ignoriert) | +2,0 |
| Randcode: `n` wechselt (andere Sendung) | +3,0 |
| Leerseite i+1, Zähler läuft über sie weiter (geplante Rückseite) | −2,0 |
| Leerseite i+1, danach Anschrift oder Zähler-Neustart (Trennblatt) | +2,5 |
| Duplex erkannt (conf ≥ 0,8) und Schnitt nach ungerader Seite (trennt ein Blatt) | −3,0 |

Entscheidung mit `p = sigmoid(score)`:

- `p ≥ 0,80` -> **Schnitt**
- `p ≤ 0,25` -> **kein Schnitt**
- dazwischen -> **unsicher** (in der UI hervorgehoben, nicht gesetzt)

Jeder Übergang speichert `reasons[]` als deutsche Kurztexte ("Seitenzähler beginnt neu:
Seite 1", "Gleiche Vertragsnummer wie vorherige Seite", "Leere Rückseite"), die im Editor
als Tooltip erscheinen.

Erwartung am Beispiel: Übergang 2|3 ≈ +9 (Schnitt, p > 0,99), 1|2 ≈ −6, alle Übergänge im
Nachtrag ≤ −6 (Zähler + Referenz), die Leerseiten-Übergänge ≈ −8. Duplex erkannt.

### Optionaler LLM-Pass (`split_use_llm`, Default an)

- Nur wenn mindestens ein Übergang **unsicher** ist oder FORCE 0 Schnitte ergab. Im
  Beispiel: 0 Aufrufe.
- **Genau ein Aufruf pro Stapel**, höchstens 12 Übergänge (sortiert nach `|p − 0,5|`). Pro
  Übergang die letzten 250 Zeichen von Seite i und die ersten 350 Zeichen von Seite i+1
  (ohne Boilerplate), dazu die Liste der sicheren Schnitte als Kontext; Eingabe auf
  ~6000 Zeichen begrenzt.
- Prompt-Datei `app/prompts/split_boundaries.txt`, gerendert über den bestehenden
  Sanitizer mit `<page_excerpts>`-Wrapping (Prompt-Injection-Schutz wie RAG).
- `llm_service.call_llm` mit JSON-Schema (`{"boundaries":[{"after_page":int,"new_document":bool,"confidence":0..1,"reason":str}]}`),
  über die Analyse-Semaphore (`ANALYSIS_CONCURRENCY=1`).
- Verrechnung: `score += 2,5 · confidence · (+1 wenn new_document, sonst −1)`, danach neu
  klassifizieren. Bleibt ein Übergang unsicher, geht der Stapel in die Prüfung.
- **Übergänge innerhalb eines fortlaufenden Seitenzählers** (Owner-Entscheidung 4): Sie
  sind heuristisch "kein Schnitt" (Zähler −4,0). Zeigen die übrigen Merkmale *ohne* den
  Zähler-Term einen Wechsel (Teil-Score ≥ 0, z.B. neue Anschrift + Anrede oder neue
  Referenznummer), kommen sie zusätzlich als Kandidaten in den LLM-Pass. Sagt das LLM
  `new_document=true` mit `confidence ≥ 0,6`, wird der Übergang auf **unsicher** gehoben –
  nie auf "Schnitt" – mit dem Grund "Neues Dokument innerhalb von Seite x/y? – bitte
  prüfen". Ein so markierter Stapel geht in die Prüfung; der Benutzer entscheidet im Editor.
- Ollama nicht erreichbar, Timeout oder ungültiges JSON: heuristisches Ergebnis bleibt,
  Hinweis "KI-Prüfung nicht verfügbar" am Übergang. Kein Fehlschlag des Jobs.
- Kosten (geschätzt, **nicht gemessen** – kein LLM-Experiment gelaufen): ~2k Tokens Eingabe,
  auf CPU mit dem Analysemodell ca. 20–40 s.

### Ergebnis der Erkennung

- AUTO, kein Schnitt und kein unsicherer Übergang -> **ein Dokument**: Batch-Zeile wird
  gelöscht, Job läuft mit `page_ocr` normal weiter.
- AUTO, mindestens ein Schnitt **oder** ein nach dem LLM-Pass noch unsicherer Übergang
  -> `AWAITING_SPLIT` (Prüfung statt Zusammenfassen).
- FORCE -> immer `AWAITING_SPLIT`, auch mit 0 Vorschlägen.

### Budgets

- `SPLIT_MAX_PAGES=100`: darüber keine Erkennung (normale Pipeline + Hinweis).
- `SPLIT_TIME_BUDGET_S=1200`: wird es überschritten, endet die Text-Erkennung bei Seite N.
  - AUTO: wurde bis dahin nichts gefunden -> ein Dokument + Benachrichtigung "Aufteilung
    nicht vollständig geprüft – bei Bedarf 'Dokument aufteilen' nutzen". Sonst Vorschlag
    mit `partial_from=N`.
  - Im Editor ist der Bereich ab N deutlich als "ohne Texterkennung geprüft" markiert;
    dort werden **keine** Schnitte vorgeschlagen (kein Schnitt nur aus Tinte/Geometrie).
    Diese Seiten werden später in der Analyse ihres Teils erkannt.
- `MAX_OCR_PAGES=10` gilt hier bewusst nicht; die Erkennung braucht jede Seite. Folge: auch
  Teile mit > 10 Seiten bekommen den vollständigen Text (bessere Volltextsuche).
- Speicher: immer nur eine Seite gleichzeitig gerendert; pypdfium2-Dokumente werden pro
  Aufruf geschlossen; Render-Größe über `_render_size` / `MAX_IMAGE_PIXELS` begrenzt
  (pdfium hat keinen Timeout).

## Backend-Komponenten

### `app/services/pdf_split_service.py` (pypdfium2, synchron, via `asyncio.to_thread`)

- `pdf_page_geometry(path) -> list[PageGeom(w, h, rot)]`
- `render_page(path, n, max_px, grayscale=True) -> PIL.Image`
- `page_ink_stats(img) -> (ink, top_ink)`
- `write_part(src, page_indices, dst) -> int`: `PdfDocument.new()` -> `import_pages` ->
  `save(tmp, version=src.get_version())` -> erneut öffnen und Seitenzahl prüfen -> fsync ->
  `os.replace(tmp, dst)`; gibt die Größe zurück.
- `PdfSplitError` für verschlüsselte/defekte Dateien.
- Nur der Worker ruft pdfium auf; die API liefert ausschließlich vorgerenderte Bilder aus.
  Ein modulweites `threading.Lock` bleibt als billige Absicherung.

Abhängigkeiten:

- `backend/requirements.txt`: `pypdfium2>=5.14,<6`.
- `backend/zettelwirtschaft.spec`: `pypdfium2`, `pypdfium2_raw` in `hiddenimports`,
  `collect_dynamic_libs("pypdfium2_raw")`.
- `scripts/build-native.ps1`: Build schlägt fehl, wenn `pdfium.dll` unter `dist/native`
  fehlt (bisher nicht verifiziert).

### `app/services/split_detection_service.py`

- `maybe_detect(session_factory, job, settings) -> SplitDecision(kind: SKIP|SINGLE|AWAITING, batch_id, pages)`
- `run_detection(…)`: checkpointet (alle 5 Seiten eigene kurze Session, Heartbeat pro
  Seite über `_heartbeat`), keine Schreibtransaktion über die OCR hinweg.
- Reine, ohne DB testbare Funktionen: `extract_features`, `boilerplate_lines`,
  `parse_counter`, `parse_refs`, `detect_address_block`, `detect_duplex`, `score_boundaries`.
- `llm_refine(boundaries, excerpts, settings)`.
- Vorschaubilder: `p###.png` aus dem 50-dpi-Render, `v###.jpg` aus dem 300-dpi-OCR-Render
  verkleinert (für Leer- und Digitalseiten 100-dpi-Render).

### `app/services/split_service.py`

- `create_batch(session, job, H, …)`: Quelle nach `data/split/<id>/source.pdf` kopieren,
  Batch + Job (`split_batch_id`, `split_role='SOURCE'`, neuer `file_path`) committen, erst
  danach die Upload-Kopie löschen.
- `confirm(session, batch_id, version, parts, excluded)`: Validierung (siehe Sonderfälle),
  Status-Übergang PROPOSED -> SPLITTING, Teildateien schreiben, `parts_written=true`,
  Teil-Jobs anlegen (vererbt: `job_source`, `email_account_id`; `page_ocr`-Ausschnitt,
  `split_mode='OFF'`), Stapel-Job -> `SPLIT`.
- `keep_whole`, `discard`, `undo`, `retry_failed_parts`, `on_part_finished`,
  `recover_interrupted_batches`, `cleanup_expired` (im täglichen Wartungslauf).
- `is_protected_source(path)`: `purge_duplicate_upload_copies`, die Upload-Bereinigung
  und jeder künftige Aufräumer überspringen Pfade von Stapeln in DETECTING, PROPOSED oder
  SPLITTING.
- `request_document_split(session, document_id)`: legt einen Stapel-Job
  (`JobSource.SPLIT_REQUEST`, `split_mode='FORCE'`) mit einer Kopie der Archivdatei an.

### Worker-Integration (`queue_worker_service._process_job`)

Neue Stufe zwischen `check_duplicate` und `generate_thumbnail`:

```python
decision = await split_detection_service.maybe_detect(session_factory, job, settings)
if decision.kind == "AWAITING":
    return  # Status, Batch, Benachrichtigung sind committed; keine Archivierung
pre_ocr_pages = decision.pages or _load_page_ocr(job)  # SINGLE bzw. Teil-Job
```

- `analyze_document` bekommt `pre_ocr_pages: list[PageText] | None`. Daraus baut
  `_analyze_document_unlimited` das `OcrResult`: Text via `_merge_pages`, Konfidenz =
  **Mittelwert der Seitenkonfidenzen** (statt der irreführenden 1,0 des
  `pre_ocr_text`-Pfads). Der `pre_ocr_text`-Pfad für E-Mail-Bodies bleibt unverändert.
- Nach dem Commit eines Teils: `page_ocr = NULL`, `on_part_finished`.
- Archivierung eines Teils setzt `Document.split_batch_id`, `split_part_index`,
  `source_pages` und schreibt `AuditAction.SPLIT_CREATED`.

## API

Neuer Router `app/api/split.py` unter `/api/split` (PIN-Middleware greift wie überall).

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/split?status=PROPOSED,…&page=` | Stapel-Liste (paginiert) |
| GET | `/api/split/{id}` | Detail: Status, version, Seiten (Geometrie, blank, Zähler, Konfidenz – **ohne Text**), Übergänge mit `decision`/`reasons`/`llm`, Duplex, `partial_from`, Fortschritt `pages_done/page_count` |
| GET | `/api/split/{id}/pages/{n}/thumbnail` | 200-px-PNG |
| GET | `/api/split/{id}/pages/{n}/preview` | 1000-px-JPEG (Großansicht) |
| POST | `/api/split/{id}/confirm` | Body `{version, parts:[{pages:[1,2]}, …], excluded:[12]}` -> 202 mit Teil-Job-IDs; 409 bei Versionskonflikt; 422 bei ungültiger Aufteilung |
| POST | `/api/split/{id}/keep-whole` | `{version}` -> Job zurück auf PENDING |
| POST | `/api/split/{id}/discard` | `{version}` -> AUTO: Job + Quelle gelöscht; MANUAL: nur Stapel verworfen |
| POST | `/api/split/{id}/undo` | `{version}` -> 409 mit Begründung, wenn Teile bereits bearbeitet wurden |
| POST | `/api/split/{id}/retry-failed` | fehlgeschlagene Teil-Jobs neu einreihen |
| POST | `/api/documents/{id}/split` | "Dokument aufteilen" -> 202 `{batch_id, job_id}`; 409 wenn schon ein aktiver Stapel existiert; 422 bei Nicht-PDF, < 2 Seiten oder DELETED |

Bestehende Endpoints:

- `POST /api/documents/upload`: optionales Formularfeld `split_check: bool = false`
  -> `create_job(…, split_mode="FORCE")`.
- `GET /api/jobs…`: Antwort ergänzt um `split_batch_id`, `split_role`, `page_from`,
  `page_to`, bei `PROCESSING` mit Stapel `split_progress {pages_done, page_count}`.
  Statusfilter akzeptiert die neuen Werte.
- `POST /api/jobs/{id}/retry`: 409 für `AWAITING_SPLIT`/`SPLIT`.
- `DELETE /api/jobs/{id}`: für `AWAITING_SPLIT` gleichbedeutend mit `discard`; für `SPLIT`
  nur den Datensatz (Teile behalten ihre Dokumente).
- `DocumentResponse`: `split_batch_id`, `split_part_index`, `split_part_count`,
  `source_pages`, `can_undo_split`.
- Review-Statistik / Navigations-Badge: zählt Stapel in `PROPOSED` mit.
- System-Einstellungen (DB, `settings_service`): `split_auto_enabled` (true),
  `split_min_pages` (3), `split_use_llm` (true).
- Config (`config.py`): `SPLIT_MAX_PAGES=100`, `SPLIT_TIME_BUDGET_S=1200`,
  `SPLIT_BLANK_INK=0.005`, `SPLIT_MAX_PARTS=50`, `SPLIT_KEEP_SOURCE_DAYS=30`,
  `SPLIT_THUMBNAIL_WIDTH=200`; in `Convert-Env-To-Config.ps1` und `.env.example` nachziehen.

PWA: `/api/split/*/pages/*` als `NetworkOnly` in die Workbox-Regeln aufnehmen (Binärdaten,
wie Thumbnails heute).

E2E-Typen (`e2e/helpers/api-types.ts`): `JobStatus` + `AWAITING_SPLIT`/`SPLIT`,
`NotificationType` + `SPLIT_NEEDED`, neue Typen `SplitBatchResponse`, `SplitPage`,
`SplitBoundary`, `SplitConfirmRequest`, Erweiterungen von Job- und Dokument-Antwort.

## Oberfläche

Alle Texte auf Deutsch mit echten Umlauten.

### Aufteilungs-Editor `SplitEditorView` (`/aufteilen/:batchId`)

- Kopf: Dateiname, Herkunft (Upload/Watch-Ordner/E-Mail/Dokument), Seitenzahl, Hinweis
  "Duplex erkannt: 22 Blätter", ggf. "KI-Prüfung nicht verfügbar".
- Raster der Seiten-Vorschaubilder (Desktop 6–8 pro Zeile, Mobile 2 Spalten). Zwischen
  zwei Seiten ein Übergangs-Marker:
  - **Schnitt** (durchgezogener Balken mit Schere),
  - **unsicher** (gestrichelt, orange, Tooltip mit Begründungen),
  - **kein Schnitt** (schmal, erscheint beim Hovern/Tippen).
  Klick/Tippen schaltet um. Tastatur: Pfeiltasten wandern, Leertaste schaltet.
- Teile werden farbig hinterlegt und beschriftet: "Teil 1 · Seiten 1–2".
- Leerseiten tragen das Etikett "Leerseite" (bzw. "Leere Rückseite"/"Trennblatt?"). Über
  einen Seiten-Button "Seite weglassen" wird eine Seite ausgeschlossen (ausgegraut,
  rückgängig machbar). Nie automatisch.
- Duplex: Ein Schnitt innerhalb eines Blatts fragt nach: "Dieser Schnitt trennt Vorder-
  und Rückseite eines Blatts. Trotzdem schneiden?"
- Doppelklick bzw. Lupe öffnet die Großansicht (1000 px) mit Blättern.
- Bereich ab `partial_from` mit Hinweis "Ab Seite N ohne Texterkennung – bitte selbst prüfen".
- Während `DETECTING`: Fortschritt "Seite 17 von 44 wird erkannt …", Editor gesperrt.
- Aktionen (Mobile als feste Leiste unten):
  - **"In N Dokumente aufteilen"** (primär)
  - **"Als ein Dokument behalten"**
  - **"Verwerfen"** (nur AUTO, mit Bestätigungsdialog "Datei wird gelöscht")
- 409: Hinweis "Der Stapel wurde inzwischen geändert" und Neu laden.

### Weitere Stellen

- **Prüfen (`/pruefen`)**: neuer Abschnitt "Stapel zum Aufteilen (n)" über den
  Rückfragen, je Karte mit Vorschaubild, Seitenzahl und vorgeschlagener Teilezahl.
- **Benachrichtigung**: "„scan_0427.pdf“ enthält vermutlich 2 Dokumente – bitte prüfen."
- **Upload**: Checkbox "Enthält mehrere Dokumente – vor der Analyse aufteilen". Im
  Fortschritt Status "Wartet auf Aufteilung" mit Link zum Editor.
- **Dokument-Detail**: Button "Dokument aufteilen" (nur PDF ≥ 2 Seiten, nicht gelöscht).
  Bestätigungsdialog nennt, was übernommen wird (Ablagebereich, manuelle Tags) und dass
  das Original nach der Aufteilung als gelöscht markiert wird (wiederherstellbar über
  "rückgängig").
- **Teildokument-Detail**: "Teil 2 von 3 aus „scan_0427.pdf“ (Seiten 3–44)" mit Link zum
  Stapel und "Aufteilung rückgängig" (deaktiviert mit Erklärung, wenn Teile bearbeitet wurden).
- **Jobliste / Dashboard**: neue Status-Labels "Wartet auf Aufteilung", "Aufgeteilt".
  Alle Stellen im Frontend, die Job-Status explizit auflisten (grep `NEEDS_REVIEW`,
  `'FAILED'`), nachziehen.
- **Einstellungen -> Verarbeitung** (`SplitSettings.vue`): automatische Prüfung an/aus,
  Mindestseitenzahl, KI-Prüfung an/aus.

## Sonderfälle

- **Duplikate und Hashes:**
  - Der Stapel-Hash H steht in `split_batches.file_hash`. `check_duplicate` prüft
    zusätzlich: gibt es einen Stapel mit H in `PROPOSED`, `SPLITTING`, `DONE` oder
    `DONE_WITH_ERRORS` -> Job `FAILED` mit `error_code=DUPLICATE` und Hinweis "Dieser
    Stapel wurde bereits aufgeteilt (→ Stapel)". Ohne diese Prüfung würde ein erneuter
    Import nicht auffallen: die Teile haben andere Hashes, und das Original ist nie ein
    aktives Dokument (AUTO) bzw. soft-deleted (MANUAL), sodass `ux_documents_file_hash_active`
    nicht greift.
  - `KEPT_WHOLE`: das Gesamtdokument trägt H, der normale Check greift.
  - `DISCARDED`/`UNDONE`: erneuter Import ist erlaubt.
  - Jeder Teil bekommt den echten Hash seiner Teildatei. Ist ein Teil schon aktiv archiviert
    (z.B. Anschreiben früher einzeln gescannt), endet der Teil-Job als Duplikat; das zählt
    für den Stapel als erledigt (`DONE`, Hinweis mit Link), nicht als Fehler.
  - Ausblick (Etappe 4, optional): Fingerprint pro Seite (SHA-256 der Roh-Bildströme),
    um denselben Teil aus einem **anderen** Stapel zu erkennen – pdfium-Ausgabedateien
    sind nicht byte-stabil.
- **Aufteilen eines archivierten Dokuments (MANUAL):** Teile übernehmen Ablagebereich
  (falls vom Benutzer gesetzt) und manuelle Tags; Steuerkategorie, Garantie und
  Rückfragen nicht (teilspezifisch, kommen aus der neuen Analyse). Das Original wird erst
  nach Archivierung **aller** Teile soft-deleted (`AuditAction.SPLIT` mit Teil-IDs). Die
  Archivdatei des Originals bleibt (Soft-Delete), daher ist "rückgängig" verlustfrei.
- **Rückgängig:** nur solange kein Teil vom Benutzer bearbeitet wurde (AuditLog
  `UPDATED`, `TAG_ADDED/REMOVED`, `REVIEWED` vom Benutzer an einem Teil) – sonst 409 mit
  Erklärung. Teile werden soft-deleted; MANUAL: Original wieder aktiv (`UNDONE`); AUTO:
  Stapel wieder `PROPOSED` (nur solange die Quelle noch aufbewahrt wird).
- **Validierung beim Bestätigen:** jede Seite gehört genau einem Teil oder ist
  ausgeschlossen; Seiten je Teil aufsteigend und zusammenhängend (Ausschlüsse erlaubt);
  jeder Teil hat mindestens eine Nicht-Leerseite; höchstens `SPLIT_MAX_PARTS` Teile;
  mindestens eine Seite insgesamt. Sonst 422 mit deutscher Meldung.
- **Quelldatei fehlt** beim Bestätigen: `SPLIT_SOURCE_MISSING`, Stapel bleibt `PROPOSED`,
  UI bietet "Verwerfen".
- **Verschlüsselte/defekte PDFs:** Erkennung `FAILED`; AUTO läuft normal weiter (wie heute),
  FORCE meldet "Datei kann nicht aufgeteilt werden".
- **Digitale PDFs:** Text pro Seite aus pdfplumber, keine OCR – Erkennung in Sekunden.
- **Gemischte Formate** (A5-Beleg, Querformat): Breiten-/Ausrichtungswechsel ist Signal;
  Höhenschwankung eines Einzugs nicht.
- **E-Mail:** PDF-Anhänge sind geeignet, der E-Mail-Body nicht. Teile erben
  `email_account_id` (Ablagebereich des Kontos). `ProcessedEmail.processing_job_id` zeigt
  auf den Stapel-Job.
- **Watch-Ordner:** Quelle wird beim Anlegen des Stapels nach `data/split/` übernommen;
  bis dahin schützt `is_protected_source`.
- **Queue-Blockade:** Die Erkennung eines großen Stapels belegt den einzigen Worker
  3–6 Minuten (Heim-Hardware); andere Dokumente warten. Pause des Workers greift erst
  nach dem nächsten Checkpoint. Fortschritt ist in der UI sichtbar.
- **Gleichzeitige Bearbeitung:** zwei Geräte/Doppelklick -> optimistisches Locking (409).
- **Ollama nicht erreichbar:** Heuristik allein; unsichere Übergänge gehen in die Prüfung.

## Tests und Akzeptanzkriterien

### Backend-Tests (pytest)

- **Reine Erkennung** (`test_split_detection.py`): synthetische Seitenmerkmale nach dem
  Muster des Beispiels (Anschreiben 2 Seiten + 42-Seiten-Dokument mit zwei leeren
  Rückseiten, Duplex) -> genau ein Schnitt nach Seite 2; Leerseiten kein Schnitt; Zähler
  über Leerseiten; Boilerplate-Entfernung ("Nachtrag" im Kopf feuert nicht); Trennblatt
  mit folgendem Neustart -> Schnitt; Duplex-Abzug; Schwellen. Fixtures nur synthetisch,
  **keine echten Scans oder Personendaten im Repo**.
- **PDF-Operationen** (`test_pdf_split_service.py`): mit reportlab erzeugte PDFs;
  `write_part` Seitenzahl, Bildströme byteidentisch, tmp+replace, verschlüsselte PDF ->
  `PdfSplitError`.
- **Worker-Integration**: AUTO ein Dokument -> normale Pipeline, OCR-Mock genau einmal pro
  Seite aufgerufen; AUTO mehrere -> `AWAITING_SPLIT` + Benachrichtigung; FORCE; Budgets
  (Seiten, Zeit); E-Mail-Body nie geprüft; Teil-Jobs nie erneut geprüft; Konfidenz =
  Seitenmittel.
- **Zustände**: Bestätigen/Behalten/Verwerfen; Doppel-Bestätigung -> eine Aufteilung + 409;
  Retry 409 für `AWAITING_SPLIT`/`SPLIT`; `recover_interrupted_jobs` lässt
  `AWAITING_SPLIT` in Ruhe; Absturz-Recovery in `DETECTING` (Checkpoint) und `SPLITTING`
  (vor/nach `parts_written`); idempotente Teil-Jobs (Unique-Index).
- **Duplikate**: erneuter Import eines aufgeteilten Stapels -> DUPLICATE; nach DISCARDED
  erlaubt; Teil-Duplikat -> Stapel `DONE`.
- **MANUAL**: Übernahme Ablagebereich/Tags, Original erst nach allen Teilen DELETED,
  Undo-Sperre nach Bearbeitung, fehlendes Originaldokument wird toleriert.
- **Aufräumen**: `is_protected_source` in allen Bereinigungspfaden; Aufbewahrungsfrist.
- **Migration 018**: Upgrade/Downgrade auf Bestands-DB, `detect_stamp`, Fresh-Schema,
  neue Enum-Werte lesbar.

### E2E (Playwright, beide Projekte, gemockt)

- Editor: Schnitt setzen/entfernen, unsicheren Übergang bestätigen, Duplex-Rückfrage,
  Seite weglassen, Request-Body von `confirm`, 409-Hinweis, Fortschritt während `DETECTING`
  (per `page.clock`).
- Prüfen-Seite mit Stapel-Abschnitt; Benachrichtigung leitet weiter.
- Dokument-Detail "Dokument aufteilen" + Dialog; Teildokument mit Herkunft und Undo.
- Upload-Checkbox sendet `split_check`.
- Mobile (Pixel 5): Raster 2 Spalten, Aktionsleiste unten.
- `npm run typecheck` gegen die erweiterten `api-types.ts`.

### Auswertung (lokal, nicht in CI)

`backend/scripts/eval_split.py <ordner> <ground_truth.json>`: führt die Erkennung auf
lokalen Stapeln aus und gibt Precision/Recall der Schnitte, Anteil unnötiger Prüfungen,
`detection_ms` aus. Die Beispieldateien bleiben außerhalb des Repos.

### Akzeptanzkriterien

- [ ] Beispiel-Stapel (44 Seiten): Vorschlag genau ein Schnitt nach Seite 2, kein Schnitt an
      den Leerseiten, Duplex erkannt, 0 LLM-Aufrufe
- [ ] Keine Seite wird zweimal per OCR erkannt (Erkennung + Teil-Analyse)
- [ ] Erkennung des Beispiels ≤ 2 Minuten auf dem Build-Host (300 dpi, Leerseiten ohne OCR)
- [ ] Speicherbedarf der Erkennung nicht höher als der heutige OCR-Pfad (eine Seite gleichzeitig)
- [ ] Automatische Prüfung greift für Upload, Watch-Ordner und E-Mail-Anhänge ab N Seiten
- [ ] "Dokument aufteilen" in Detailansicht und Upload funktioniert und läuft über die Queue
- [ ] Benutzer kann Schnitte setzen, entfernen, Seiten weglassen und bestätigen; jede
      Seite landet genau einmal in einem Teil oder ist bewusst ausgeschlossen
- [ ] Jeder Teil wird als eigenes Dokument analysiert, archiviert, FTS- und
      vektorindiziert und trägt Herkunft (`source_pages`) + AuditLog `SPLIT_CREATED`
- [ ] Teile sind verlustfrei (Bildströme unverändert)
- [ ] Doppelte Bestätigung erzeugt keine doppelten Dokumente
- [ ] Erneuter Import eines aufgeteilten Stapels wird als Duplikat erkannt
- [ ] Ollama aus: Erkennung funktioniert heuristisch, kein Job schlägt deswegen fehl
- [ ] Absturz während Erkennung oder Aufteilung hinterlässt keinen inkonsistenten Zustand
- [ ] Native-Build enthält `pdfium.dll`, Aufteilung funktioniert im Native-Modus
- [ ] Alle bestehenden Backend- und E2E-Tests bleiben grün

## Aufwand in Etappen

| Etappe | Inhalt | Aufwand |
|---|---|---|
| 1 – Manuelles Aufteilen | Modell + Migration 018, `pdf_split_service`, Worker-Stufe nur für FORCE (Upload-Checkbox, "Dokument aufteilen"), Seiten-OCR mit Checkpoint + Vorschaubildern, Editor ohne Vorschläge, Bestätigen/Behalten/Verwerfen, Teil-Pipeline mit `pre_ocr_pages`, Duplikat-Logik, AuditLog, Recovery, Tests | 3–4 Tage |
| 2 – Heuristik + Automatik | Merkmale, Scoring, Duplex, Budgets, AUTO-Auslöser, Einstellungen, Benachrichtigung, Prüfen-Abschnitt, Auswerteskript, Kalibrierung | 2–3 Tage |
| 3 – KI-Pass + Lebenszyklus | LLM-Pass mit Schema + Prompt, Undo inkl. Bearbeitungssperre, Aufbewahrung/Aufräumen, MANUAL-Übernahme | 2 Tage |
| 4 – Härtung + Release | Native-Build (`pdfium.dll`-Check), E2E beide Projekte, `api-types.ts`, Doku (CLAUDE.md, memory), optional Seiten-Fingerprint | 1–2 Tage |

Etappe 1 ist allein schon nützlich (Benutzer teilt selbst auf) und liefert die Daten für die
Kalibrierung von Etappe 2.

## Entscheidungen zu den offenen Fragen (Owner, 2026-10-18)

1. **Mindestseitenzahl N = 3** (Einstellung `SPLIT_MIN_PAGES`, Default 3).
2. **Originalstapel 30 Tage aufbewahren**, danach automatisch löschen.
3. **Aufteilen eines archivierten Dokuments: Ablagebereich und manuelle Tags werden
   übernommen**, Steuerkategorie/Garantie/Notizen pro Teil neu analysiert; das Original wird
   erst nach Archivierung aller Teile soft-deleted und ist über "rückgängig" wiederherstellbar.
4. **Schnitte innerhalb eines fortlaufenden Seitenzählers darf die KI vorschlagen** — aber
   nur als *unsicherer* Vorschlag (nie Stufe "Schnitt", nie automatisch ausgeführt); der
   Editor hebt ihn hervor ("innerhalb von Seite x/y — bitte prüfen"). Der Benutzer
   entscheidet.

## Offene Fragen an den Owner

1. **Gibt es weitere Beispiel-Stapel zur Kalibrierung?** Das vorhandene Beispiel stammt von
   einem einzigen Absender; Absender-, IBAN- und Datumswechsel ließen sich nicht messen.
   Empfehlung: **3–5 weitere echte Stapel mit verschiedenen Absendern lokal bereitstellen**
   (bleiben außerhalb des Repos), bevor Etappe 2 abgeschlossen wird. Ohne sie startet die
   Automatik mit den obigen Gewichten und der Regel "im Zweifel prüfen".

## Nicht-Ziele

- Kein automatisches Archivieren ohne Bestätigung, wenn mehrere Dokumente erkannt wurden
- Kein automatisches Löschen von Seiten
- Kein Zusammenführen mehrerer Dateien zu einem Dokument
- Keine Neusortierung von Seiten (falsche Reihenfolge wird nur über den Randcode erkannt
  und als Hinweis angezeigt)
- Keine Nutzung von `/api/tasks` für die Erkennung
