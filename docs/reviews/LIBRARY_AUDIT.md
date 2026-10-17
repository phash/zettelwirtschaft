# Library Audit & Migrations-Plan — Zettelwirtschaft

**Stand:** 2026-05-08
**Geprueft:** Backend (Python/FastAPI), Frontend (Vue 3), E2E (Playwright), Docker (ollama, chromadb, nginx, python, node)
**Quellen:** PyPI, GitHub Releases, npm, Snyk, NVD, GitHub Advisory DB

---

## 1. Bestandsuebersicht (Tabelle)

### Backend (`backend/requirements.txt`)

| Name              | Current (Pin) | Latest 2026 | Risk   | Breaking | Notes                                                                       |
|-------------------|---------------|-------------|--------|----------|-----------------------------------------------------------------------------|
| fastapi           | 0.115.*       | 0.136.x     | MEDIUM | Yes      | Strict Content-Type-Check fuer JSON, Starlette 1.0+ noetig                  |
| uvicorn[standard] | 0.34.*        | 0.46.x      | LOW    | No       | Aktuelle Stable; HTTP/2-Verbesserungen                                      |
| starlette         | (transitiv)   | 1.0.x       | HIGH   | Yes      | **CVE-2025-54121 + CVE-2025-62727** (multipart DoS) in 0.39-0.49           |
| sqlalchemy[async] | 2.0.*         | 2.0.49      | LOW    | No       | Patch-Update; 2.1 noch in Beta                                              |
| aiosqlite         | 0.20.*        | 0.21.x      | LOW    | No       | Minor-Update                                                                |
| alembic           | 1.14.*        | 1.18.4      | LOW    | No       | Mehrere Minor-Versionen Verzug, aber kompatibel                             |
| pydantic-settings | 2.7.*         | 2.13.x      | LOW    | Minor    | Pydantic v2.13 stable; FieldInfo-Refactor in 2.12                           |
| pydantic          | (transitiv)   | 2.13.3      | LOW    | Minor    | Polymorphic_serialization neu; FieldInfo-Refactor                           |
| httpx             | 0.28.*        | 0.28.1      | LOW    | No       | Aktuell; SSL-API-Vereinfachung                                              |
| python-multipart  | 0.0.*         | 0.0.26      | HIGH   | No       | **CVE-2026-24486 + CVE-2024-53981** in <0.0.22; aktuelle Pin laesst <0.0.18 zu! |
| python-dotenv     | 1.1.*         | 1.1.x       | LOW    | No       | Aktuell                                                                     |
| watchdog          | 6.*           | 6.x         | LOW    | No       | Aktuell                                                                     |
| Pillow            | 11.*          | 12.2.0      | HIGH   | Minor    | **CVE-2026-40192** (FITS DoS) in 10.3-12.1.1; in 12.2.0 gefixt              |
| pdf2image         | 1.17.*        | 1.17.x      | LOW    | No       | Aktuell                                                                     |
| pytesseract       | 0.3.*         | 0.3.x       | LOW    | No       | Aktuell                                                                     |
| pdfplumber        | 0.11.*        | 0.11.x      | LOW    | No       | Aktuell                                                                     |
| reportlab         | 4.2.*         | 4.5.0       | LOW    | No       | Minor-Update                                                                |
| chromadb-client   | 0.6.*         | 1.x         | MEDIUM | Yes      | **Major-Bump 1.0** (Rust-Rewrite, Auth-API-Changes, irreversible Migration) |
| cryptography     | 44.*          | 48.x        | HIGH   | Minor    | **CVE-2026-39892** (Buffer Overflow) in 45.0.0-46.0.6; in 46.0.7+ gefixt    |
| croniter          | 6.*           | 6.x         | LOW    | No       | Aktuell                                                                     |
| pytest            | 8.*           | 8.x         | LOW    | No       | Aktuell                                                                     |
| pytest-asyncio    | 0.25.*        | 0.25.x      | LOW    | No       | Aktuell                                                                     |

### Frontend (`frontend/package.json`)

