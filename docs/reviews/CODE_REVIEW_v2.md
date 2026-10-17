# Code Review v2 — Zettelwirtschaft (post v1.2.2)

Datum: 2026-05-08
Scope: Neue Findings nach dem ersten Review (`CODE_REVIEW_FINDINGS.md`, 47/55 gefixt). Fokus: Pipeline-Korrektheit, Performance, Konsistenz, Test-Lücken.

Severity: **BLOCKER** (Daten/Korrektheit) > **HIGH** (sichtbarer Bug / DoS / Race) > **MEDIUM** (UX / Skalierung) > **LOW** (Code-Hygiene).

---

## BLOCKER

### B-1 — `reanalyze_document` referenziert nicht-existentes Feld `analysis.issuer`
**Datei:** `backend/app/api/review.py:235-236`
**Problem:** Der Re-Analyse-Endpoint (v1.2.2 Feature) liest `analysis.issuer`, aber das `AnalysisResult`-Dataclass hat dieses Feld nicht — es heißt `sender` (siehe `analysis_service.py:45`). Sobald das LLM beim Re-Analysieren einen Aussteller liefert, wirft das `AttributeError` und der Re-Analyse-Endpoint kollabiert mit HTTP 500. Kommentar im Datamodel sagt explizit „LLM-Feld 'sender' wird als 'issuer' auf Document gemappt".
**Tests:** Bestehende Tests (`test_review.py:847+`) decken nur 404/400-Pfade ab — der Happy-Path mit gemocktem LLM fehlt, deshalb läuft der Bug undetektiert.
**Auswirkung:** Re-Analyse ist die einzige UX-Route für „LLM war beim ersten Mal nicht erreichbar". User klickt „Erneut analysieren", bekommt aber „Fehler bei der Re-Analyse" sobald die Erkennung tatsächlich erfolgreich war.
**Fix:**
```python
# review.py:235-236
if analysis.sender:
    doc.issuer = analysis.sender
```
Plus Test mit gemocktem `_try_combined_analysis` der ein `AnalysisResult` mit `sender` zurückgibt.

---

### B-2 — Stuck PROCESSING-Jobs werden bei Restart nicht recovert
**Datei:** `backend/app/services/queue_worker_service.py:125-208` (run_queue_worker), `backend/app/main.py:86-91`
**Problem:** Wenn der Backend-Container während der Job-Verarbeitung abstürzt (OOM, kill, neuer Deploy), bleibt der gerade verarbeitete Job auf `PROCESSING` stehen. Der Worker sucht beim Start nur nach `PENDING`-Jobs (Zeile 142). Es gibt keinen Recovery-Step beim Lifespan-Startup, der `PROCESSING` → `PENDING` zurücksetzt. Der Job hängt für immer, wird im Dashboard ewig als „in Verarbeitung" angezeigt, blockiert aber tatsächlich nichts (anderer Worker existiert nicht). Manuell ist es nur über DB-Eingriff lösbar.
**Auswirkung:** Datenintegrität / UX. Dashboard-Stat `processing_jobs` läuft hoch und stimmt nicht.
**Fix:** In `lifespan` vor Worker-Start:
```python
async with async_session_factory() as session:
    await session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.status == JobStatus.PROCESSING)
        .values(status=JobStatus.PENDING, error_message="Worker neu gestartet, Job wird wiederholt")
    )
    await session.commit()
```

---

### B-3 — Hash-Kollision-Pfad löscht Quelldatei trotz NEEDS_REVIEW-Status
**Datei:** `backend/app/services/queue_worker_service.py:109-122`
**Problem:** Bei Duplikat-Erkennung wird der Job auf `NEEDS_REVIEW` gesetzt und die Exception abgefangen. Im Anschluss läuft `await session.commit()` (Zeile 115) plus `file_path.unlink()` (118-122). Die Original-Upload-Datei wird also gelöscht, obwohl es nichts zu reviewen gibt — der User kann das Duplikat nicht inspizieren oder umbenennen. `error_message` enthält nur „Duplikat erkannt: Hash xxx...". Der Status `NEEDS_REVIEW` macht hier keinen Sinn (keine ReviewQuestion existiert), korrekt wäre `FAILED` oder ein neuer Status `DUPLICATE`.
**Auswirkung:** User verliert den Upload, Dashboard zeigt „Review nötig" für etwas das nicht reviewbar ist.
**Fix:** Status `JobStatus.FAILED` + spezifischere `error_message` mit ID des Duplikats; Datei in `data/duplicates/` archivieren oder im Upload-Ordner belassen.

