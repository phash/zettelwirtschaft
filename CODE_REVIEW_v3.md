# Code-Review v3 — Stand 2026-05-09

Fokus: Neue Patterns aus Phase 2-5 (LLM-Reranker, Verifier-Pass, slowapi, model_validator-Refactor, Lib-Major-Bumps Pinia 3 / Vite 6 / Chromadb 1.x).

Skala: BLOCKER (Release verhindern) / HIGH / MEDIUM / LOW.

Zeilenangaben sind exakt zum Ist-Stand. Findings sind nach Severity und Wirkung sortiert.

---

## BLOCKER

### B-01 — `migrate.py` ruft weiterhin `init_db()` (create_all) VOR `alembic upgrade`
**Datei:** `backend/migrate.py:62-79, 121-131` + `backend/app/database.py:36-38`

CLAUDE.md behauptet "init_db() entfernt (nur Alembic)" — Code zeigt das Gegenteil. `migrate.py.main()` ruft erst `asyncio.run(create_schema())` auf, was alle Tabellen via `Base.metadata.create_all` anlegt; **danach** `fix_alembic_version()` und `alembic upgrade head`.

**Auswirkung:**
- Auf einer Frisch-Installation legt `create_all` das Schema vom Stand der Modelle an, ohne Alembic-Tracking. `fix_alembic_version` findet dann z.B. `email_accounts` (vom create_all gerade frisch erzeugt) und stempelt auf `009_add_email_accounts` — **die Performance-Indizes 011 und der FK-Fix 010 werden nicht angewandt** und auch nicht via `upgrade head` nachgezogen, weil die Detection-Reihenfolge nur bis 009 geht.
- Bei zukünftigen Migrationen, die Felder umbauen (z.B. Spalte droppen), können `create_all` (legt sie an) und `alembic upgrade` (versucht sie zu droppen) gegeneinander laufen.
- Doppeltes Schema-Setup verlängert Container-Start.

**Fix:** `create_schema()` + Aufruf in `main()` entfernen. `fix_alembic_version` so anpassen, dass eine fehlende `alembic_version`-Tabelle ohne erkennbare Legacy-Datenbank `alembic stamp head` macht (oder gar nichts → Alembic legt initiale Tabellen via Migrations selbst an, sofern `001` `op.create_table` für `documents` etc. enthält — verifizieren).

---

### B-02 — ChromaDB 0.6.3 → 1.0.20: kein Volume-Migrationspfad, alter Container-Path geändert
**Datei:** `docker-compose.yml:43-60` + `backend/app/services/vectorize_service.py`

Der Kommentar im Compose lautet: "Default-persist_path hat sich von `/chroma/chroma` auf `/data` geaendert. Volume-Format ist bei Major-Bump nicht abwaerts-kompatibel — Backup vor Update Pflicht." — aber:

- Der Pfad wird nur per Mount auf `/data` geändert, **es gibt keine Logik die alte Daten aus `/chroma/chroma` migriert**. Bei Bestandsuser geht der Vector-Index 1:1 verloren.
- `update.bat` macht laut CLAUDE.md ein DB-Backup, aber **kein** ChromaDB-Volume-Backup. Nach dem Bump ist der Volume-Inhalt unbrauchbar (Format-Inkompat).
- Es gibt keinen Auto-Reindex-Trigger. `_initial_vectorize` läuft nur bei `count == 0` — im neuen 1.x-Container wird ein leeres Volume reflektiert, also läuft Reindex automatisch — gut. **Aber** wenn `update.bat` das alte Volume mountet und 1.x den alten 0.6.3-Datenstand liest, gibt's Lese-Fehler statt einen sauberen Reindex.

**Auswirkung:** Datenverlust bei Update auf v1.3 (oder welche Version den Bump bringt). Bestandsuser müssen manuell `docker volume rm zettelwirtschaft_chromadb-data` machen, sonst stiller Fehler.

**Fix:**
1. In `update.bat` einen Schritt einfügen, der bei detektiertem chromadb-Major-Bump das Volume rotiert (alt umbenennen, neu erstellen).
2. Im Backend Startup-Code: ChromaDB-Heartbeat mit Versions-Check; bei alter Datenstruktur loggen + automatisch Reindex starten.
3. Release-Notes / `update.bat` muss explizit warnen.

---

## HIGH

### H-03 — slowapi Default 200/min wendet sich auch auf `/api/health` an → Docker-Healthcheck kann gerate-limited werden
**Datei:** `backend/app/main.py:223-241, 252`