| Name                | Current     | Latest     | Risk   | Breaking | Notes                                                                |
|---------------------|-------------|------------|--------|----------|----------------------------------------------------------------------|
| vue                 | ^3.5.0      | 3.5.34     | LOW    | No       | 3.6 noch in Beta (Vapor-Mode); 3.5.x aktuell stable                  |
| vue-router          | ^4.4.0      | 4.5.x      | LOW    | No       | Minor-Update                                                         |
| pinia               | ^2.2.0      | 2.3.x      | LOW    | No       | Minor-Update                                                         |
| axios               | ^1.7.0      | 1.15.1     | HIGH   | No       | **CVE-2026-40175 + CVE-2025-62718** SSRF/Cloud-Metadata; supply-chain compromise (1.14.1, 0.30.4 BAD) |
| vite                | ^5.4.0      | 7.x / 8.x  | MEDIUM | Yes      | Vite 7 braucht Node 20.19+/22.12+, Default-Browser-Target geaendert  |
| @vitejs/plugin-vue  | ^5.1.0      | 5.x / 6.x  | LOW    | No       | Mit Vite 7 mitziehen                                                 |
| vite-plugin-pwa     | ^0.20.0     | 0.21.x     | LOW    | No       | Unterstuetzt Vite 3-8                                                |
| tailwindcss         | ^3.4.0      | 4.x        | MEDIUM | Yes      | **Major v4** — Rust-Engine, CSS-first @theme, neue PostCSS-Plugins   |
| postcss             | ^8.4.0      | 8.5.x      | LOW    | No       | Aktuell                                                              |
| autoprefixer        | ^10.4.0     | 10.4.x     | LOW    | No       | Bei Tailwind v4 nicht mehr noetig                                    |

### E2E (`e2e/package.json`)

| Name              | Current  | Latest  | Risk | Breaking | Notes                          |
|-------------------|----------|---------|------|----------|--------------------------------|
| @playwright/test  | ^1.49.0  | 1.59.1  | LOW  | No       | 10 Minor-Versionen Verzug      |
| @types/node       | ^25.3.3  | 25.x    | LOW  | No       | Aktuell                        |
| typescript        | ^5.7.0   | 5.8.x   | LOW  | No       | Minor-Update                   |

### Infrastructure

| Komponente          | Current      | Latest          | Risk   | Notes                                                            |
|---------------------|--------------|-----------------|--------|------------------------------------------------------------------|
| python (Docker)     | 3.12-slim    | 3.13.13 / 3.14  | LOW    | 3.13 stable; 3.14 ebenfalls released; Sprung optional            |
| node (Docker)       | 20-alpine    | 22-alpine       | LOW    | Node 20 LTS bis 2026-04 EOL; Node 22 LTS empfohlen               |
| nginx (Docker)      | alpine       | 1.30-alpine     | LOW    | Tag `alpine` zieht aktuelle Version automatisch                  |
| ollama              | latest       | latest          | LOW    | Tag `latest` (Empfehlung: auf Version pinnen)                    |
| chromadb            | 0.6.3        | 1.x             | MEDIUM | **Major-Bump 1.0**, irreversible DB-Migration                    |

---

## 2. CVE-Liste sortiert nach Severity

### KRITISCH

1. **axios — Supply-Chain-Compromise (Maerz 2026)**
   - Versionen `1.14.1` und `0.30.4` enthielten kompromittierte Payloads (RAT).
   - Aktuelle Pin `^1.7.0` ist NICHT betroffen, *koennte* aber bei `npm install` ohne lockfile-Disziplin spaeter ziehen.
   - **Action:** Auf `axios@1.15.1` pinnen. Lockfile pruefen.

### HIGH

2. **CVE-2026-40175 (axios)** — Cloud-Metadata-Exfiltration via SSRF. Fix: 1.15.0+.
3. **CVE-2025-62718 (axios)** — NO_PROXY-Bypass SSRF. Fix: 1.15.0+.
4. **CVE-2026-39892 (cryptography)** — Buffer Overflow bei non-contiguous buffers in `Hash.update()`. Affected: 45.0.0-46.0.6. Fix: 46.0.7+. (Aktueller Pin `44.*` ist davor — daher *nicht* direkt betroffen, aber Upgrade auf 48.x bleibt sinnvoll fuer langfristige Wartung.)
5. **CVE-2026-40192 (Pillow)** — FITS-Decompression-Bomb (DoS). Affected: 10.3.0-12.1.1. Aktueller Pin `11.*` ist betroffen. Fix: 12.2.0.
6. **CVE-2025-54121 (starlette)** — Multipart-Parser blockiert Event-Loop bei grossen Files. Affected: <=0.47.1 (transitiv via FastAPI 0.115). Fix: 0.47.2+.
7. **CVE-2025-62727 (starlette)** — Multipart parts ohne filename ohne Size-Limit. Affected: 0.39.0-0.49.0. Fix: 0.49.1+ (in Starlette 1.0).
8. **CVE-2026-24486 (python-multipart)** — Path-Traversal bei `UPLOAD_DIR + UPLOAD_KEEP_FILENAME=True`. Affected: <0.0.22. Aktueller Pin `0.0.*` ohne Lower-Bound — **gefaehrlich**. Fix: 0.0.22+.
9. **CVE-2024-53981 (python-multipart)** — DoS via grosser Daten vor erstem Boundary. Affected: <0.0.18. Fix: 0.0.18+.
10. **CVE-2026-40347 (python-multipart)** — DoS via Preamble/Epilogue. Fix: 0.0.26+.

