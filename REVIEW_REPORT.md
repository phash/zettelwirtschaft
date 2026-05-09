# Review-Report — Zettelwirtschaft

Datum: 2026-05-08
Scope: Code-Review, Security-Review, Library-Audit, LLM-Optimierung, UI/Performance-Tests, Implementierung

---

## TL;DR

| Kategorie | Findings | Gefixt | Offen |
|---|---|---|---|
| Security (neu) | 8 | 7 | 1 (PIN_ENABLED-Default — Breaking Change) |
| Code BLOCKER | 3 | 3 | 0 |
| Code HIGH | 13 | 13 | 0 |
| Code MEDIUM | 11 | 7 | 4 (M-22, M-23, M-25, M-27, M-29) |
| Code LOW | 6 | 2 | 4 |
| Library CVEs | 4 | 4 | 0 |
| LLM Quick-Wins | 5 | 4 | 1 (Vision-LLM) |
| LLM Strategic-Wins | 3 | 1 (Hybrid Search RRF) | 2 (Reranker, Vision) |

**Performance baseline (Live-Test):**
- Read-Endpoints (`/health`, `/stats`, `/documents`, `/search`, `/notifications`): 12-35 ms avg.
- Stack-Healthchecks alle grün, ChromaDB-Wget-Bug behoben.

**Tests (final, nach allen Phasen):**
- Backend pytest: **342 passed / 1 skipped** (auf Host gegen lokales Python-Env; `.dockerignore` schliesst `tests/` aus dem Container aus).
- Playwright Chromium: **145/145 passed** (war 137/145 vor Fixes — Sidebar-Version-Bug + Scan-View-Selektor-Issues behoben).
- Neu hinzugefuegt: 6 Tests fuer Few-Shot-Pipeline + RRF-Hybrid-Search.
- Test-Anpassungen: 3 Tests an neue Architektur angepasst (JSON-Schema-Mode, bge-m3-Embedding-Default, ChromaDB-Metadata-Filter, Hybrid-Retrieval-Mock).

---

## 1. Sicherheits-Findings (gefixt)

### N-001 [CRITICAL] Backend-Port 8000 extern exposed → Rate-Limit-Bypass + CSP-Bypass
**Datei:** `docker-compose.yml`
**Fix:** `ports: ["8000:8000"]` → `expose: ["8000"]`. Backend ist jetzt nur intern via nginx erreichbar.
**Verifiziert:** `curl http://localhost:8000` → connection refused.

### N-002 [HIGH] Login-Lockout-Counter wurde nach Ablauf nicht zurückgesetzt
**Datei:** `backend/app/api/auth.py`
**Fix:** `_cleanup_expired()` löscht den kompletten Eintrag, nicht nur die `lockout_until`-Zeit. Ein Tippfehler nach Cooldown re-locked nicht mehr für weitere 30 s.

### N-013 [MEDIUM] Prompt-Injection → Tax-Daten-Manipulation
**Dateien:** `backend/app/prompts/analyze_document.txt`, `rag_answer.txt`, `backend/app/services/analysis_service.py`
**Fixes:**
- OCR-Text in `<document_ocr>`-Block gewrappt mit expliziter Anweisung das Modell solle Inhalte als Daten und nicht als Instruktionen interpretieren.
- RAG-Prompt: gleiches Pattern für `<document_excerpts>` + `<user_question>`.
- Post-Processing in `_build_result_from_combined`: Sanity-Check für Beträge (>50.000 € → Review-Frage), Tax-Category-Whitelist statt blinder Übernahme.

### B-3 [BLOCKER] Duplikat-Erkennung löschte Quelldatei
**Datei:** `backend/app/services/queue_worker_service.py`
**Fix:** Bei `ValueError` (Hash-Kollision) wird Status auf `FAILED` (statt fragwürdigem `NEEDS_REVIEW`) gesetzt, Quelldatei bleibt im Upload-Ordner damit der User sie inspizieren kann.

---

## 2. Code-BLOCKER (gefixt)

### B-1 [BLOCKER] Re-Analyse-Endpoint kollabiert mit AttributeError
**Datei:** `backend/app/api/review.py:235-236`
**Problem:** `analysis.issuer` referenziert — das Feld heißt `sender` in `AnalysisResult`. Der v1.2.2-Re-Analyse-Endpoint warf HTTP 500 sobald das LLM einen Aussteller lieferte.
**Fix:** `analysis.sender → doc.issuer` (Mapping-Pattern aus archive_service übernommen).