Die PIN-Middleware white-listed `/api/health`, der slowapi-Limiter NICHT. Healthcheck läuft alle 30s vom Container und dem ChromaDB-Healthcheck — bei mehreren Clients im LAN + Frontend-Polling alle 10s + Dashboard-Polling alle 3s kommt ein einzelner ngx-Reverse-Proxy-IP schnell an die 200/min, und ab da liefert `/api/health` 429 → docker-compose markiert Backend als unhealthy → Auto-Restart-Loop. Frontend-Polling sieht plötzlich keine Stats mehr.

**Fix:** Limiter-Exemption für `/api/health` und idealerweise `/api/auth/status`:
```python
@app.middleware("http")
async def skip_rate_limit_on_health(request, call_next):
    if request.url.path in ("/api/health", "/api/auth/status"):
        request.state._rate_limit_exempt = True
    return await call_next(request)
```
Oder: Health-Endpoint nicht mit dem globalen Limiter, sondern per `@limiter.exempt` markieren.

---

### H-04 — slowapi `key_func` liest `X-Real-IP` ohne Prüfung → Spoof-bar wenn jemand direkt das Backend aufrufen kann
**Datei:** `backend/app/main.py:232-238`

`_client_id` greift unkonditional `request.headers.get("X-Real-IP")`. Im Heim-LAN ist das Backend nur via nginx erreichbar — **aber** `expose: "8000"` macht den Port docker-compose-Netzwerk-intern erreichbar; und das Backend hört auf `0.0.0.0`. Wenn jemand direkten Zugriff auf das Backend kriegt (via `curl` aus dem Backend-Container, via Misconfig im Compose, via Docker-Desktop-Port-Forward), kann er pro Request einen anderen `X-Real-IP` setzen → Rate-Limit ist effektiv aus. Gleicher Issue wie auth.py:78 (auch nicht gefixt).

**Auswirkung:** Defense-in-Depth-Annahme bricht. Im normalen Betrieb harmlos, aber das ist genau der Use-Case der Limiter abdeckt.

**Fix:** `X-Real-IP` nur akzeptieren wenn Request-IP in einer Whitelist liegt (Docker-Bridge, localhost). Sonst fallback auf socket-peer.

---

### H-05 — `_llm_rerank`: bei `len(scores) != len(chunks)` werden ALLE Chunks zurückgegeben, nicht auf `target_k` gestutzt
**Datei:** `backend/app/services/rag_service.py:106-107`

```python
if not scores or len(scores) != len(chunks):
    return chunks
```
Gibt `chunks` zurück, nicht `chunks[:target_k]`. Wenn `chunks` z.B. 15 Einträge hat (RAG_TOP_K=5 × 2 wegen Reranker-aware Pfad in Z.191), bekommt das Antwort-LLM 15 statt 5 Kontext-Chunks. Mehr Kontext, höhere Latenz, schlechtere Antwortqualität (Lost-in-the-Middle).

**Fix:** `return chunks[:target_k]` in beiden Fallback-Pfaden (Z.103, 107, 118 ist schon korrekt).

---

### H-06 — `_llm_rerank`: Score-Validierung akzeptiert negative Werte und Werte > 10
**Datei:** `backend/app/services/rag_service.py:108-113`

Schema sagt `minimum: 0, maximum: 10`, aber Ollama-Schema-Constrainment ist im LLama/Qwen-Backend nicht garantiert hart durchgesetzt (v.a. nicht für Items in Arrays mit numerischen Bounds — Ollama selbst weist darauf hin dass nicht alle Schema-Features 100% enforced sind). Code macht nur:
```python
c["_rrf_score"] = (c.get("_rrf_score") or 0) + (float(s) / 10.0)
```
Ein LLM-Score von z.B. `-5` oder `99` schraubt den RRF-Score komplett kaputt — kein Clip. Auch `s` als String wird zwar mit `float(s)` versucht aber zwingt dann via `try/except (TypeError, ValueError)` zu **silent skip** — der betreffende Chunk behält den alten RRF-Score, andere kriegen LLM-Boost. Sortierung ist dann inkonsistent.

**Fix:**
```python
try:
    score = float(s)
    if score != score or score < 0 or score > 10:  # NaN/out-of-range
        continue
    c["_rrf_score"] = (c.get("_rrf_score") or 0) + score / 10.0
except (TypeError, ValueError):
    continue
```

---