### MEDIUM

11. **chromadb-client 0.6 vs Server 1.x** — Kein direkter CVE, aber wenn Server-Image gebumpt wird: Auth-API-Breaking-Changes, geaenderte `list_collections`-Result-Order, irreversible DB-Migration.

### LOW (informativ)

- Tailwind v4: kein CVE, aber browser-Mindestanforderung Safari 16.4+ / Chrome 111+ (PWA-Kontext beachten).

---

## 3. Empfohlener Migrations-Plan (3 Stufen)

### P1 — SOFORT (Security-Patches, kein Breaking)

Diese Updates schliessen aktive CVEs und sind weitgehend drop-in.

| Library          | Von        | Ziel       | Begruendung                                          |
|------------------|------------|------------|------------------------------------------------------|
| Pillow           | 11.*       | 12.2.*     | CVE-2026-40192 (FITS DoS)                           |
| python-multipart | 0.0.*      | 0.0.26     | CVE-2026-24486, CVE-2024-53981, CVE-2026-40347      |
| axios            | ^1.7.0     | ^1.15.1    | CVE-2026-40175, CVE-2025-62718, supply-chain        |
| @playwright/test | ^1.49.0    | ^1.59.0    | Bugfixes, neue Locator-API                          |
| reportlab        | 4.2.*      | 4.5.*      | Bugfixes                                             |
| sqlalchemy       | 2.0.*      | 2.0.49     | Bugfixes (oder Pin offen lassen, pip nimmt latest)  |
| alembic          | 1.14.*     | 1.18.*     | Bugfixes, kompatibel mit SQLAlchemy 2.0             |

**Test-Strategie P1:**
- `pytest backend/tests/` (alle 268 Backend-Tests)
- `cd e2e && npm test` (alle E2E-Tests Desktop + Mobile)
- Manuell: Upload mit grossem PDF (Pillow), Login (axios), DB-Migration (Alembic)

### P2 — MIT TEST (Major/Minor mit Breaking Changes, planen)

| Library              | Von        | Ziel       | Risiko     | Aufwand                                                                  |
|----------------------|------------|------------|------------|--------------------------------------------------------------------------|
| fastapi              | 0.115.*    | 0.136.*    | MEDIUM     | Strict-Content-Type-Check pruefen; Tests fuer alle Upload-Endpoints      |
| starlette            | (auto)     | 1.0+       | MEDIUM     | Mit FastAPI-Update; multipart-CVE-Fix                                    |
| pydantic-settings    | 2.7.*      | 2.13.*     | LOW-MEDIUM | FieldInfo-Refactor, Edge-Cases bei Field()                               |
| cryptography         | 44.*       | 48.*       | LOW        | E-Mail-Encryption (Fernet) testen; Migration-Pfad (existierende Keys)    |
| node                 | 20-alpine  | 22-alpine  | LOW        | Frontend-Build verifizieren                                              |
| python (Dockerfile)  | 3.12-slim  | 3.13-slim  | LOW        | Alle Backend-Tests; pytesseract/Pillow/cryptography Wheels-Verfuegbarkeit |

**Test-Strategie P2:**
- Vollstaendiger Backend-Testlauf + manuelles Upload/OCR/LLM-Workflow auf Test-DB
- E2E-Tests gegen frischen Build
- Smoke-Test der E-Mail-Konten (Fernet) — ggf. Re-Encrypt-Migration noetig
- DB-Backup vor Alembic-Lauf

### P3 — SPAETER (Major-Bumps, Plan + Vorbereitung)

