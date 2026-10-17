# Code Review Findings — Zettelwirtschaft v1.2.2

Datum: 2026-03-29

---

## CRITICAL

- [x] **#1** `.env` aus Backup-ZIP entfernen (`backup_service.py`) — enthaelt PIN + Encryption Key
- [x] **#2** `EMAIL_ENCRYPTION_KEY` leer → `HTTPException(503)` statt stiller Key-Generierung (`api/email.py:41-45`)
- [x] **#3** Blocking IMAP in async Context — alle IMAP-Calls in `asyncio.to_thread()` wrappen (`email_fetch_service.py`)
- [x] **#4** Path Traversal bei Backup-Download — `resolve()` + `is_relative_to()` Check (`api/system.py:256-267`)
- [x] **#5** FK-Typ-Mismatch `EmailAccount.filing_scope_id` Integer → String(36) (`email_account.py:37`) + Migration 010
- [x] **#6** Rate-Limiting auf PIN-Login — Per-IP Cooldown nach 5 Fehlversuchen + `secrets.compare_digest` (`api/auth.py`)

## HIGH

- [x] **#7** SQL ORDER BY ohne Whitelist in Search-Endpoint — `pattern=` Constraint hinzugefuegt (`api/search.py`)
- [x] **#8** E-Mail-Anhaenge Magic-Byte-Validierung hinzugefuegt (`email_fetch_service.py`)
- [x] **#9** File Move → Copy+Delete nach Commit — Rollback-sicher (`archive_service.py`, `queue_worker_service.py`)
- [x] **#10** `_Proxy`-Klasse → shared `_OrmProxy` mit `__slots__`, Duplikation entfernt (`schemas/document.py`)
- [x] **#11** `call_llm`/`call_llm_text` → shared `_call_ollama` Helper (`llm_service.py`)
- [x] **#12** Double-Commit entfernt aus `review.py`, `warranties.py`, `notifications.py`, `jobs.py`
- [x] **#13** `rebuild_fts_index` gibt jetzt `int` zurueck (`search_service.py`)
- [x] **#14** `datetime.utcnow()` → `datetime.now(timezone.utc)` (`review.py`)
- [x] **#15** Untyped `dict` → `AnswerRequest` Pydantic-Schema (`review.py`)
- [x] **#16** Dead Review-Endpoints aus `documents.py` entfernt + Dead SQL in search_service entfernt
- [x] **#17** Route-Param-Watch fehlt — `DocumentDetailView` zeigt alte Daten bei Navigation
- [x] **#18** Dashboard-Polling kann stacken — Guard gegen parallele `loadData` Calls
- [x] **#19** SettingsView God-Component — in Sub-Components aufteilen
- [x] **#20** Raw Enum-Werte in UI — Labels statt `HANDWERKER_RECHNUNG` (`DocumentsView`, `DocumentDetailView`, `SearchView`)
- [x] **#21** `formatDate` 8x dupliziert — shared Utility erstellen
- [x] **#22** Umlaute inkonsistent — ae/oe/ue → ae/oe/ue in UI-Strings (`EmailAccountForm`, `UploadView`, `ScanView`, `ConfirmDialog`)

## MEDIUM

- [x] **#23** PIN-Vergleich → `secrets.compare_digest()` (bereits in #6 gefixt)
- [x] **#24** CORS → explizite Origins + LAN-Regex statt `["*"]` (`main.py`)
- [x] **#25** Nginx Security-Headers hinzugefuegt: CSP, X-Frame-Options, X-Content-Type-Options, server_tokens off (`nginx.conf`)
- [x] **#26** `init_db()` aus main.py lifespan entfernt — Schema via Alembic
- [ ] **#27** Inkonsistente Pagination — Warranties + Notifications ohne Paginierung
- [ ] **#28** Inkonsistente DELETE-Responses — vereinheitlichen
- [x] **#29** Inkonsistentes Logger-Naming — auf `__name__` vereinheitlichen
- [x] **#30** Auth-Store Kommentar warum `axios` direkt (`stores/auth.js`)
- [x] **#31** Notification-Dropdown schliesst bei Outside-Click (`AppHeader.vue`)
- [x] **#32** Toast-Container `bottom-20 lg:bottom-4` fuer Mobile (`ToastContainer.vue`)
- [x] **#33** Dynamischer `<title>` pro Seite via `router.afterEach` (`router/index.js`)
- [x] **#34** `file_path` aus `DocumentResponse` entfernt (`schemas/document.py`)
- [x] **#35** DB-Indizes hinzugefuegt: Migration 011 (`Notification.is_read`, `ProcessingJob.created_at/status`, `ReviewQuestion.is_answered`)
- [x] **#36** ChromaDB Healthcheck → HTTP heartbeat (`docker-compose.yml`)
- [x] **#37** E-Mail-Body `.txt` — intern erzeugt, Kommentar hinzugefuegt

## MINOR

- [x] **#38** `documentTypes` Array → shared Constant (in #20 gefixt)
- [ ] **#39** Icon SVG-Paths in Sidebar + BottomNav dupliziert — niedrige Prio, skip
- [x] **#40** `getDocumentFileUrl`/`getThumbnailUrl` Dead Code entfernt (`api.js`)
- [x] **#41** `package.json` Version → 1.2.2
- [ ] **#42** Fehlende `aria-label` auf Icon-only Buttons — niedrige Prio, skip
- [x] **#43** `ScanView` HTTP/HTTPS Erkennung + Umlaute gefixt
- [x] **#44** `_sessions` Dict Cleanup bei Login hinzugefuegt
- [x] **#45** `sender` vs `issuer` Naming-Inkonsistenz dokumentiert (`analysis_service.py`)
- [x] **#46** Filing Scope keyword JSON-Parsing → `parsed_keywords` Property
- [x] **#47** Identity-Replacements in `generate_slug` entfernt (`filing_scope.py`)
- [x] **#48** Dead no-op Query entfernt (`filing_scopes.py`)
- [x] **#49** CSV BOM Encoding Roundtrip vereinfacht (`tax_export_service.py`)
- [x] **#50** Warranty Stats → SQL COUNT mit `case()` (`warranties.py`)
- [x] **#51** Email Stats → Single GROUP BY Query (`api/email.py`)
- [ ] **#52** `reanalyze_document` dupliziert Pipeline-Logik — akzeptierter Trade-off
- [ ] **#53** Backend Dockerfile System-Packages — akzeptierter Trade-off
- [x] **#54** `.dockerignore` erstellt fuer backend + frontend
- [x] **#55** Ollama Dependency → `service_healthy` (`docker-compose.yml`)