### H-07 — `_verify_analysis`: keine Tests vorhanden + None-Fields werden unsauber rendered
**Datei:** `backend/app/services/analysis_service.py:340-347`

`summary_lines = [f"amount = {analysis.amount} {analysis.currency or ''}", ...]` ergibt bei `amount=None`, `currency=None` den String `"amount = None "`. Das Verifier-LLM bekommt das als Validation-Input und kann darauf "issue: amount ist None" generieren — was als Review-Question dem Endnutzer angezeigt wird, obwohl es ja absichtlich null ist (nicht alle Belege haben Beträge, z.B. amtliche Schreiben).

Außerdem gibt es **in `tests/services/test_analysis_service.py` keinen einzigen Test** für `_verify_analysis` (verifiziert via Grep — 0 Treffer für `verify_analysis`). Fallback-Pfad bei LLM-Fehler ungetestet.

**Fix:**
1. None-Felder im Summary überspringen oder als "(nicht erkannt)" rendern.
2. Tests: Issues=[], Issues=["..."], LLM-Fehler-Pfad, Threshold-Edge-Case (`confidence == LLM_VERIFIER_THRESHOLD`).

---

### H-08 — Verifier-Threshold: `confidence < threshold` — bei `confidence == 0.5` kein Verifier
**Datei:** `backend/app/services/analysis_service.py:528`

```python
if settings.LLM_USE_VERIFIER and analysis.confidence < settings.LLM_VERIFIER_THRESHOLD:
```
Default-Threshold 0.5. Wenn das LLM `confidence: 0.5` zurückgibt (passiert oft als "default unsicher"-Wert), wird der Verifier **nicht** ausgelöst. Die ganze Idee des Verifiers ist ja "bei niedriger Konfidenz validieren" — Genau-Schwellenwerte sollten inklusiv sein.

**Fix:** `<=` statt `<`. Oder Threshold dokumentieren als "exklusiv".

---

### H-09 — `_resolve_scope_name` model_validator: bricht bei ORM-Objekten ohne `__dict__`
**Datei:** `backend/app/schemas/document.py:62-72`

```python
if hasattr(data, "__dict__"):
    fields = list(cls.model_fields.keys())
    out = {}
    for f in fields:
        if f == "filing_scope_name":
            fs = getattr(data, "filing_scope", None)
```
Problem 1: ORM-Objekte mit `__slots__` haben kein `__dict__`. Aktuell verwendet das Projekt das nicht, ist aber eine zukünftige Stolperstelle.

Problem 2: `getattr(data, "filing_scope", None)` triggert in async-SQLAlchemy potenziell **Lazy-Load außerhalb des Sessions-Kontexts** → `MissingGreenlet`. Document-Modell hat zwar `lazy="selectin"` für `filing_scope`, aber nur wenn das Document mit `selectinload` oder via Query mit eingerichtetem Mapping geladen wurde. Bei Round-Trips über `model_validate(orm_object)` außerhalb der ursprünglichen Query (z.B. nach einem `await session.refresh(doc)` ohne explizites Loader) bricht das.

**Fix:** Catch `MissingGreenlet` / `StatementError` und nimm `None`. Oder erzwinge im DocumentResponse-Erzeugungs-Code immer `selectinload(Document.filing_scope)`.

---

## MEDIUM

### M-10 — `analysis_service.py` 557 Zeilen — über Schwelle, sollte aufgeteilt werden
**Datei:** `backend/app/services/analysis_service.py` (gesamt)

10 Sub-Funktionen + 2 Schemas + Konstanten + Pipeline-Orchestrator in einer Datei. Gemischte Concerns: Sanitizing (`_sanitize_amount`), JSON-Parsing (`_parse_analysis_json`), Few-Shot-Building (`_format_correction_examples`, `_load_correction_examples`), LLM-Calls (`_try_combined_analysis`, `_try_sequential_analysis`, `_verify_analysis`), Result-Building (`_build_result_from_combined`).

**Fix:** Splitten:
- `analysis/result.py` → AnalysisResult, _build_result_from_combined, _sanitize_amount
- `analysis/parser.py` → _parse_analysis_json, _truncate_text, schema-Defs
- `analysis/few_shot.py` → _format_correction_examples, _load_correction_examples, _format_filing_scopes
- `analysis_service.py` → analyze_document + _try_*, _verify_analysis

Gleiche Priorität für `rag_service.py` (286 Zeilen — noch ok, aber wenn ein zweiter Reranker-Modus dazukommt, splitten).