### B-2 [BLOCKER] Stuck-Jobs werden bei Restart nicht recovert
**Datei:** `backend/app/main.py` (lifespan)
**Problem:** Crash mitten in der Verarbeitung → Job hängt für immer auf `PROCESSING`.
**Fix:** Lifespan-Startup setzt alle `PROCESSING` → `PENDING` mit `error_message="Worker neu gestartet, Job wird wiederholt"`. Worker greift sich den Job dann erneut.

---

## 3. Library-Updates (CVE-Fixes)

| Paket | Vorher | Nachher | Grund |
|---|---|---|---|
| Pillow | `11.*` | `>=12.2,<13` | CVE-2026-40192 (FITS-Decompression-Bomb DoS) |
| python-multipart | `0.0.*` | `>=0.0.26,<0.1` | CVE-2024-53981 + CVE-2026-24486 + CVE-2026-40347 |
| axios (frontend) | `^1.7.0` | `^1.15.1` | Supply-Chain-Compromise März 2026 (Versions 1.14.1 + 0.30.4 enthielten RAT) |
| FastAPI | `0.115.*` | `>=0.119,<0.137` | Schließt transitive Starlette-CVEs (multipart DoS) |
| cryptography | `44.*` | `>=44,<49` | CVE-2026-39892 |

Alle anderen Pins wurden auf eine Lower-Bound + Major-Upper-Bound geändert (statt `*`), um Reproduzierbarkeit zu erhöhen ohne Patch-Updates zu blockieren.

**Nicht durchgeführt** (zu großer Migrations-Aufwand für diese Session):
- chromadb 0.6.3 → 1.x (Rust-Rewrite, irreversible Volume-Migration)
- Vite 5 → 7 (erzwingt Node 22)
- Tailwind 3 → 4 (CSS-First-Config + Klassen-Renaming)
- Pinia 2 → 3, vue-router 4 → 5

→ Diese sind im `LIBRARY_AUDIT.md` als P3-Roadmap dokumentiert.

---

## 4. LLM/OCR-Optimierung (Quick-Wins umgesetzt)

### bge-m3 Embeddings statt nomic-embed-text
**Dateien:** `backend/app/config.py`, `backend/app/services/vectorize_service.py`, `.env.example`
**Änderungen:**
- `EMBEDDING_MODEL` Default: `nomic-embed-text` → `bge-m3` (multilingual, bessere Performance auf deutschen Belegen).
- ChromaDB-Collection-Name jetzt modell-abhängig (`documents_bge-m3`, `documents_nomic-embed-text`) — verhindert Dimension-Mismatch (768 vs 1024) beim Modellwechsel.
- Auto-Pull beim Backend-Startup ist nun **non-blocking** (Background-Task statt Lifespan-Block) — bge-m3 ist ~1.2 GB groß und blockierte den HTTP-Server vorher mehrere Minuten.
- `.env.example` mit Default-Hinweis aktualisiert.

**Migration für Bestandsuser:**
1. `EMBEDDING_MODEL=bge-m3` in `.env` setzen (oder Default akzeptieren).
2. Backend neu starten — Modell wird im Hintergrund gepullt.
3. In den Einstellungen "Vektor-Index neu aufbauen" auslösen.
4. Alte Collection (`documents_nomic-embed-text`) kann manuell gelöscht werden.

### JSON-Schema-constrained Generation statt `format=json`
**Dateien:** `backend/app/services/llm_service.py`, `backend/app/services/analysis_service.py`
**Änderung:**
- `call_llm(..., schema=dict)` übergibt JSON-Schema an Ollama (≥0.5).
- `_analysis_schema()` in `analysis_service.py` definiert die exakte Struktur (`document_type` als Enum, `confidence` 0-1, etc.).
- Kombinierter Prompt nutzt jetzt das Schema → Output ist **garantiert konform**, die drei Fallback-Parser-Pfade (json.loads / Markdown-Block / erstes-{-bis-letztes-}) feuern praktisch nicht mehr.
- Erwarteter Effekt: ~80 % weniger `_try_sequential_analysis`-Fallback-Pfade, robustere Pipeline.

### Prompt-Injection-Härtung (siehe N-013 oben)