| Library     | Von    | Ziel  | Aufwand  | Notiz                                                                     |
|-------------|--------|-------|----------|---------------------------------------------------------------------------|
| Tailwind    | 3.4    | 4.x   | HOCH     | Klassen-Migration (`bg-gradient-to-r` -> `bg-linear-to-r`), CSS-first @theme, PostCSS-Plugin-Wechsel. **Auto-Upgrade-Tool deckt ~90% ab.** Browser-Mindest pruefen (PWA-Zielgruppe). |
| Vite        | 5.4    | 7.x   | MITTEL   | Node 20.19+/22.12+ Pflicht; Default-Browser-Target geaendert; ESM-only. PWA-Plugin kompatibel.    |
| chromadb    | 0.6.3  | 1.x   | HOCH     | **Irreversible Migration** — Backup ZWINGEND. Auth-API geaendert, list_collections-Order geaendert. ChromaDB-Client mit-bumpen. Vektor-Index Rebuild via System-Wartung.  |
| Vue         | 3.5    | 3.6   | NIEDRIG  | Wenn Stable: Vapor-Mode opt-in, kein Breaking Change. Aktuell noch Beta.  |

**Test-Strategie P3:**
- Tailwind: vollstaendiger visueller Smoke-Test aller 13 Views; PWA-Testlauf auf iOS Safari + Android Chrome
- Vite: Build + PWA-Install + Service-Worker-Cache-Verhalten
- ChromaDB: separate Test-Instanz, Migration auf Kopie der `chromadb-data`-Volume; ggf. Vektor-Index aus Dokumenten neu aufbauen

---

## 4. Konkrete Patch-Snippets

### `backend/requirements.txt` (P1 anwenden)

```diff
-fastapi==0.115.*
+fastapi==0.115.*           # P2: -> 0.136.*
 uvicorn[standard]==0.34.*
 sqlalchemy[asyncio]==2.0.*
 aiosqlite==0.20.*
-alembic==1.14.*
+alembic==1.18.*
 pydantic-settings==2.7.*
 httpx==0.28.*
 python-dotenv==1.1.*
-python-multipart==0.0.*
+python-multipart>=0.0.26,<0.1
 watchdog==6.*
-Pillow==11.*
+Pillow>=12.2,<13
 pdf2image==1.17.*
 pytesseract==0.3.*
 pdfplumber==0.11.*
-reportlab==4.2.*
+reportlab==4.5.*
 chromadb-client==0.6.*     # P3: -> 1.x mit Server-Bump koordinieren
-cryptography==44.*
+cryptography==44.*         # P2: -> 48.* (CVE-2026-39892 nur 45-46.0.6 betroffen)
 croniter==6.*
+# Hinweis: starlette wird transitiv via fastapi gezogen.
+# CVE-2025-54121/62727 erfordern starlette>=0.49.1 (in fastapi 0.119+).

 # Testing
 pytest==8.*
 pytest-asyncio==0.25.*
```

### `frontend/package.json` (P1 anwenden)

```diff
   "dependencies": {
-    "axios": "^1.7.0",
+    "axios": "^1.15.1",
     "pinia": "^2.2.0",
     "vue": "^3.5.0",
     "vue-router": "^4.4.0"
   },
   "devDependencies": {
     "@vitejs/plugin-vue": "^5.1.0",
     "autoprefixer": "^10.4.0",
     "postcss": "^8.4.0",
     "tailwindcss": "^3.4.0",
-    "vite": "^5.4.0",
-    "vite-plugin-pwa": "^0.20.0"
+    "vite": "^5.4.0",
+    "vite-plugin-pwa": "^0.21.0"
   }
```

P3-Vorschau (Vite 7 + Tailwind v4):
```diff
-    "tailwindcss": "^3.4.0",
+    "tailwindcss": "^4.0.0",
+    "@tailwindcss/postcss": "^4.0.0",
-    "vite": "^5.4.0",
+    "vite": "^7.0.0",
-    "@vitejs/plugin-vue": "^5.1.0",
+    "@vitejs/plugin-vue": "^6.0.0"
```

### `e2e/package.json` (P1)

```diff
   "devDependencies": {
-    "@playwright/test": "^1.49.0",
+    "@playwright/test": "^1.59.0",
     "@types/node": "^25.3.3",
-    "typescript": "^5.7.0"
+    "typescript": "^5.8.0"
   }
```

### `docker-compose.yml` (P3)