---

### M-11 — chromadb 1.x: `where`-Filter-Syntax kompatibilitäts-prüfen
**Datei:** `backend/app/services/vectorize_service.py:154, 283, 290`

```python
collection.delete(where={"doc_id": doc_id})
collection.query(... where={"filing_scope_id": filing_scope_id})
```
Die Kurzform `{"key": "value"}` ist in chromadb 1.x noch unterstützt aber als Shortcut für `{"key": {"$eq": "value"}}`. Die explizite Form ist robuster und in 1.x als kanonisch dokumentiert. Mit Server 1.0.20 + Client 1.5.9 ist das Mismatch-Risiko erhöht (Client-Server-API-Drift möglich).

**Fix:** Auf explizite Form migrieren:
```python
where={"doc_id": {"$eq": doc_id}}
where={"filing_scope_id": {"$eq": filing_scope_id}}
```
+ Smoke-Test gegen die **gepinnte** Server-Version laufen lassen (CI tut das nicht heute — chromadb läuft im Compose, aber Backend-Tests mocken vectorize komplett).

---

### M-12 — chromadb 1.x: `HttpClient` ohne `tenant`/`database` arg → Default-Tenant-Annahme
**Datei:** `backend/app/services/vectorize_service.py:21-24`

chromadb 1.x führt Multi-Tenant ein, default-tenant ist "default_tenant". Bei späterem Volume-Restore aus Backup oder Migration über mehrere Instanzen kann sich das beißen. Aktuell unkritisch (Single-Tenant-Setup), aber wenn `chromadb-data`-Volume mal manipuliert wurde (z.B. durch CLI-Tool, das `--tenant` setzt), findet der Client die Collection nicht mehr.

**Fix:** Explizit `tenant="default_tenant", database="default_database"` setzen — defensiv.

---

### M-13 — `_initial_vectorize` startet ALLE Dokumente sequentiell ohne Concurrency-Limit
**Datei:** `backend/app/main.py:172-202`

Bei 1000 archivierten Belegen = 1000 sequentielle `vectorize_document` mit Embedding-Calls über Ollama. Auf Heim-Hardware (8 GB RAM, kein GPU) kann das **>1 Stunde** dauern und blockiert Ollama für reguläre LLM-Calls (gleiche Engine). Es gibt **kein** Heartbeat-Logging des Fortschritts (nur das Start- und End-Log).

**Fix:**
1. Progress-Log pro 50 Docs.
2. Asyncio-Semaphore für max 1 Vectorize gleichzeitig (jetzt schon implizit via to_thread sync, aber Ollama-Embedding-Calls sind async und stacken trotzdem).
3. `_initial_vectorize` skippen oder Banner setzen wenn ein Re-Index gerade explizit läuft (war eigentlich der Sinn von `rebuild_status`-Check, aber das wird beim Cold-Start initial nie gesetzt sein — Race wenn der User direkt nach Start "Reindex" drückt).

---

### M-14 — `RAG_USE_RERANKER` und `LLM_USE_VERIFIER` nicht in `.env.example` dokumentiert
**Datei:** `.env.example` (alle Zeilen)

Code hat 3 neue Settings (RAG_USE_RERANKER, LLM_USE_VERIFIER, LLM_VERIFIER_THRESHOLD), `.env.example` zeigt davon **null**. Standard ist `False`/`0.5` — User wissen nicht dass es die Optionen gibt. CLAUDE.md erwähnt sie nur en-passant als Phase-4-Wins.

**Fix:** Block in `.env.example` ergänzen mit Erklärung "verdoppelt Latenz pro Call, aber bessere Genauigkeit".

---

### M-15 — `slowapi` `default_limits=["200/minute"]` ohne Burst-Toleranz für legitime Nutzung
**Datei:** `backend/app/main.py:238`

Dashboard pollt alle 3s bei aktiven Jobs (CLAUDE.md), Notifications alle ~15s, Settings-Health alle 10s. Auf einem aktiven Browser-Tab + Mobile-PWA + Watch-Folder-Auto-Verarbeitung erreicht das Backend leicht ~50 req/min vom selben Client. Ein User der noch UploadView öffnet und Suche tippt addiert weitere ~20 req/min. Bei zwei Familienmitgliedern = ~140 req/min im normalen Betrieb. 200/min ist nicht großzügig.

**Fix:** Auf `500/minute` setzen, oder per-Endpoint differenzieren (`/api/auth/login` strikt, `/api/documents/*` großzügig, `/api/jobs/*` sehr großzügig).