---

## HIGH

### H-4 — `rebuild_vectors` blockiert HTTP-Worker minutenlang
**Datei:** `backend/app/api/system.py:296-322`
**Problem:** Der Endpoint iteriert synchron über alle Dokumente und ruft `vectorize_document` für jedes auf. Bei 500 Dokumenten und 8s pro LLM-Embedding sind das 60+ Minuten — einer von drei uvicorn-Workern hängt komplett. Frontend-Timeout (`axios` Default 0/30s) wirft längst Fehler, im Backend läuft die Schleife trotzdem weiter (kein cancel-on-disconnect). Ähnlich `rebuild_index` für sehr große FTS5-Tabellen.
**Auswirkung:** UI hängt, Frontend zeigt Fehlermeldung obwohl der Rebuild noch läuft. Bei zwei parallelen Klicks doppelter Rebuild.
**Fix:** Als Background-Task mit Status-Tracking:
```python
@router.post("/system/maintenance/rebuild-vectors")
async def rebuild_vectors(...):
    if app.state.rebuild_in_progress:
        raise HTTPException(409, "Rebuild laeuft bereits")
    asyncio.create_task(_rebuild_vectors_bg(...))
    return {"started": True}
```
Plus Status-Endpoint und Anzeige im Frontend.

---

### H-5 — Tax-Export blockiert Event-Loop bei großen Archiven
**Datei:** `backend/app/api/tax.py:47-78` + `backend/app/services/tax_export_service.py:103-294`
**Problem:** `create_tax_export_zip()` ist eine reguläre `async def`, ruft aber rein synchron `zipfile.write()` (read+compress aus dem Archiv-Verzeichnis) und `_create_overview_pdf` (reportlab) auf. Bei 200 Dokumenten à 2 MB und ZIP_DEFLATED hängt der Event-Loop ~30 s — alle anderen API-Calls in der Zeit bekommen Timeouts.
**Auswirkung:** Während Export sind Suche/Upload tot.
**Fix:** Den ZIP-Build in `asyncio.to_thread`:
```python
zip_bytes = await asyncio.to_thread(_build_zip_sync, docs, settings, ...)
```
Daten vorher async laden, dann den IO-/CPU-Teil im Thread.

---