```diff
   chromadb:
-    image: chromadb/chroma:0.6.3
+    image: chromadb/chroma:1.0.x   # nach Migration + Backup
```

### `backend/Dockerfile` (P2 optional)

```diff
-FROM python:3.12-slim AS builder
+FROM python:3.13-slim AS builder
...
-FROM python:3.12-slim
+FROM python:3.13-slim
```

### `frontend/Dockerfile` (P2)

```diff
-FROM node:20-alpine AS build
+FROM node:22-alpine AS build
```

---

## 5. Spezielle Notizen

### Pydantic v2-Status
Bereits auf Pydantic v2 (via `pydantic-settings==2.7.*`). Kein v1->v2-Migrationsbedarf. Einziges Risiko bei Bump auf 2.13: `FieldInfo`-Refactor (siehe v2.12 Release-Notes), Edge-Cases bei `Field()` in Dataclasses.

### SQLAlchemy 2.0 async + `lazy="selectin"`
Korrekt verwendet laut CLAUDE.md. Keine Aenderung noetig — `lazy="selectin"` bleibt in 2.0.x und 2.1 stabil. SQLAlchemy 2.1 ist noch in Beta (2.1.0b2 vom 16.04.2026), Wechsel nicht empfohlen.

### Vite + PWA
Plugin `vite-plugin-pwa` unterstuetzt offiziell Vite 3-8. Beim Sprung Vite 5 -> 7: nur Node-Version-Anhebung relevant; PWA-Workbox-Cache-Strategien (NetworkOnly fuer File/Thumbnail) bleiben unveraendert.

### Tailwind v4 — Aufwandsschaetzung
- Auto-Upgrade-Tool: `npx @tailwindcss/upgrade@latest`
- Manuelle Restarbeit: Custom-Klassen `btn`, `input`, `badge`, `card` (in CLAUDE.md erwaehnt) muessen ggf. ins neue `@theme`-Format ueberfuehrt werden.
- `autoprefixer`/`postcss` koennen entfallen (Rust-Engine + Lightning CSS).
- Browser-Min: Safari 16.4 / Chrome 111 — pruefen ob das fuer die PWA-Zielgruppe (Smartphone-Scan) akzeptabel ist.

### chromadb 1.x — Risiko-Hinweis
- DB-Migration ist **irreversibel**. Vor Upgrade: `chromadb-data` Volume sichern.
- Bestehender Code nutzt `chromadb-client` lediglich fuer Embeddings/Retrieval — Auth-API-Aenderungen treffen uns nicht (lokal, kein Auth).
- `list_collections`-Result-Order-Aenderung: pruefen ob `embedding_service.py`/`vectorize_service.py` darauf basieren (vermutlich nein — Collections werden ueber Scope-Slug-Name angesprochen).

### Ollama Image-Pin
Aktuell `ollama/ollama:latest` — empfohlen, auf konkrete Version zu pinnen (z.B. `0.5.x`) damit Updates kontrolliert ablaufen. Risiko: API-Aenderungen `/api/chat` oder `/api/embed` zwischen Versionen.

---

## 6. Reihenfolge & Abhaengigkeiten

```
P1 (1 Tag)        P2 (2-3 Tage)              P3 (1-2 Wochen)
+-------------+   +--------------------+     +----------------+
| Pillow      |-->| FastAPI/Starlette  |---->| Tailwind v4    |
| python-multi|   | pydantic-settings  |     | Vite 7         |
| axios       |   | cryptography       |     | chromadb 1.x   |
| reportlab   |   | python 3.13        |     | Vue 3.6 (later)|
| alembic     |   | node 22            |     |                |
| Playwright  |   |                    |     |                |
+-------------+   +--------------------+     +----------------+
```

**Blocker-Abhaengigkeiten:**
- FastAPI-Bump zieht Starlette-Bump automatisch (CVE-Fix inklusive).
- Vite 7 erzwingt Node 22 (P3 setzt P2-Node-Bump voraus).
- chromadb-client-Bump muss synchron mit Server-Image laufen (sonst Protokoll-Mismatch).
- Tailwind v4 unabhaengig, kann ohne Vite-Bump erfolgen, aber Reihenfolge "Vite zuerst, dann Tailwind" reduziert PostCSS-Konfigkonflikte.

---

**Dokument-Ende.** Keine Code-Aenderungen vorgenommen — nur Audit.