---

### M-16 — `_apply_rrf` mutiert `chunks`-Dicts (`chunk["_rrf_score"] = ...`) — Side-Effect für Caller
**Datei:** `backend/app/services/rag_service.py:135-141`

Die Funktion gibt zwar eine sortierte Kopie zurück, aber sie modifiziert die Eingabe-Chunks in-place (sie kriegen alle ein neues Feld `_rrf_score`). Wenn ein Test oder zukünftiger Caller dieselbe Chunk-Liste danach verwendet, ist sie "dirty". Aktuell nur in `ask_question` verwendet, aber eine versteckte Falle. `_llm_rerank` macht es genauso.

**Fix:** `chunk = {**chunk, "_rrf_score": score}` oder dokumentieren dass die Mutation Absicht ist.

---

### M-17 — `auth_login`: doppeltes Rate-Limit (slowapi default + In-Memory) — slowapi-Counter wird auch bei erfolgreichen Logins inkrementiert
**Datei:** `backend/app/api/auth.py:68-107` + `backend/app/main.py:238`

Erfolgreiche Logins zählen gegen die `200/min`-Quote. Ein Mobile-PWA, das nach Reconnect das Status-Endpoint prüft + Login + bekommt 200 — alles wird gezählt. Im normalen Familienbetrieb harmlos, aber gegen Brute-Force-Bot **schwächer als der dedizierte Auth-Limiter** weil 200/min einen großen Brute-Force-Korridor lässt.

Außerdem: das eigene Auth-Rate-Limit (5 Versuche, 30s Lockout) zählt auch erfolgreiche Logins **nicht** zurück → korrekt. Aber nach Lockout wird der Counter NIE freigegeben außer durch `_cleanup_expired` (das entfernt nur expired Lockouts). Bei 4 Fehlversuchen in 50 Minuten = 4 Counter, der 5. Versuch lockt SOFORT, obwohl die ersten 4 längst "vergessen" sein sollten. Kein Reset-Window.

**Fix:** Zeitfenster für Fail-Counter (z.B. nach 5 Min ohne Versuch reseten).

---

### M-18 — Pinia 3 Migration: Stores nutzen Composition-API-Pattern, sollte safe sein, aber kein Migration-Test
**Datei:** `frontend/src/stores/{documents,auth,notifications}.js`

Stores definieren als Setup-Store (`defineStore('name', () => {...})`), das ist in Pinia 3 **breaking-frei**. Allerdings: Pinia 3 entfernt `pinia.use(plugin)` für einige Plugins, und SSR-related APIs. Da das Projekt keine SSR macht und keine externen Plugins, OK.

**Aber:** Es gibt **keinen Frontend-Test** der auch nur einen Store instanziiert — die E2E-Tests prüfen API-Mocks, nicht Pinia-Verhalten. Wenn ein Bug in Pinia 3 die Reaktivität bricht, wird's erst beim manuellen Klick sichtbar.

**Fix:** Mindestens 1 Vitest-Test pro Store (set/get/reset). Niedrige Priorität bis das Projekt sich ein Test-Setup für Frontend leistet.

---

### M-19 — `.dockerignore` für Frontend: `dev-dist/` (vite-plugin-pwa 1.x output) nicht ausgeschlossen?
**Datei:** Nicht überprüft, aber relevant nach vite-plugin-pwa 0.20→1.3

vite-plugin-pwa 1.x ändert das Output-Verzeichnis-Verhalten in dev (`dev-dist/`). Wenn das im Dockerfile-Build-Context landet, bläht es das Image auf.

**Fix:** Verifizieren dass `dev-dist/` und `dist/` in `.dockerignore` stehen.

---

## LOW

### L-20 — `_format_correction_examples` und `_format_filing_scopes` haben dieselbe Struktur — DRY
**Datei:** `backend/app/services/analysis_service.py:253-284`

Beide bauen einen Header-Line + Bullet-List. Ein gemeinsamer `_format_bullet_list(header, items, formatter)`-Helper wäre lesbarer.

---

### L-21 — `_check_chromadb_reachable` synchroner httpx-Call wird 2x aufgerufen pro Vectorize
**Datei:** `backend/app/services/vectorize_service.py:181, 188, 242`

`vectorize_document` macht: heartbeat-check → delete → add. 3 separate Connections. Mit chromadb 1.x sollte besser ein einziger persistenter `HttpClient` pro Vectorize-Pass verwendet werden. Aktuell wird in jedem Helper neu `_get_chroma_client` aufgerufen → 3 TCP-Handshakes.