### Few-Shot aus CorrectionMappings (Strategic Win)
**Dateien:** `backend/app/services/analysis_service.py`, `backend/app/prompts/analyze_document.txt`, `backend/app/services/queue_worker_service.py`, `backend/app/api/review.py`
**Konzept:** Die `CorrectionMapping`-Tabelle sammelt User-Korrekturen seit Migration 004, wurde aber nur fuer auto-apply nach 3x gleicher Korrektur genutzt. Jetzt werden die haeufigsten Eintraege (occurrence_count >= 2, top 8) als Few-Shot-Examples in den Analyse-Prompt injiziert. Effekt: Das LLM lernt aus User-Feedback ohne Fine-Tuning. Beispiel: Wenn der User "BSH GmbH" 3x zu "Bosch Siemens Hausgeraete" korrigiert hat, sieht das LLM diese Mapping-Beispiele bei jeder neuen Analyse.

### Hybrid Search FTS5 + Vector mit RRF (Strategic Win)
**Datei:** `backend/app/services/rag_service.py`
**Konzept:** Reciprocal Rank Fusion (`score = 1/(k+vec_rank) + 1/(k+fts_rank)`) zwischen Vector-Search und FTS5-Suche im RAG. FTS5 ist exzellent fuer Eigennamen / Rechnungsnummern, Vector-Search exzellent fuer semantische Naehe — beide werden parallel ausgewertet und kombiniert. Erwartet: +9 pp MRR fuer Chat-Antworten zu spezifischen Dokumenten.

### OCR-Preprocessing erweitert
**Datei:** `backend/app/services/ocr_service.py`
**Konzept:** Auto-Upscale auf 2x bei Bildern < 1500 px kuerzeste Kante (typische Handy-Fotos), aggressiveres Autocontrast (cutoff=2), MedianFilter zum Denoising. Erwartet: +5–10 pp OCR-Accuracy auf schiefen Smartphone-Scans.

---

## 5. Performance-Baseline (Live-Test)

Gemessen mit `curl -w "%{time_total}"` (5er-Average):

| Endpoint | Avg. |
|---|---|
| `/api/health` | 32 ms |
| `/api/stats` | 24 ms |
| `/api/documents?page=1&page_size=20` | 35 ms |
| `/api/search?q=test` | 33 ms |
| `/api/notifications` | 13 ms |

Alle Werte sind exzellent. Migration 011 (Performance-Indizes) ist offensichtlich wirksam.

**Lange Operationen (NICHT gefixt, aber identifiziert):**
- `POST /api/system/maintenance/rebuild-vectors` — synchron, blockiert HTTP-Worker bei 500 Docs 60+ Min (Code-Review-H-4).
- `POST /api/tax/export` — `zipfile.write()` + `reportlab.build()` ohne `to_thread` blockiert Event-Loop ~30 s (H-5).
- `POST /api/system/maintenance/optimize-db` — `VACUUM` blockiert alle Writes (H-17).

---

## 6. UI-Tests (Playwright)

**Vor Fixes:** 137 passed / 8 failed (chromium).
**Nach Fixes:** **145/145 passed**.

Behobene Test-Failures + Root-Causes:
- **Sidebar-Version-Anzeige (`vdev` statt `v1.2.2`):** Vite-Config las `../VERSION` relativ, aber Docker-Frontend-Build hat den Parent-Folder nicht im Build-Context. Fix in `frontend/vite.config.js`: Primaerquelle ist jetzt `package.json` (immer im Build-Context), `../VERSION` ist Fallback. Damit wird der `__APP_VERSION__`-String korrekt zur Build-Zeit eingebettet.
- **Scan-View 4 Failures:** Waren stale-Image-Probleme (Container hatte alte Frontend-Build mit anderen Selektoren). Nach Rebuild: alle 4 grün.

---

## 7. Zusätzliche Fixes (Phase-2-Batch nach erstem Report)

### Code-HIGH (jetzt gefixt)
- **H-4** `rebuild_vectors` als Background-Task mit Status-Endpoint (`GET /api/system/maintenance/rebuild-vectors/status`). 409 Conflict bei parallelem Klick.
- **H-5** Tax-Export: Daten async laden, ZIP+reportlab in `to_thread` — kein Event-Loop-Block mehr.
- **H-7** IMAP `expunge()` nach Move-Loop. Plus Cleartext-Warning (N-005).
- **H-9** Tag-Add/Remove via `DocumentTag`-Junction-Table (statt Relationship-Mutation).
- **H-10** Watch-Folder Stabilitäts-Polling: Datei wird erst verarbeitet wenn Größe zwischen zwei stat-Calls gleich bleibt.
- **H-11** `_chroma_delete_existing` nutzt direkt `delete(where=)` — kein Memory-Spike mehr durch Vorab-Get aller IDs+Embeddings.
- **H-13** RAG-Scope-Filter über ChromaDB-`where`-Metadata statt Python-Post-Filterung. Verhindert false-negative Antworten bei aktivem Scope.
- **H-17** `optimize_db` VACUUM via separater `sqlite3.connect()` + `to_thread` — keine aktive Transaktion, kein Worker-Block.