### H-6 — Filing-Scope-Löschung erzeugt Orphan-Dateien im Archiv
**Datei:** `backend/app/api/filing_scopes.py:140-181` + `archive_service.py:34-55`
**Problem:** Beim Löschen eines Scopes werden die Dokumente per UPDATE auf den Default-Scope umgehängt, aber die physischen Dateien bleiben unter `data/archive/{old_slug}/...`. `Document.file_path` zeigt weiterhin auf den alten Pfad — funktioniert für Download, aber Backup-Dump/Re-Indexierung zeigt einen Mischzustand. Der Scope-Slug ist nirgends mehr in der DB referenziert, sodass die Orphans nicht mehr aufgeräumt werden können.
**Auswirkung:** Speicher-Lecks, inkonsistenter Archive-Ordnerbaum, beim Edit eines Scope-Slugs (aktuell nicht erlaubt) wären die Dateien tot.
**Fix:** Bei Delete entweder die Dateien physisch in den Default-Scope-Ordner verschieben (rekursiv `shutil.move`) und `file_path` aktualisieren, oder Scope mit Dokumenten ablehnen (HTTP 400, „Erst Dokumente verschieben").

---

### H-7 — IMAP-Mailmove ohne EXPUNGE — Mails landen doppelt im verarbeiteten Ordner
**Datei:** `backend/app/services/email_fetch_service.py:271-277` (`_move_email`) + 137-151 (`_move_emails_sync`)
**Problem:** `conn.copy()` + `conn.store(num, "+FLAGS", "\\Deleted")` markiert die Mail nur zum Löschen, aber `expunge()` wird nie gerufen. Beim nächsten Polling-Lauf liefert `search UNSEEN` die Mails nicht mehr (sie sind als gelesen+deleted markiert), aber sie sind noch in INBOX sichtbar — und bei manchen IMAP-Servern (Gmail, Dovecot mit auto-expunge=off) tauchen sie nach Server-Restart wieder als UNSEEN auf, was zu Re-Verarbeitung führt. Glücklicherweise greift dann der Duplikat-Check via `message_id`, aber LLM-Calls (kostenintensiv) werden umsonst gemacht, weil Relevance-Check vor dem Duplikat-Check läuft (`email_fetch_service.py:200-217`).
**Auswirkung:** Verschwendete LLM-Calls, INBOX füllt sich mit „gelöschten" Mails.
**Fix:** `_move_emails_sync` nach der Schleife `conn.expunge()` aufrufen. Außerdem Reihenfolge in `fetch_emails_for_account` umdrehen: erst Duplikat-Check, dann Relevance-Check.

---

### H-8 — `update_document` triggert `MissingGreenlet` bei Tag-FTS-Update
**Datei:** `backend/app/api/documents.py:219-226`
**Problem:** Der Code liest `document.tags` direkt im async-Endpoint. `Document.tags` ist `lazy="selectin"` — funktioniert beim ersten Load, aber nach dem `await db.execute(text(...))` bei Zeile 222 ist der Lazy-Trigger in einem nicht-greenlet-fähigen Pfad. In der Praxis funktioniert es, weil selectin alle Tags vorab lädt; aber wenn das Document gerade geflushed/neu erstellt wurde und Tags noch nicht im Identity-Map gehängt sind, wird ein async-Refresh ausgelöst. Dazu kommt: das `Document` aus `db.execute(select)` lädt nur Document selbst, der selectin-loader fired nach dem `scalar_one_or_none()`. Beim FTS-Reindex wird dann auf `document.tags` zugegriffen und alles funktioniert — aber inkonsistent.
**Auswirkung:** Race in seltenen Fällen. Hauptsächlich Code-Klarheit: explizites `await db.refresh(document, ["tags"])` oder das Tags-Loading explizit machen.
**Fix:** Tag-Liste explizit per Query laden statt via Relationship:
```python
tag_names_result = await db.execute(
    text("SELECT t.name FROM document_tags dt JOIN tags t ON dt.tag_id = t.id WHERE dt.document_id = :id"),
    {"id": document_id},
)
tags_str = " ".join(r[0] for r in tag_names_result)
```

---

### H-9 — `add_tag_to_document` / `remove_tag_from_document` mutieren Relationship in async-Kontext
**Datei:** `backend/app/api/documents.py:366-403`
**Problem:** `document.tags.append(tag)` und `document.tags.remove(tag)` (Zeile 367, 395) operieren direkt auf der M2M-Relationship. Im CLAUDE.md steht explizit: „Tag-Zuweisung via Junction-Table … nicht über Relationship-Assignment, um MissingGreenlet in async-Kontext zu vermeiden." `archive_service.py:301-302` befolgt das mit `session.add(DocumentTag(...))`. Hier in `documents.py` wird das Pattern jedoch verletzt. Tests laufen durch (selectin lädt vorab), aber unter Last und im Worker-Kontext ist das Greenlet-Problem latent.
**Auswirkung:** Sporadische 500er bei Tag-Operationen unter Concurrency.
**Fix:** Stattdessen `session.add(DocumentTag(document_id=..., tag_id=...))` und symmetrisch `await db.execute(delete(DocumentTag).where(...))`.

---

### H-10 — Watch-Folder `on_created` triggert auf inkomplette Dateien beim mehrteiligen Copy
**Datei:** `backend/app/services/watch_folder_service.py:30-45`
**Problem:** Beim Drag-and-Drop großer Dateien (Windows kopiert mit Temp-Datei `.tmp`) feuert `on_created` zweimal — einmal für die Temp-Datei, einmal nach dem Rename. Der `_SETTLE_DELAY = 2.0`s ist hardcoded und reicht nicht für Mehr-GB-Dateien über langsame USB-Verbindungen. Das Resultat: Datei wird mit halbem Inhalt verarbeitet, OCR liefert Müll. Außerdem wird `file_path.stat().st_size` in `_handle_new_file` ohne `to_thread` synchron im Event-Loop aufgerufen (Zeile 51).
**Auswirkung:** Korrupte Pipeline-Eingaben bei großen Files.
**Fix:** Stabilität-Check loop: zweimal `stat()` mit 1s Pause, erst wenn Größe gleich → verarbeiten. Zusätzlich `on_modified` als Trigger und das stat-Call in `to_thread`.

---

### H-11 — `vectorize_document` arbeitet fehlerhaft mit `_chroma_delete_existing` bei großen Re-Indexes
**Datei:** `backend/app/services/vectorize_service.py:133-138`
**Problem:** `collection.get(where={"doc_id": doc_id})` ohne `limit` zieht die existierenden Chunks komplett. Bei großen Dokumenten (10000+ Chunks für ein Buch) lädt das alle Embeddings (768 dim × 4 Bytes × 10000 = 30 MB Memory) in den Backend-Prozess für einen einfachen Delete. ChromaDB unterstützt `collection.delete(where={"doc_id": doc_id})` direkt ohne vorheriges Get.
**Auswirkung:** Memory-Spike bei Rebuild großer Dokumente; OOM-Risiko.
**Fix:**
```python
def _chroma_delete_existing(settings, doc_id):
    collection = _get_collection_sync(settings)
    collection.delete(where={"doc_id": doc_id})
```

---

### H-12 — Chat-Pagination mit `offset > 0` liefert verwirrende Reihenfolge
**Datei:** `backend/app/api/chat.py:74-116`
**Problem:** Code lädt `limit` neueste Messages absteigend nach `created_at`, dann `reversed()` für chronologische Reihenfolge. Mit `offset=50` bekommt man die _zweitneuesten_ 50 Messages chronologisch, aber das passt nicht zu typischer Chat-UI-Pagination („ältere laden") — der Frontend würde diese als ältere Block am Anfang einfügen. Tatsächlich gibt es im Frontend gar keine Pagination, ChatView lädt nur die ersten 50 (Default). Bei vielen Nachrichten (>50) werden die ältesten im Frontend nie sichtbar.
**Auswirkung:** UX — bei vielen Chat-Messages verliert User den ältesten Verlauf.
**Fix:** Frontend: scroll-up triggert `loadHistory(offset += 50)` und prepend; oder Backend: `limit=ALL` per Default mit Soft-Cap 1000.

---

### H-13 — RAG-Service: `filing_scope_id`-Filter zieht Chunks nachträglich weg, kann zu 0 Ergebnissen führen
**Datei:** `backend/app/services/rag_service.py:50-93`
**Problem:** Bei Scope-Filter wird `top_k * 3` Chunks geholt und nachträglich auf den Scope gefiltert. Wenn die Top-15 zufällig alle aus „Privat" stammen aber der User den Scope „Praxis" gewählt hat, kommen 0 Chunks zurück und User sieht „Keine relevanten Dokumente in dem Bereich gefunden". Der RAG-Pipeline gehen relevante Chunks verloren, die in der `top_k * 3`-Auswahl nicht drin sind.
**Auswirkung:** RAG liefert false-negative Antworten bei Scope-Filter.
**Fix:** `filing_scope_id` als ChromaDB-Metadata-Filter direkt in `collection.query(where={"filing_scope_id": ...})`. Setzt voraus dass der Scope beim Vectorize gespeichert wird (`vectorize_service.py:200-217` speichert ihn aktuell **nicht**).

---

### H-14 — Rebuild-Vectors löscht alte Chunks nicht zuverlässig wenn Dokument-Set kleiner geworden ist
**Datei:** `backend/app/api/system.py:296-322` + `vectorize_service.py:152-229`
**Problem:** Rebuild iteriert über aktive Dokumente und ruft `vectorize_document` (welches per `_chroma_delete_existing` _nur die eigenen_ Chunks löscht). Wenn ein Dokument zwischenzeitlich gelöscht wurde aber `delete_document_vectors` damals fehlgeschlagen ist (z.B. weil ChromaDB down war), bleiben dessen Chunks beim Rebuild stehen — sie werden vom RAG weiterhin returnt, dann aber im `rag_service:75-83` als „doc not found" gefiltert. Resultiert in `chunks_found > 0` aber leerer Antwort.
**Auswirkung:** RAG-Resultate enthalten Geister-Dokumente.
**Fix:** Vor dem Rebuild komplettes `collection.delete()` in eine fresh collection (oder `client.delete_collection("documents")` + recreate).

---

### H-15 — Email-Scheduler `should_fetch_now` driftet bei langen Outages
**Datei:** `backend/app/services/email_scheduler_service.py:31-40`
**Problem:** Bei `CRON`-Schedule wird `croniter(account.cron_expression, account.last_checked_at)` verwendet — der nächste Lauf wird relativ zum _letzten erfolgreichen Check_ berechnet. Wenn der Backend-Container 3 Tage off war und das Cron `0 */1 * * *` (jede Stunde) lautet, wäre der nächste Lauf weit in der Vergangenheit — `should_fetch_now` triggert sofort _einen_ Fetch, danach steht `last_checked_at = jetzt`, der Cron schiebt sich also um 3 Tage. Die geplanten 72 stündlichen Fetches werden zu einem zusammengelegt — möglicherweise erwünscht, aber nicht offensichtlich. Bei `IDLE` (5min) das Gleiche.
Außerdem: croniter liefert naive datetime, der manuelle `replace(tzinfo=utc)` (Zeile 38-39) ist fragil — wenn die Cron-Expression timezone-sensitiv geschrieben ist (z.B. „0 8 * * *" für 8 Uhr Lokalzeit), wird sie hier als 8 Uhr UTC interpretiert.
**Auswirkung:** Cron läuft zu falschen Zeiten in Nicht-UTC-Zeitzonen; verpasste Schedules nach Outage.
**Fix:** Settings-Option für Timezone (`TZ` env), `croniter(expr, last_checked_at).get_next(datetime)` mit `tz=ZoneInfo(settings.TZ)`. Dokumentation: „cron läuft in UTC".

---

### H-16 — `archive_service`: ValueError bei FilingScope-Mismatch reisst Transaction
**Datei:** `backend/app/services/archive_service.py:185-403`
**Problem:** Wenn das Filing-Scope-Match (`_match_filing_scope`) `None` zurückgibt (kein Default-Scope vorhanden, leere FilingScope-Liste), werden Document und ggf. WarrantyInfo committed mit `filing_scope_id=None`. Das ist erlaubt (`Document.filing_scope_id` ist nullable), führt aber zu Dokumenten ohne Scope, die im Frontend „Kein Bereich" angezeigt bekommen — und in `tax_export` mit `filing_scope_id`-Filter sind sie unsichtbar.
**Auswirkung:** Daten-Inkonsistenz wenn jemand alle Filing Scopes löscht (technisch unmöglich wegen H-6, aber Migration 005 garantiert Default nur initial).
**Fix:** Bei `filing_scope_id is None` defensiv den ersten Scope nehmen oder eine Pflicht-Constraint: Migration `nullable=False` auf `Document.filing_scope_id` sobald migriert.

---

### H-17 — `optimize_db` (VACUUM) hält Schreib-Lock — alle Inserts blocken
**Datei:** `backend/app/api/system.py:273-281`
**Problem:** SQLite `VACUUM` wird als reguläre async-DB-Operation ausgeführt. Es kopiert die DB komplett um (kann minuten dauern bei 1+ GB DBs). Während VACUUM läuft sind keine Writes möglich — Queue-Worker, Watch-Folder, E-Mail-Scheduler hängen. Außerdem: VACUUM darf nicht in einer aktiven Transaktion laufen — `get_db` startet aber eine; je nach SQLAlchemy-Treiber wird das mit „cannot VACUUM from within a transaction" abgewiesen oder läuft mit unklarem Verhalten.
**Auswirkung:** System steht still während VACUUM, ggf. Fehlermeldung oder partielle Änderungen.
**Fix:** Eigener Connection-Path außerhalb Pool, `BEGIN; COMMIT; VACUUM;` oder besser per `sqlite3.connect()` direkt + `to_thread`.

---

### H-18 — `lazy="selectin"` auf zirkulärer Relationship `WarrantyInfo.document` ↔ `Document.warranty_info`
**Datei:** `backend/app/models/warranty_info.py:44` + `document.py:137-139`
**Problem:** Beide Seiten der OneToOne-Relation sind `selectin` — wenn `WarrantyInfo` geladen wird, lädt SQLAlchemy zusätzlich `WarrantyInfo.document`, was wiederum `Document.warranty_info` _nicht_ erneut lädt (Identity-Map), aber das `tags` und `review_questions` (selectin auf Document) werden eager mitgezogen. `GET /warranties` liefert für 50 Garantien dann 50 Document + alle Tags + alle ReviewQuestions als selectin — überproportional viele Queries für eine eigentlich schlanke Liste.
**Auswirkung:** Performance — `/api/warranties` ist langsamer als nötig.
**Fix:** `WarrantyInfo.document` auf `lazy="raise"` oder `lazy="select"` (nur wenn explizit zugegriffen) und in der API explizit nur `document.title`/`thumbnail_path` per Join laden.

---

### H-19 — Multi-File-Upload-Endpoint hat keine Fehler-Isolation pro Datei
**Datei:** `backend/app/api/documents.py:45-94`
**Problem:** Bei Upload von 10 Dateien gleichzeitig: wenn die 5. Datei eine `Exception` wirft (DB-Fehler, Disk voll), wird sie in `rejected` gesammelt — aber der `db`-Context ist über alle 10 geshared. Wenn der DB-Fehler die Session in einen Rollback-Status zwingt, schlagen die Dateien 6-10 stillschweigend fehl. Die 5 vorher schon erfolgreich eingereichten Jobs sind aber nicht committed (Commit erfolgt erst per FastAPI-Dependency am Ende), gehen also verloren.
**Auswirkung:** Bei großen Batch-Uploads partielle Verluste.
**Fix:** Pro Datei eigene Session, oder explizit nach jedem `process_upload` `await db.commit()` und `await db.begin()`.

---

## MEDIUM

### M-20 — `get_system_info()` blockiert Event-Loop mit `rglob`
**Datei:** `backend/app/services/backup_service.py:101-125` (called from `system_health` endpoint)
**Problem:** `Path.rglob("*")` mit Stat-Calls für alle Dateien läuft synchron im async Endpoint. Bei 10000+ Dateien im Archiv blockt der `/api/system/health`-Call mehrere Sekunden — und das wird in `SettingsView` alle 10s gepollt.
**Auswirkung:** Settings-Polling wirft jeden Worker minutenlang lahm.
**Fix:** `to_thread`, oder Größe nur beim Auto-Backup berechnen und cachen (`SystemSetting.archive_size_bytes`).

---

### M-21 — `manual_fetch` E-Mail commitet nicht — nichts wird gespeichert
**Datei:** `backend/app/api/email.py:119-130`
**Problem:** `fetch_emails_for_account` ruft `db.flush()` aber nie `db.commit()`. In der Standard-Pipeline mit `get_db` wird am Endpoint-Ende ein impliziter Commit gesetzt, aber `get_db` macht kein automatisches Commit (nur Cleanup). Beim Scheduler funktioniert es weil `email_scheduler_service.py:69` explizit `commit()`. Bei `manual_fetch` bleibt der Commit aus → Dryrun.
**Auswirkung:** „Jetzt abrufen"-Button im UI tut nichts persistent.
**Fix:** `await db.commit()` am Ende von `manual_fetch` oder generelles Commit in `get_db`.

---

### M-22 — Inkonsistente Pagination-Patterns über Endpoints
**Datei:** Mehrere
**Problem:** Verschiedene Endpoints nutzen unterschiedliche Pagination-Schemata:
- `/documents`: `page` + `page_size`, response mit `items/total/page/page_size` (PaginatedResponse-Schema)
- `/notifications`, `/email/accounts/{id}/history`: `limit` + `offset`, response: nacktes Array (kein total)
- `/warranties`: keine Pagination
- `/jobs`: `page` + `page_size` (konsistent)
- `/chat/history`: `limit` + `offset`, response mit `messages/total`
**Auswirkung:** Frontend muss drei Pagination-Pattern handhaben; OpenAPI-Schema chaotisch.
**Fix:** Einheitlich `page`/`page_size` mit `PaginatedResponse[T]`-Generic; Migration in Frontend einplanen.

---

### M-23 — DELETE-Endpoint-Responses inkonsistent
**Datei:** `backend/app/api/documents.py:231-262`, `email.py:96-103`, `filing_scopes.py:140-181`
**Problem:** DELETE für Document gibt `{"message": "...", "id": ...}`, DELETE für Filing-Scope gibt nur `{"message": ...}`, DELETE für E-Mail-Account gibt `204 No Content` (keine body). Frontend muss drei Cases behandeln.
**Auswirkung:** Code-Inkonsistenz, Schema-Doku unklar.
**Fix:** Vereinheitlichen auf 204 No Content für alle DELETEs.

---

### M-24 — Warranty-Reminder verpasst Tage bei Service-Outages
**Datei:** `backend/app/services/warranty_reminder_service.py:31-37`
**Problem:** Vergleicht `warranty_end_date == target_date` (exakter Tag). Wenn der Reminder-Service zwischen Tag X-1 und Tag X+1 down war, wird die 90-Tage-Erinnerung für eine Garantie, die genau an Tag X 90 Tage entfernt war, nie versendet.
**Auswirkung:** Verpasste Garantie-Reminder.
**Fix:** Range-Query: `warranty_end_date BETWEEN today + interval AND today + interval + lookback_days` mit Field-Check `reminder_*_sent IS FALSE`.

---

### M-25 — Frontend `DocumentDetailView` zeigt veraltete Daten nach Tag-Add
**Datei:** `frontend/src/views/DocumentDetailView.vue:113-122`
**Problem:** `handleAddTag` ersetzt `doc.value` durch das Response, aber `editForm` wird nicht resetted. Der User hat möglicherweise gerade `editForm.title` geändert, der Tag-Add überschreibt dann nicht den Form-State, sondern nur `doc.value` — Speichern danach committet die alte Server-Version + den Form-Title, und das Tag-Update wird auf dem Server zwar geschrieben, aber bei `saveChanges` wird dann der ältere Document-Stand mit `tags`-Array überschrieben (wenn `tags` im `updates` landet). Defensiver wäre `tags` aus `editForm` rauszuhalten.
**Auswirkung:** Race-bedingt verlorene Tag-Änderungen.
**Fix:** Tags-Logik aus `saveChanges` rauspatching: `editForm` enthält keine Tags.

---

### M-26 — `DocumentListItem` enthält `tags` aber Liste lädt sie via lazy="selectin" für jeden Doc
**Datei:** `backend/app/api/documents.py:118-172` + `schemas/document.py:110-130`
**Problem:** `/documents`-Liste pro Page-Size 25: SELECT documents (1 Query) + selectin Tags (1 Query) + selectin warranty (1 Query) + selectin review_questions (1 Query) + selectin filing_scope (1 Query) = 5 Queries pro Liste. Aber `DocumentListItem` braucht nur Tags und filing_scope. Für die anderen wird gequeryt aber nichts genutzt.
**Auswirkung:** Mehrere überflüssige Queries pro Listen-Aufruf.
**Fix:** `DocumentListItem`-Endpoint mit explizitem Loader-Override: `select(Document).options(selectinload(Document.tags), selectinload(Document.filing_scope))` + `noload` für Rest.

---

### M-27 — `formatAmount` toleriert `null` aber `formatDate` nicht — Inkonsistenz
**Datei:** `frontend/src/utils/formatters.js`
**Problem:** Schauen ob `formatAmount(null)` und `formatDate(null)` einheitlich behandeln. (Aus Quick-Inspektion in DocumentsView wird `formatAmount(doc.amount, doc.currency)` mit potenziell `null` aufgerufen; `formatDate(doc.document_date)` ebenso.) Wenn Test fehlt: silent NaN/Invalid-Date-Render.
**Auswirkung:** UI-Bugs bei Edge-Cases.
**Fix:** Beide Funktionen explizit auf `null/undefined` testen + Unit-Tests.

---

### M-28 — ScanView `switchCamera` startet ohne await — UI flackert
**Datei:** `frontend/src/views/ScanView.vue:61-65`
**Problem:** `switchCamera` ruft `stopCamera()` (synchron, OK) dann `startCamera()` ohne await. Die UI zeigt erst Camera-stopped, dann während Permission-Prompts kurz gar nichts, dann das neue Bild. Bei abgelehnter Permission stoppt's still.
**Auswirkung:** UX glitch.
**Fix:** `async function switchCamera` + `await startCamera()`.

---

### M-29 — `TaxView` zeigt Year-Selector mit „Alle Bereiche" auch ohne Daten
**Datei:** `frontend/src/views/TaxView.vue:74-77`
**Problem:** `selectedYear` ist standardmäßig das aktuelle Jahr. Wenn `years.value` leer ist (kein steuerrelevantes Doc je), zeigt der Selector das aktuelle Jahr (Fallback `<option v-if="!years.includes(selectedYear)">`) — User klickt Export → 400 „Keine steuerrelevanten Dokumente". Empty-State im Body sagt zwar das Richtige, aber der Export-Button bleibt anklickbar (`:disabled="!summary?.total_documents"` ist OK, aber dazwischen kann er klickbar sein während Loading).
**Auswirkung:** UX. Edge-Case akzeptabel aber unsauber.
**Fix:** Wenn `years.length === 0` → Hint „Keine Steuerbelege" + Export-Button ausblenden.

---

### M-30 — Dashboard-Polling resetiert `loadingData` auch wenn andere Calls noch laufen
**Datei:** `frontend/src/views/DashboardView.vue:22-60`
**Problem:** `getEmailStats().then(...)` läuft async ohne in `loadingData` einzubeziehen. Beim Polling-Trigger feuert eine neue `loadData`-Iteration, während die alte E-Mail-Promise noch hängt — es passiert kein Schaden, aber `emailStats.value` kann von einem alten Resolver überschrieben werden (last-resolved wins, statt last-fired).
**Auswirkung:** Selten flackernder E-Mail-Counter.
**Fix:** Ignore-old-result-Pattern (request-id) oder E-Mail-Stats in `Promise.all` aufnehmen.

---

## LOW

(Niedrige Prio, kurz aufgelistet — Kontextfehler-Resilienz, Code-Hygiene)

- **L-31** `_initial_vectorize` (`main.py:130-156`) startet ohne Lock — bei zweitem Backend-Start-Up während ChromaDB noch nicht ready ist, kann es doppelt laufen.
- **L-32** `_OrmProxy` in `schemas/document.py:55-66` ist clever aber unnötig — `model_validator(mode="before")` kann ein Dict zurückgeben statt eines Proxy. Cleaner Code, identische Funktion.
- **L-33** `database.py` hat `init_db` exportiert obwohl niemand mehr es nutzt — Dead Code (war in Vor-Review-Zustand markiert, aber Import noch in `main.py:25`).
- **L-34** `Notification`/`Warranty` haben kein Pagination, Liste wächst monoton — bei 1000+ Notifications wird `/notifications` langsam.
- **L-35** `chat.py:46-63` schreibt User+Assistant in einer Transaktion mit `created_at` 1ms apart — bei sehr schnellem Senden zweier Fragen kollidiert die Sortierung. Sub-Microsekunden-Auflösung wäre besser.
- **L-36** `frontend/src/views/ChatView.vue:43-83` hat keine Cancel-Function für laufende Frage — wenn User die Frage versehentlich abschickt, kann er sie nicht abbrechen, blockiert UI für Timeout.

---

## Zusammenfassung & Empfehlung

**Sofort fixen (BLOCKER + HIGH-1..3):**
- **B-1** `analysis.issuer` → `analysis.sender` + Test
- **B-2** PROCESSING-Recovery beim Lifespan-Startup
- **B-3** Hash-Kollision: Status `FAILED` statt `NEEDS_REVIEW`, Datei nicht löschen
- **H-9** Tag-Operationen via `DocumentTag`-Junction (CLAUDE.md-Pattern verletzt)
- **H-7** IMAP `expunge()` + Reihenfolge Duplikat→Relevance

**Mittelfristig:**
- **H-4 / H-5 / H-17** Lange Operationen (Vector-Rebuild, Tax-Export, VACUUM) als Background-Tasks mit Status-Tracking
- **H-13 / H-14** RAG-Pipeline: Filing-Scope als ChromaDB-Metadata, Rebuild als Drop+Recreate
- **H-10** Watch-Folder-Stability-Check

**Refactor / Hygiene:**
- M-22 + M-23 Pagination & DELETE-Konsistenz
- M-24 Warranty-Reminder mit Range-Query
- M-26 Listen-Endpoints mit gezieltem Loader-Override

**Test-Lücken (kritisch):**
- Re-Analyse Happy-Path (B-1 wäre detected)
- Queue-Worker Crash-Recovery
- IMAP-Disconnect mid-Fetch
- Filing-Scope-Delete mit existierenden Dokumenten
- ChromaDB-Down während Vectorize