**Fix:** Client einmal pro `vectorize_document`-Call erzeugen, an Helpers durchreichen.

---

### L-22 — `LLM_VERIFIER_THRESHOLD` als float in Settings ohne Validierung
**Datei:** `backend/app/config.py:55`

Wenn ein User `LLM_VERIFIER_THRESHOLD=1.5` in `.env` setzt, läuft der Verifier IMMER (jede Confidence < 1.5). Pydantic-Validator fehlt.

**Fix:** `LLM_VERIFIER_THRESHOLD: float = Field(0.5, ge=0.0, le=1.0)`

---

### L-23 — `_llm_rerank` lädt `import json as _json` lokal — minimal Performance-Hit
**Datei:** `backend/app/services/rag_service.py:104` + `analysis_service.py:360`

Local-import-Pattern ist OK aber inkonsistent — top of file hat schon `import json` nicht. Ein `import json` am File-Anfang wäre cleaner.

---

### L-24 — `_verify_analysis` Issues-Cap auf 5 ist hardcoded
**Datei:** `backend/app/services/analysis_service.py:361`

`return list(_json.loads(raw).get("issues", []))[:5]` — wenn der Verifier 10 echte Issues findet, bekommt der User nur 5 angezeigt. Bei einem komplexen, schlecht erkannten Beleg ist das ärgerlich.

**Fix:** Cap erhöhen auf 10 oder als Setting auslagern (`LLM_VERIFIER_MAX_ISSUES`).

---

### L-25 — `chunk["_rrf_score"]` als String-Schlüssel mit Underscore-Prefix lecken in API-Response durch?
**Datei:** `backend/app/services/rag_service.py` (mehrfach) + `backend/app/api/chat.py`

`ask_question` returned chunks mit dem internen `_rrf_score` möglicherweise nicht direkt — Sources werden nur aus `source_doc_ids` erstellt. Aber wenn ein zukünftiger Refactor mal die kompletten Chunks zurückgibt, leckt internes Scoring an Frontend.

**Fix:** Entweder `_rrf_score` aus den finalen Chunks vor Return entfernen, oder umbenennen zu `__rrf_score` (private dict-Konvention) plus Pop am Ende von `ask_question`.

---

# TOP-10 Findings (Zusammenfassung)

| # | Severity | Datei | Kurz |
|---|---|---|---|
| B-01 | BLOCKER | migrate.py:62-79 | `init_db()` läuft VOR alembic → Migrations 010+011 werden auf Frisch-Installs übersprungen |
| B-02 | BLOCKER | docker-compose.yml:43-60 | chromadb 0.6→1.0.20 ohne Volume-Migrationspfad → Datenverlust beim Update |
| H-03 | HIGH | main.py:223-241 | slowapi 200/min greift auf `/api/health` → Healthcheck wird gerate-limited → unhealthy-Loop |
| H-04 | HIGH | main.py:232 | `X-Real-IP` ungeprüft akzeptiert → Rate-Limit per Header umgehbar |
| H-05 | HIGH | rag_service.py:106-107 | Reranker-Mismatch-Fallback gibt ALLE Chunks zurück statt top-k → Lost-in-the-Middle |
| H-06 | HIGH | rag_service.py:108-113 | Score-Validierung fehlt (negative/NaN/range) → kaputte Reranker-Sortierung |
| H-07 | HIGH | analysis_service.py:340-347 | `_verify_analysis` rendert None-Felder als String "None"; keine Tests vorhanden |
| H-08 | HIGH | analysis_service.py:528 | Verifier-Threshold-Vergleich `<` statt `<=` → Edge-Case 0.5 triggert nicht |
| H-09 | HIGH | schemas/document.py:62-72 | model_validator kann MissingGreenlet werfen wenn `filing_scope` nicht eager-loaded |
| M-10 | MEDIUM | analysis_service.py | Datei mit 557 Zeilen + 10 Sub-Funktionen, gemischte Concerns — sollte gesplittet werden |

Test-Coverage-Lücken:
- 0 Tests für `_llm_rerank` (Fallback-Pfade, Score-Mismatch, LLM-Fehler)
- 0 Tests für `_verify_analysis` (Issues=[], Issues=[...], LLM-Down, Threshold-Edge)
- 0 Tests für slowapi Rate-Limit-Trigger
- Pinia-3-Stores ungetestet