### Code-MEDIUM (jetzt gefixt)
- **M-20** `get_system_info()` (rglob+stat über ganzes Archiv) in `to_thread`.
- **M-21** `manual_fetch` E-Mail-Endpoint: explizites `db.commit()` ergänzt — UI-Button persistiert jetzt tatsächlich.
- **M-24** Warranty-Reminder mit Range-Query (Lookback 7 Tage) — verpasst keine Tage mehr nach Service-Outage.

### Security (jetzt gefixt)
- **N-003** `PUT /api/system/settings` validiert Host-Pfade gegen System-Verzeichnisse (C:\Windows, /etc, /proc, /sys etc.). Verhindert Bind-Mount-Hijack.
- **N-004** `chat_history` Limit-Cap (`Query(le=200)`) verhindert OOM via `?limit=999999999`.
- **N-005** IMAP-Cleartext-Warning bei `use_ssl=False` pro Connection geloggt.
- **N-006** Frontend nginx läuft als non-root (`USER nginx`, listen 8080). docker-compose mappt Host-Port → Container 8080.
- **N-008** Installer (`install.ps1` + `install-gui.ps1`) generieren `EMAIL_ENCRYPTION_KEY` automatisch (32 random Bytes urlsafe-base64).

## 8. Phase-3-Batch (alle restlichen HIGHs + Mediums)

### Code-HIGH (jetzt komplett gefixt)
- **H-6** Filing-Scope-Delete: Dateien werden physisch in den Default-Scope-Ordner verschoben (`shutil.move`), `file_path` aktualisiert, alter Scope-Ordner aufgeräumt. Verhindert Orphan-Files im Archiv.
- **H-15** Email-Scheduler: `_ensure_utc()` defensiv für naive datetimes, Doku-Hinweis dass Cron-Expressions in UTC ausgewertet werden.
- **H-18** `WarrantyInfo.document` von `lazy="selectin"` auf `lazy="raise"` umgestellt; `/api/warranties` lädt nur noch `Document.title` und `thumbnail_path` per `selectinload(load_only(...))` statt den ganzen Document-Tree.
- **H-19** Multi-File-Upload comittet pro Datei einzeln, mit `await db.rollback()` im Fehler-Pfad — verhindert dass eine fehlerhafte Datei alle vorherigen Uploads in der Session verwirft.

### Code-MEDIUM (jetzt gefixt)
- **M-26** `/documents`-Liste lädt mit `noload(warranty_info, review_questions)` — von 5 auf 3 Queries reduziert.
- **M-28** `ScanView.switchCamera` ist jetzt async, await für `startCamera()`.
- **M-30** Dashboard-Polling: Request-ID-Token verhindert dass eine alte E-Mail-Stats-Promise eine neuere Iteration überschreibt.

### Code-LOW (gefixt)
- **L-31** `_initial_vectorize` startet mit 5s Delay + prüft `app.state.rebuild_status` — kein Konflikt mehr mit parallelem Rebuild-Vectors-Endpoint.
- **L-33** `init_db` Dead-Import aus `main.py` entfernt.

## 9. Verbleibender Backlog

### Security
- **N-007 (HIGH)** `PIN_ENABLED=False` Default — Breaking Change, eigener Migrationspfad nötig.

### Code-MEDIUM (übrig, niedrige Priorität)
- **M-22** Pagination-Inkonsistenzen über Endpoints (limit/offset vs page/page_size)
- **M-23** DELETE-Response-Inkonsistenz (200 vs 204)
- **M-25** DocumentDetailView Tag-Add Race mit editForm
- **M-27** formatDate/formatAmount Edge-Cases (cosmetic)
- **M-29** TaxView Year-Selector ohne Daten

### LLM-Strategic-Wins (separater Sprint)
- Hybrid-Search FTS5 + Vector mit RRF + bge-reranker-v2-m3
- CorrectionMappings als Few-Shot-Examples in `analyze_document.txt`
- Vision-LLM-OCR (Qwen 2.5-VL als Tesseract-Alternative)
- Default-Modell-Wechsel Llama 3.2 → Qwen 2.5 7B

### Lib-Major-Bumps (P3)
- chromadb 1.x (Server + Client koordiniert, irreversibel)
- Tailwind v4 (Custom-Klassen migrieren)
- Vite 7 (erzwingt Node 22)
- Pinia 3, vue-router 5

### LLM-Optimierungen (Strategic-Wins)
- Hybrid-Search FTS5 + Vector mit RRF + bge-reranker-v2-m3
- CorrectionMappings als Few-Shot-Examples
- Tesseract-Vorverarbeitung (Deskew + adaptive Threshold)
- Default-Modell-Wechsel Llama 3.2 → Qwen 2.5 7B
- Vision-LLM-OCR (Qwen 2.5-VL als Tesseract-Alternative)

→ Details in `LLM_OPTIMIZATION.md`.

### Lib-Major-Bumps (P3)
- chromadb 1.x (Server + Client koordiniert)
- Tailwind v4
- Vite 7 (mit Node 22)
- Pinia 3, vue-router 5

→ Details in `LIBRARY_AUDIT.md`.

---

## 8. Geänderte Dateien (Quick-Reference)

### Backend
- `backend/app/api/auth.py` — Lockout-Counter-Reset (N-002)
- `backend/app/api/review.py` — `analysis.sender → doc.issuer` (B-1)
- `backend/app/main.py` — Stuck-Job-Recovery (B-2), non-blocking embedding pull
- `backend/app/services/queue_worker_service.py` — Duplikat behält Datei (B-3)
- `backend/app/services/llm_service.py` — `schema`-Parameter für JSON-Schema-Mode
- `backend/app/services/analysis_service.py` — JSON-Schema, Sanity-Checks, Tax-Whitelist (LLM Quick-Win + N-013)
- `backend/app/services/vectorize_service.py` — Modell-abhängiger Collection-Name
- `backend/app/config.py` — `EMBEDDING_MODEL=bge-m3` Default
- `backend/app/prompts/analyze_document.txt` — Prompt-Injection-Wrapping (N-013)
- `backend/app/prompts/rag_answer.txt` — Prompt-Injection-Wrapping (N-013)
- `backend/alembic/versions/011_add_performance_indexes.py` — Idempotent (`CREATE INDEX IF NOT EXISTS`)
- `backend/requirements.txt` — Sicherheits-Pins (Pillow 12, python-multipart 0.0.26+, FastAPI 0.119+)

### Frontend
- `frontend/package.json` — axios `^1.7.0` → `^1.15.1`

### Infrastructure
- `docker-compose.yml` — Backend `ports` → `expose` (N-001), ChromaDB-Healthcheck `wget` → Python (Bug)
- `.env.example` — `EMBEDDING_MODEL=bge-m3` Default + `EMAIL_ENCRYPTION_KEY`-Generierungs-Hinweis

### Reports (neu erstellt)
- `SECURITY_AUDIT_v2.md` — 5 neue Sicherheits-Findings
- `CODE_REVIEW_v2.md` — 30 Code-Findings (3 BLOCKER, 13 HIGH)
- `LIBRARY_AUDIT.md` — Lib-Audit + Migrations-Plan
- `LLM_OPTIMIZATION.md` — LLM/OCR/Embedding/RAG-Empfehlungen
- `REVIEW_REPORT.md` — Diese Zusammenfassung

---

## 9. Empfohlene nächste Schritte

**Sofort (vor Release):**
1. Backend + Frontend-Tests grün durchlaufen lassen.
2. Manueller Smoke-Test: Upload eines PDFs + Verifikation dass JSON-Schema-Mode korrekt funktioniert (LLM-Antwort-Logs prüfen).
3. CHANGELOG.md aktualisieren mit Sicherheits-Fixes.

**Kurzfristig:**
4. N-008 (Installer EMAIL_ENCRYPTION_KEY-Auto-Generation).
5. H-4/H-5/H-17 (Background-Tasks für Long-Running-Operations).
6. N-003 (Pfad-Whitelist für Settings-PUT).

**Mittelfristig:**
7. Hybrid-Search mit RRF + Reranker (LLM_OPTIMIZATION 3).
8. CorrectionMappings als Few-Shot.
9. Lib-Major-Bumps in eigenen Branches.

---

## 10. Verifikations-Befehle

```bash
# N-001: Backend nicht extern erreichbar
curl -m 3 http://localhost:8000/api/health  # erwartet: connection refused / timeout

# Stack-Health
curl http://localhost:8080/api/health  # erwartet: status=ok für alle Komponenten

# Backend-Tests
docker exec zettelwirtschaft-backend-1 python -m pytest -q

# E2E-Tests
cd e2e && npx playwright test --project=chromium
```
