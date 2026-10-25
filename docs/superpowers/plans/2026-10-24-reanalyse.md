# Neu-Analyse mit Änderungsvorschlag — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dokumente (einzeln oder als Massenlauf nach Modellwechsel) neu analysieren lassen, optional mit neuer OCR. Das Ergebnis ist immer ein Änderungsvorschlag, den der Benutzer pro Feld übernimmt oder verwirft (Issue #39).

**Architecture:** Die Re-Analyse speichert nur noch das (auto-korrigierte) `AnalysisResult` plus optional neuen OCR-Text als `ReanalysisProposal` (höchstens einer pro Dokument). Der Diff wird **bei jedem Lesen** aus Vorschlag und aktuellem Dokument berechnet. Geschützte Felder, zwischenzeitliche Benutzer-Edits und schon gleiche Werte fallen dadurch von selbst heraus. Veraltete Vorschläge brauchen deshalb keine eigene Logik. Das Anwenden ist die bisherige Übernahme-Logik aus `reanalysis_service`, eingeschränkt auf die angenommenen Felder. Der Massenlauf ist eine persistente Item-Tabelle, die eine eigene Hintergrund-Schleife abarbeitet, und zwar nur dann, wenn keine `PENDING`-ProcessingJobs warten.

**Tech Stack:** FastAPI, SQLAlchemy 2.1 async, Alembic, SQLite; Vue 3 `<script setup>`, Tailwind v4; pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-24-reanalyse-design.md`

## Global Constraints

- Analyse ausschließlich über `analysis_service.analyze_document` (Slot, Sanitizer, Verifier, Few-Shot). Keine zweite Pipeline.
- Lange Vorgänge: HTTP 202 + `TaskResponse`, Polling über `useBackgroundTask`. `work()` öffnet eigene kurze Sessions und hält nie eine Schreib-Transaktion über OCR/LLM.
- Migration `019_reanalysis` auf Head `018_document_split`. `detect_stamp` erkennt sie (Tabelle `reanalysis_proposals`). Zeitstempel als `UTCDateTime`. Enums `native_enum=False`.
- Freigegebene Dokumente (`review_status == REVIEWED`) nimmt der Massenlauf nie auf. Einzeln erzeugt der Knopf auch für sie nur einen Vorschlag.
- Ändern sich nur `title` und/oder `summary`, gilt das Dokument als „unverändert“: kein Vorschlag bleibt offen, nur `analysis_model` wird gesetzt.
- UI-Texte deutsch mit echten Umlauten. E2E-Mocks gegen `e2e/helpers/api-types.ts` typgeprüft (`npm run typecheck`).
- Keine Test-Credentials als Literal.
- Neue API-Routen fallen unter das Standard-Body-Limit (256 KiB). Kein Eintrag in `request_limits`/nginx nötig.

## Review Focus

1. **Dokument wird bearbeitet, während ein Vorschlag offen ist.** Das bearbeitete Feld verschwindet aus dem Diff und wird beim Anwenden übersprungen. → Test `test_user_edit_after_proposal_drops_field` (Task 2).
2. **Dokument wird (soft-)gelöscht, während ein Vorschlag offen ist oder ein Lauf es noch vor sich hat.** Der Vorschlag verschwindet aus Liste und Zähler, der Lauf verbucht das Item als `SKIPPED`. Kein 500. → `test_deleted_document_proposal_hidden` (Task 4), `test_run_skips_deleted_document` (Task 5).
3. **Ollama fällt mitten im Massenlauf aus oder das Modell ist noch nicht gezogen.** Der Lauf wartet („wartet auf KI“) und verbucht kein Item als Fehler. Kommt Ollama zurück, läuft er weiter. → `test_run_waits_when_ollama_unreachable` (Task 5).
4. **Neustart des Dienstes während eines Laufs.** Ein Item in `PROCESSING` geht auf `PENDING` zurück, der Lauf setzt fort, nichts wird doppelt gezählt. → `test_run_recovers_after_restart` (Task 5).
5. **Ablagebereich aus dem Vorschlag wurde inzwischen gelöscht, oder seine Archivdatei fehlt.** Der Scope-Eintrag entfällt aus dem Diff. Fehlt die Datei beim Verschieben, kommt beim Anwenden 409 mit verständlicher Meldung, und das Dokument bleibt unverändert. → `test_proposal_scope_deleted_is_dropped`, `test_apply_missing_file_conflict` (Task 2).

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `backend/alembic/versions/019_reanalysis.py` | Neu: `documents.analysis_model`, Tabellen `reanalysis_proposals`, `reanalysis_runs`, `reanalysis_run_items` |
| `backend/app/models/reanalysis.py` | Neu: `ReanalysisProposal`, `ReanalysisRun`, `ReanalysisRunItem`, Enums `RunStatus`, `RunItemStatus` |
| `backend/app/models/document.py` | + `analysis_model` |
| `backend/app/models/notification.py` | + `NotificationType.REANALYSIS_DONE` |
| `backend/app/services/reanalysis_service.py` | Umbau: `create_proposal` (Analyse → Vorschlag), `compute_changes` (Diff), `apply_proposal`, `discard_proposal`. Der Direkt-Überschreib-Pfad entfällt. |
| `backend/app/services/reanalysis_run_service.py` | Neu: Auswahl/Vorschau, Lauf anlegen, `run_reanalysis_runner`-Schleife, Recovery, Pause/Abbruch, Abschluss-Benachrichtigung |
| `backend/app/schemas/reanalysis.py` | Neu: Request-/Response-Schemas |
| `backend/app/api/reanalysis.py` | Neu: `/api/reanalysis/...` (Vorschläge + Läufe) |
| `backend/app/api/review.py` | `reanalyze`-Endpoint nimmt Body `{with_ocr}` und erzeugt einen Vorschlag |
| `backend/app/api/documents.py`, `schemas/document.py` | `analysis_model` + `has_proposal` in `DocumentResponse`. DELETE entfernt den offenen Vorschlag. |
| `backend/app/services/archive_service.py` | setzt `analysis_model` bei erfolgreicher LLM-Analyse |
| `backend/app/main.py`, `migrate.py` | Router, Runner-Task, Recovery im Lifespan. `detect_stamp`. |
| `frontend/src/components/reanalysis/ProposalDiff.vue` | Neu: Feldtabelle alt → neu mit Annehmen/Ablehnen, „Alles übernehmen“, „Verwerfen“ |
| `frontend/src/components/reanalysis/ReanalyzeDialog.vue` | Neu: Bestätigungsdialog mit OCR-Checkbox, optional Vorschau-Zahlen für den Massenlauf |
| `frontend/src/components/reanalysis/ReanalysisProposals.vue` | Neu: Liste aller Vorschläge, Feldfilter, Alle übernehmen/verwerfen |
| `frontend/src/components/reanalysis/ReanalysisRunStatus.vue` | Neu: Fortschritt + Pause/Fortsetzen/Abbrechen des aktuellen Laufs |
| `frontend/src/views/DocumentDetailView.vue`, `ReviewView.vue`, `DocumentsView.vue`, `components/layout/AppHeader.vue`, `services/api.js` | Einbindung |
| `e2e/tests/44-reanalysis.spec.ts`, `e2e/helpers/api-types.ts`, `e2e/helpers/mock.helpers.ts` | E2E |

---

### Task 1: Datenmodell + Migration 019

**Files:**
- Create: `backend/app/models/reanalysis.py`, `backend/alembic/versions/019_reanalysis.py`, `backend/tests/test_migration_reanalysis.py`
- Modify: `backend/app/models/document.py`, `backend/app/models/__init__.py` (Import, falls dort registriert), `backend/app/models/notification.py`, `backend/migrate.py:74`, `backend/app/services/archive_service.py` (Document-Erzeugung ~624)

**Interfaces:**
- Produces:
  - `Document.analysis_model: Mapped[str | None]` (`String(200)`): Modell-Tag der zuletzt übernommenen bzw. geprüften Analyse.
  - `ReanalysisProposal` (`reanalysis_proposals`): `id: str` (uuid), `document_id: str` (FK `documents.id` ON DELETE CASCADE, **unique**), `model: str`, `with_ocr: bool`, `analysis: dict` (JSON, `AnalysisResult.to_dict()` + `tags`, `warranty_info`, `review_questions`, `needs_review`, `filing_scope`, `filing_scope_confidence`, also verlustfrei rekonstruierbar), `ocr_text: str | None`, `ocr_confidence: float | None`, `ocr_note: str | None` (`"discarded_worse"`), `run_id: str | None` (ohne FK), `created_at: datetime`.
  - `RunStatus(str, Enum)`: `RUNNING, PAUSED, WAITING_AI, DONE, CANCELLED`. Aktiv = `RUNNING, PAUSED, WAITING_AI`.
  - `ReanalysisRun` (`reanalysis_runs`): `id`, `status`, `model`, `with_ocr`, `total: int`, `created_at`, `finished_at | None`, `last_error: str | None`. Partial Unique Index `ux_reanalysis_runs_active` auf einer Konstanten, WHERE status IN aktiv (höchstens ein aktiver Lauf).
  - `RunItemStatus(str, Enum)`: `PENDING, PROCESSING, PROPOSED, UNCHANGED, FAILED, SKIPPED`.
  - `ReanalysisRunItem` (`reanalysis_run_items`): `id: int`, `run_id` (FK CASCADE), `document_id: str` (ohne FK, Leser tolerieren fehlende Dokumente), `position: int`, `status`, `error: str | None`. Index `(run_id, status, position)`.
  - `NotificationType.REANALYSIS_DONE = "REANALYSIS_DONE"` (`document_id` NULL).
  - `ReanalysisProposal.analysis_result() -> AnalysisResult` rekonstruiert das Dataclass.
  - `AnalysisResult.from_dict(data: dict) -> AnalysisResult` (classmethod in `analysis_service.py`, Gegenstück zu `to_dict`; unbekannte Keys ignorieren).

- [ ] **Step 1: Tests schreiben** in `tests/test_migration_reanalysis.py` (Muster: `test_migration_document_split.py`):
  - `test_upgrade_from_018_adds_tables_and_column`: DB auf 018 → `upgrade head` → Tabellen `reanalysis_proposals`, `reanalysis_runs`, `reanalysis_run_items` und Spalte `documents.analysis_model` existieren, bestehende Dokumente haben `analysis_model IS NULL`.
  - `test_upgrade_is_idempotent_on_fresh_schema`: `create_fresh_schema` + Stamp 018 + `upgrade head` läuft ohne Fehler.
  - `test_detect_stamp_recognizes_019`: Schema mit `reanalysis_proposals` → `detect_stamp(cur) == "019_reanalysis"`.
  - `test_second_active_run_violates_unique_index`: zweiter Run mit `status="RUNNING"` → `IntegrityError`; ein `DONE`-Run daneben ist erlaubt.
  - In `tests/services/test_archive_service.py`: `test_archive_sets_analysis_model` (erfolgreiche Analyse → `doc.analysis_model == settings.OLLAMA_MODEL`), `test_archive_without_llm_leaves_analysis_model_null` (`analysis_result.llm_error="unreachable"` → `None`).
  - In `tests/services/test_analysis_service.py`: `test_analysis_result_dict_roundtrip` (`AnalysisResult.from_dict(r.to_dict()) == r` für ein Ergebnis mit Tags, Garantie, strukturierten Fragen).
- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**: `cd backend && python -m pytest tests/test_migration_reanalysis.py tests/services/test_archive_service.py -k "analysis_model or reanalysis" -q` → FAIL (Modul/Spalte fehlt).
- [ ] **Step 3: Implementieren**: Models, Migration (idempotent wie 018: Spalten/Tabellen nur anlegen, wenn sie fehlen), `detect_stamp` als ersten Check vor 018, `archive_service` setzt `analysis_model=settings.OLLAMA_MODEL` genau dann, wenn `analysis_result` vorhanden ist und `llm_error is None`, `to_dict` um fehlende Felder ergänzen und `from_dict` ergänzen.
- [ ] **Step 4: Tests + Ketten-Guard laufen lassen**: `python -m pytest tests/test_migration_reanalysis.py tests/test_migrate_stamp.py tests/services/test_archive_service.py tests/services/test_analysis_service.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(reanalysis): Datenmodell + Migration 019 (#39)`

### Task 2: Vorschlag erzeugen, Diff berechnen, anwenden

Zerlegt `run_reanalysis` in drei Schritte: Vorschlag anlegen, Diff berechnen, anwenden. Der direkte Überschreib-Pfad entfällt.

**Files:**
- Modify: `backend/app/services/reanalysis_service.py`
- Test: `backend/tests/services/test_reanalysis_proposals.py` (neu), `backend/tests/api/test_review_hardening.py::TestReanalysis` (umstellen)

**Interfaces:**
- Consumes: Task 1 (`ReanalysisProposal`, `Document.analysis_model`, `AnalysisResult.from_dict`).
- Produces:
  - `PROPOSAL_FIELDS: tuple[str, ...] = REANALYZED_FIELDS + ("filing_scope_id", "tags", "warranty", "ocr_text")`
  - `TEXT_ONLY_FIELDS = frozenset({"title", "summary"})`
  - `@dataclass FieldChange: field: str; old: Any; new: Any` (JSON-fähig: Datum ISO, Decimal als str, Enum-Wert; `filing_scope_id` als `{"id","name"}`; `tags` als sortierte Namensliste; `warranty` als Dict der Garantiefelder oder `None`; `ocr_text` als `{"text","confidence"}`)
  - `async def compute_changes(session, doc: Document, proposal: ReanalysisProposal, settings) -> list[FieldChange]`: nur echte Unterschiede, ohne geschützte Felder (`user_edited_fields`, Garantie-Prefix, Scope), Tags nach `_user_tag_decisions`, Scope über `_match_filing_scope` mit aktuellen Scopes und Text `proposal.ocr_text or doc.ocr_text` (gelöschter oder unbekannter Scope → kein Eintrag). Reihenfolge = `PROPOSAL_FIELDS`.
  - `def is_substantive(changes: list[FieldChange]) -> bool`: `True`, wenn mindestens ein Feld außerhalb `TEXT_ONLY_FIELDS` dabei ist.
  - `@dataclass ProposalOutcome: status: Literal["proposed", "unchanged"]; changes: list[FieldChange]; ocr_note: str | None`
  - `async def create_proposal(ctx: TaskContext | None, session_factory, document_id: str, settings, *, with_ocr: bool, run_id: str | None = None) -> ProposalOutcome`: analysiert (OCR-Teil siehe Task 3, hier noch `with_ocr=False`) und ersetzt einen offenen Vorschlag des Dokuments. Bei `not is_substantive` wird der Vorschlag gelöscht und `doc.analysis_model = settings.OLLAMA_MODEL` gesetzt. Fehler: `TaskError` (Dokument weg, kein Text) bzw. `ReanalysisLLMError(TaskError)` mit `kind: OllamaErrorKind` bei `analysis.llm_error`.
  - `async def apply_proposal(session, document_id: str, settings, fields: set[str] | None = None) -> list[str]`: `None` = alle aktuellen `compute_changes`. Wendet nur angenommene Felder an (bisherige Logik: Felder, `ensure_tax_year`, `_sync_tags`, `_sync_warranty`, Scope + `plan_move`/`copy_files`/`remove_sources`, `ocr_text`/`ocr_confidence`, FTS). Rückfragen nur für angenommene Felder bzw. Freitext-Fragen ohne Feld. `ai_confidence` wird gesetzt, sobald ≥ 1 Feld angenommen ist. `review_status` wird wie heute aus den offenen Fragen bestimmt, nur `REVIEWED` bleibt `REVIEWED`. `analysis_model = proposal.model`, Vorschlag löschen, AuditLog `REANALYZED` mit `{"applied": [...], "rejected": [...], "model": ...}`. Die Funktion committet selbst, weil die Dateiverschiebung rollback-sicher wie bisher um den Commit herum laufen muss. Revektorisierung post-commit über `vectorize_service.revectorize_document_safe`, falls Felder oder Text übernommen wurden. Rückgabe: angewandte Felder. Fehlt die Datei beim Verschieben: `ProposalConflict(str)`.
  - `async def discard_proposal(session, document_id: str) -> bool`: löscht den Vorschlag, setzt `analysis_model = proposal.model`, AuditLog `REANALYZED` `{"discarded": true}`.
  - `async def get_open_proposal(session, document_id) -> ReanalysisProposal | None`: ignoriert gelöschte Dokumente.

- [ ] **Step 1: Bestehende `TestReanalysis`-Tests umstellen**: `_run_reanalysis` startet den Task und ruft danach `POST /api/reanalysis/proposals/{id}/apply` mit `{}` auf. Dieser Endpoint kommt erst in Task 4. Bis dahin rufen die Tests `apply_proposal(db_session, doc_id, test_settings)` direkt auf. Alle bisherigen Assertions (Felder, Tags, FTS, Fragen, Garantie, Auto-Korrektur, Scope-Verschiebung) bleiben, nur die Erwartung an das Audit ändert sich auf `"title" in details["applied"]`. `test_llm_failure_leaves_document_untouched` bleibt unverändert (Task FAILED, kein Vorschlag).
- [ ] **Step 2: Neue Tests** in `tests/services/test_reanalysis_proposals.py` (Fixture `fake_pipeline` aus `test_review_hardening.py` nach `tests/support/reanalysis_fixtures.py` verschieben und per `pytest_plugins` laden):
  - `test_create_proposal_does_not_touch_document`: nach `create_proposal` sind alle Felder unverändert, und es gibt genau einen Vorschlag mit `model == settings.OLLAMA_MODEL`.
  - `test_second_proposal_replaces_first`
  - `test_only_title_summary_changed_is_unchanged`: Analyse unterscheidet sich nur in Titel und Zusammenfassung → `outcome.status == "unchanged"`, kein Vorschlag, `doc.analysis_model` gesetzt.
  - `test_identical_values_yield_no_change`: gleiche Werte → `compute_changes == []`.
  - `test_protected_field_not_in_changes`: Titel per PATCH bearbeitet → `"title"` nicht in den Changes.
  - `test_user_edit_after_proposal_drops_field` (Review Focus 1): Vorschlag ändert `issuer`, danach PATCH `issuer` → nicht mehr in `compute_changes`. `apply_proposal(fields={"issuer"})` lässt den Benutzerwert stehen.
  - `test_partial_apply`: Vorschlag ändert `amount` und `document_type`. `apply_proposal(fields={"amount"})` → Betrag neu, Typ alt, neue Frage mit `field_affected="document_type"` wird **nicht** angelegt, Frage zu `amount` schon. Vorschlag ist weg, `analysis_model` gesetzt.
  - `test_reviewed_stays_reviewed_after_apply`
  - `test_discard_sets_model_and_removes_proposal`
  - `test_proposal_scope_deleted_is_dropped` (Review Focus 5)
  - `test_apply_missing_file_conflict` (Review Focus 5): Scope-Wechsel, Archivdatei gelöscht → `ProposalConflict`, Dokument und Vorschlag unverändert.
- [ ] **Step 3: Fehlschlag prüfen**: `python -m pytest tests/services/test_reanalysis_proposals.py tests/api/test_review_hardening.py -k Reanalysis -q` → FAIL.
- [ ] **Step 4: Implementieren.** `run_reanalysis(ctx, document_id, settings, with_ocr=False)` bleibt als `work`-Funktion bestehen und ruft `create_proposal`. Rückgabe `{"message", "status": outcome.status, "changes": len(changes), "ocr_note"}`. Die bisherigen Hilfsfunktionen werden weiterverwendet, nicht kopiert.
- [ ] **Step 5: Tests laufen lassen**: `python -m pytest tests/services/test_reanalysis_proposals.py tests/api/test_review_hardening.py tests/api/test_review.py tests/api/test_dos_guards.py -q` → PASS.
- [ ] **Step 6: Commit** `feat(reanalysis): Vorschlag statt Direkt-Ueberschreiben (#39)`

### Task 3: Option „Text neu erkennen“

**Files:**
- Modify: `backend/app/services/reanalysis_service.py`
- Test: `backend/tests/services/test_reanalysis_proposals.py`

**Interfaces:**
- Consumes: `ocr_service.extract_text(file_path, file_type, settings) -> OcrResult | None`.
- Produces: `OCR_WORSE_MARGIN = 0.10` (Modul-Konstante). In `create_proposal(with_ocr=True)`: neue OCR. Ist `result is None`, der Text leer oder `result.average_confidence < doc.ocr_confidence - OCR_WORSE_MARGIN`, analysiert die KI mit dem alten Text, und es gilt `ocr_note = "discarded_worse"`. Sonst analysiert sie mit `pre_ocr_text=result.full_text` und speichert `proposal.ocr_text/ocr_confidence`. Ein Vorschlag nur mit neuem OCR-Text (sonst nichts geändert) zählt als substantiell.

- [ ] **Step 1: Tests**: `test_ocr_option_uses_new_text` (gefälschtes `extract_text` liefert Konfidenz 0.9, alt 0.6 → Analyse bekommt neuen Text, Change `ocr_text` mit `new.confidence == 0.9`), `test_ocr_worse_is_discarded` (neu 0.4, alt 0.8 → Analyse mit altem Text, `outcome.ocr_note == "discarded_worse"`, kein `ocr_text`-Change), `test_ocr_works_without_stored_text` (Dokument ohne `ocr_text`, `with_ocr=True` → Vorschlag statt `TaskError`), `test_apply_ocr_updates_text_and_fts`.
- [ ] **Step 2: Fehlschlag prüfen** (`-k ocr`) → FAIL.
- [ ] **Step 3: Implementieren.** Die OCR läuft außerhalb jeder Session in einem eigenen `async with analysis_slot():`-Block (AVAIL-06: OCR + LLM teilen den Slot). Danach wird der Slot freigegeben und erst dann `analyze_document` aufgerufen, das ihn selbst wieder nimmt. Den Semaphor nicht verschachteln, er ist nicht reentrant.
- [ ] **Step 4: Tests** → PASS.
- [ ] **Step 5: Commit** `feat(reanalysis): optionale Neu-Erkennung des Textes (#39)`

### Task 4: API für Einzel-Re-Analyse und Vorschläge

**Files:**
- Create: `backend/app/schemas/reanalysis.py`, `backend/app/api/reanalysis.py`, `backend/tests/api/test_reanalysis_api.py`
- Modify: `backend/app/api/review.py:244-275`, `backend/app/api/documents.py` (Response + DELETE), `backend/app/schemas/document.py` (`DocumentResponse`), `backend/app/main.py:330-344`

**Interfaces:**
- Consumes: Task 2/3.
- Produces (alle Pfade unter `/api`):
  - `POST /review/documents/{id}/reanalyze` Body `ReanalyzeRequest{with_ocr: bool = False}` (Body optional, leerer Body = `False`). Ohne OCR-Text und ohne `with_ocr` bleibt es bei 400 `NO_OCR_TEXT_MESSAGE`. Task-Ergebnis siehe Task 2.
  - `GET /reanalysis/proposals?field=<name>&page=&page_size=` → `ProposalListResponse{items: list[ProposalResponse], total, field_counts: dict[str, int]}`. Gelöschte Dokumente und Vorschläge ohne verbleibende substantielle Changes werden nicht gezählt. `field`-Filter ignoriert `TEXT_ONLY_FIELDS`.
  - `GET /reanalysis/proposals/{document_id}` → `ProposalResponse{document_id, document_title, model, with_ocr, ocr_note, created_at, changes: list[FieldChangeResponse{field, label, old, new}]}`, 404 wenn keiner offen ist. `label` = deutscher Feldname (Mapping in `schemas/reanalysis.py`: `document_type` „Dokumenttyp“, `title` „Titel“, `issuer` „Aussteller“, `document_date` „Datum“, `amount` „Betrag“, `currency` „Währung“, `summary` „Zusammenfassung“, `tax_relevant` „Steuerrelevant“, `tax_category` „Steuerkategorie“, `tax_year` „Steuerjahr“, `recipient` „Empfänger“, `reference_number` „Referenznummer“, `filing_scope_id` „Ablagebereich“, `tags` „Tags“, `warranty` „Garantie“, `ocr_text` „Erkannter Text“).
  - `POST /reanalysis/proposals/{document_id}/apply` Body `ApplyRequest{fields: list[str] | None = None}` → `DocumentResponse`. Unbekannte Feldnamen → 422. `ProposalConflict` → 409.
  - `POST /reanalysis/proposals/{document_id}/discard` → 204.
  - `POST /reanalysis/proposals/apply-all` Body `BulkProposalRequest{field: str | None = None}` → 202 `TaskResponse`, kind `reanalysis_apply_all`, key `"global"`. Pro Dokument eigene Session, Konflikte werden gezählt, nicht abgebrochen. Ergebnis `{"applied", "conflicts"}`.
  - `POST /reanalysis/proposals/discard-all` Body `BulkProposalRequest` → `{"discarded": int}`.
  - `DocumentResponse` + `analysis_model: str | None`, `has_proposal: bool`. `DELETE /documents/{id}` löscht den offenen Vorschlag mit.

- [ ] **Step 1: Tests** in `tests/api/test_reanalysis_api.py`: `test_reanalyze_with_ocr_body`, `test_reanalyze_empty_body_still_works`, `test_get_proposal_404_when_none`, `test_get_proposal_has_german_labels`, `test_apply_selected_fields`, `test_apply_unknown_field_422`, `test_apply_conflict_409`, `test_discard_204`, `test_list_filter_by_field_ignores_text_only`, `test_field_counts`, `test_apply_all_task`, `test_discard_all_with_filter`, `test_document_response_has_model_and_proposal_flag`, `test_deleted_document_proposal_hidden` (Review Focus 2: DELETE → Vorschlag weg, Liste leer, `total == 0`).
- [ ] **Step 2: Fehlschlag prüfen** → FAIL.
- [ ] **Step 3: Implementieren**, Router in `main.py` einhängen.
- [ ] **Step 4: Tests** `python -m pytest tests/api -q` → PASS.
- [ ] **Step 5: Commit** `feat(reanalysis): API fuer Vorschlaege (#39)`

### Task 5: Massenlauf

**Files:**
- Create: `backend/app/services/reanalysis_run_service.py`, `backend/tests/services/test_reanalysis_run_service.py`
- Modify: `backend/app/api/reanalysis.py`, `backend/app/schemas/reanalysis.py`, `backend/app/main.py` (Lifespan: Recovery vor dem Start, Runner-Task in `background_tasks`), `backend/app/services/notification_service.py`

**Interfaces:**
- Consumes: `create_proposal(..., run_id=...)`, `ReanalysisLLMError.kind`, `queue_worker_service.is_queue_paused()`.
- Produces:
  - `SECONDS_PER_DOCUMENT = 45`, `OCR_FACTOR = 2`, `WAIT_BACKOFF_S = (30, 60, 120, 300, 900)` (danach 900 s), `IDLE_POLL_S = 5`, `WAIT_KINDS = {UNREACHABLE, TIMEOUT, MODEL_MISSING, SERVER_ERROR}`.
  - `@dataclass RunPreview: eligible: int; reviewed_excluded: int; no_text_excluded: int; deleted_or_missing: int; estimate_seconds: int`
  - `async def preview_run(session, settings, *, document_ids: list[str] | None, outdated: bool, with_ocr: bool) -> tuple[RunPreview, list[str]]`. Auswahl: nicht `DELETED`, nicht `REVIEWED`. Ohne `with_ocr` nur Dokumente mit Text. Bei `outdated=True` (`document_ids` muss dann `None` sein): `analysis_model IS NULL OR != settings.OLLAMA_MODEL`, ausgenommen Dokumente mit offenem Vorschlag, dessen `model == settings.OLLAMA_MODEL`. Reihenfolge nach `created_at` aufsteigend. Rückgabe zusätzlich der geordneten ID-Liste.
  - `async def start_run(session_factory, settings, *, document_ids, outdated, with_ocr) -> ReanalysisRun`: Bei leerer Auswahl `ValueError` (→ 422 „Keine Dokumente zum Neu-Analysieren.“), bei aktivem Lauf `RunConflictError(active_run)` (→ 409).
  - `async def run_reanalysis_runner(session_factory, settings) -> None`: Endlosschleife. Kein Item, Lauf `PAUSED`, `is_queue_paused()` oder ein `ProcessingJob` mit Status `PENDING`/`PROCESSING` → `sleep(IDLE_POLL_S)`. Sonst das nächste `PENDING`-Item claimen (`UPDATE ... WHERE status='PENDING'`), dann `create_proposal`. Ergebnis → `PROPOSED`/`UNCHANGED`. `TaskError` → `FAILED` + `error`. Dokument gelöscht/fehlt → `SKIPPED`. `ReanalysisLLMError` mit `kind in WAIT_KINDS` → Item zurück auf `PENDING`, Lauf `WAITING_AI` + `last_error`, Backoff-Schlaf, danach `RUNNING`. Sonst `FAILED`. Sind alle Items erledigt: Lauf `DONE`, `finished_at`, Benachrichtigung `REANALYSIS_DONE` mit Titel „Neu-Analyse fertig“ und Text „{proposed} Vorschläge, {unchanged} unverändert, {failed} Fehler“.
  - `async def recover_reanalysis_runs(session_factory) -> int`: Items `PROCESSING` → `PENDING`.
  - `async def set_run_status(session, run_id, action: Literal["pause", "resume", "cancel"]) -> ReanalysisRun`: cancel → `CANCELLED`, offene Items `SKIPPED`, bereits erzeugte Vorschläge bleiben.
  - `async def run_summary(session, run) -> RunResponse{id, status, model, with_ocr, total, done, proposed, unchanged, failed, skipped, last_error, created_at, finished_at, failures: list[{document_id, title, error}] (max. 50)}`
  - API: `POST /reanalysis/runs/preview` Body `RunRequest{document_ids: list[str] | None (max. 5000), outdated: bool = False, with_ocr: bool = False}` → `RunPreview`. `POST /reanalysis/runs` gleicher Body → 201 `RunResponse`. `GET /reanalysis/runs/current` → aktiver oder zuletzt beendeter Lauf, 404 ohne. `POST /reanalysis/runs/{id}/{pause|resume|cancel}` → `RunResponse`.

- [ ] **Step 1: Tests** in `tests/services/test_reanalysis_run_service.py` (Runner per Hilfsfunktion `run_until_idle(session_factory, settings, max_steps)` testbar machen, statt die Endlosschleife zu starten; dafür eine öffentliche Funktion `process_next_item(session_factory, settings) -> bool` extrahieren, die die Schleife ruft):
  - `test_preview_outdated_excludes_reviewed_current_and_open_current_proposal`
  - `test_preview_without_ocr_excludes_documents_without_text`
  - `test_preview_explicit_ids_excludes_reviewed`
  - `test_estimate_seconds` (3 Dokumente, OCR an → `3 * 45 * 2`)
  - `test_second_run_conflict`
  - `test_process_waits_while_jobs_pending`: PENDING-ProcessingJob vorhanden → `process_next_item` gibt `False` zurück, Item bleibt `PENDING`.
  - `test_process_respects_pause_and_queue_pause`
  - `test_run_completes_and_notifies`: 3 Items (Ergebnisse proposed/unchanged/TaskError) → Lauf `DONE`, Zähler 1/1/1, genau eine `REANALYSIS_DONE`-Benachrichtigung mit Text „1 Vorschläge, 1 unverändert, 1 Fehler“.
  - `test_run_waits_when_ollama_unreachable` (Review Focus 3): erster Aufruf `unreachable` → Item `PENDING`, Lauf `WAITING_AI`. Zweiter Aufruf erfolgreich (Backoff-Schlaf per Monkeypatch auf 0) → `PROPOSED`, Lauf `RUNNING`/`DONE`.
  - `test_run_skips_deleted_document` (Review Focus 2)
  - `test_run_recovers_after_restart` (Review Focus 4): Item `PROCESSING` → `recover_reanalysis_runs` → `PENDING`, danach normal fertig, `total` unverändert.
  - `test_cancel_marks_pending_skipped_and_keeps_proposals`
  - API-Tests in `tests/api/test_reanalysis_api.py`: `test_runs_preview_endpoint`, `test_start_run_201_and_current`, `test_start_run_empty_422`, `test_start_run_conflict_409`, `test_pause_resume_cancel`.
- [ ] **Step 2: Fehlschlag prüfen** → FAIL.
- [ ] **Step 3: Implementieren**, Lifespan einhängen (Recovery vor dem Runner-Start, wie `recover_interrupted_jobs`).
- [ ] **Step 4: Tests**: `python -m pytest -q` (komplett) → PASS, nur der bekannte Tesseract-Skip.
- [ ] **Step 5: Commit** `feat(reanalysis): Massenlauf mit Vorrang neuer Eingaenge (#39)`

### Task 6: Frontend-Einzeldokument: Knopf, Dialog, Diff

**Files:**
- Create: `frontend/src/components/reanalysis/ProposalDiff.vue`, `frontend/src/components/reanalysis/ReanalyzeDialog.vue`, `e2e/tests/44-reanalysis.spec.ts`
- Modify: `frontend/src/services/api.js`, `frontend/src/views/DocumentDetailView.vue`, `frontend/src/views/ReviewView.vue:233-258,516-535`, `e2e/helpers/api-types.ts`, `e2e/helpers/mock.helpers.ts`

**Interfaces:**
- Consumes: API aus Task 4.
- Produces:
  - `api.js`: `reanalyzeDocument(id, { withOcr = false } = {})` (Body `{with_ocr}`), `getProposal(documentId)`, `applyProposal(documentId, fields = null)`, `discardProposal(documentId)`, `getProposals(params)`, `applyAllProposals(field = null)`, `discardAllProposals(field = null)`, `previewReanalysisRun(body)`, `startReanalysisRun(body)`, `getCurrentReanalysisRun()`, `setReanalysisRunStatus(id, action)`.
  - `<ProposalDiff :proposal :busy @apply="(fields: string[] | null) => …" @discard>`: Tabelle Feld | Bisher | Neu | Checkbox (Standard: alle angehakt). Knöpfe „Auswahl übernehmen“, „Alles übernehmen“, „Verwerfen“. `ocr_text` wird als „Erkannter Text (Konfidenz 62 % → 88 %)“ mit aufklappbarer Vorschau alt/neu angezeigt. `ocr_note === 'discarded_worse'` erzeugt den Hinweis „Neu erkannter Text war schlechter und wurde verworfen.“ Darstellung von Werten: `formatDate`, `formatCurrency`, Dokumenttyp-Label aus `constants/documentTypes.js`, `true/false` → „Ja/Nein“, leer → „—“.
  - `<ReanalyzeDialog :open :title :preview? :busy @confirm="({ withOcr }) => …" @cancel>`: Checkbox „Text neu erkennen (OCR) — dauert länger“, Standard aus. Mit `preview` zusätzlich die Zahlen („N Dokumente, ca. X“, „Y freigegebene bleiben unverändert“, „Z ohne erkannten Text — nur mit Neu-Erkennung“). Bei Änderung der Checkbox emittiert er `@ocr-change`, damit der Aufrufer die Vorschau neu lädt.
- DocumentDetailView: Knopf „Neu analysieren“ in der Kopfzeile neben „Herunterladen“, dann Dialog, dann `useBackgroundTask` (`resume({kind:'reanalyze', key:id})` beim Mounten). Bei Erfolg: Status `unchanged` → Toast „Keine Änderungen — mit {model} geprüft“, Dokument neu laden. Status `proposed` → `getProposal` und `ProposalDiff` oberhalb der Metadaten. Hat das Dokument `has_proposal`, wird der Diff beim Laden direkt angezeigt. Nach apply/discard Dokument neu laden. Im Abschnitt „KI-Analyse“ steht „Modell: {analysis_model || 'unbekannt'}“.
- ReviewView: Das bestehende Re-Analyse-Panel ruft `reanalyzeDocument(id)` auf und zeigt danach `ProposalDiff` statt des direkten Neuladens.

- [ ] **Step 1: E2E-Tests** in `44-reanalysis.spec.ts` (Mocks in `mock.helpers.ts`, Typen in `api-types.ts`: `ReanalyzeRequest`, `ProposalResponse`, `FieldChangeResponse`, `ProposalListResponse`, `RunPreview`, `RunResponse`, `DocumentResponse.analysis_model/has_proposal`):
  - `detail: Neu analysieren mit OCR sendet with_ocr=true` (`page.waitForRequest`, Body `{with_ocr: true}`)
  - `detail: Vorschlag zeigt alt → neu und übernimmt nur angehakte Felder` (Häkchen bei „Betrag“ entfernen → Apply-Body `{fields: [...ohne amount]}`)
  - `detail: unverändert zeigt Toast mit Modell`
  - `detail: verworfener OCR-Text zeigt Hinweis`
  - `review: Re-Analyse nach KI-Ausfall zeigt Vorschlag`
- [ ] **Step 2: Fehlschlag prüfen**: `cd e2e && npx playwright test 44-reanalysis --project=chromium` → FAIL.
- [ ] **Step 3: Implementieren.**
- [ ] **Step 4: Prüfen**: `cd e2e && npm run typecheck && npx playwright test 44-reanalysis 05-document-detail 06-review 31-review-regressions` → PASS in beiden Projekten.
- [ ] **Step 5: Commit** `feat(reanalysis): Neu analysieren am Dokument mit Vorschlag (#39)`

### Task 7: Frontend: Vorschläge prüfen auf `/pruefen`

**Files:**
- Create: `frontend/src/components/reanalysis/ReanalysisProposals.vue`
- Modify: `frontend/src/views/ReviewView.vue`, `frontend/src/components/layout/AppHeader.vue:111-116`, `e2e/tests/44-reanalysis.spec.ts`

**Interfaces:**
- Consumes: `getProposals`, `applyProposal`, `discardProposal`, `applyAllProposals`, `discardAllProposals`, `<ProposalDiff>`.
- Produces: In ReviewView gibt es Tabs „Rückfragen“ und „Änderungsvorschläge (N)“. Der Tab steht in `?tab=vorschlaege` (Router-Query, damit der Benachrichtigungs-Link funktioniert). `ReanalysisProposals` zeigt eine Feldfilter-Auswahl (aus `field_counts`, Labels aus der Antwort) und eine Liste. Pro Eintrag gibt es Titel (Link `/dokumente/:id`) und einen aufklappbaren `ProposalDiff`. Über der Liste stehen „Alle übernehmen (N)“ und „Alle verwerfen (N)“, jeweils mit `ConfirmDialog` und unter Berücksichtigung des Filters. „Alle übernehmen“ läuft per `useBackgroundTask` (kind `reanalysis_apply_all`, key `global`), danach wird die Liste neu geladen, und ein Toast zeigt „{applied} übernommen, {conflicts} mit Konflikt“. Paginierung mit `components/common/Pagination`. AppHeader: `REANALYSIS_DONE` → `router.push('/pruefen?tab=vorschlaege')`, Text „{title} – Vorschläge prüfen“.

- [ ] **Step 1: E2E-Tests**: `pruefen: Tab Änderungsvorschläge zeigt Anzahl und Liste`, `pruefen: Feldfilter lädt mit field=amount`, `pruefen: Alle übernehmen fragt nach und startet Task`, `benachrichtigung: REANALYSIS_DONE öffnet Vorschläge-Tab`.
- [ ] **Step 2: Fehlschlag prüfen** → FAIL.
- [ ] **Step 3: Implementieren.**
- [ ] **Step 4: Prüfen**: `npx playwright test 44-reanalysis 06-review 16-dashboard-queue-notifications` → PASS.
- [ ] **Step 5: Commit** `feat(reanalysis): Aenderungsvorschlaege auf der Pruefen-Seite (#39)`

### Task 8: Frontend: Massenlauf in der Dokumentliste

**Files:**
- Create: `frontend/src/components/reanalysis/ReanalysisRunStatus.vue`
- Modify: `frontend/src/views/DocumentsView.vue`, `e2e/tests/44-reanalysis.spec.ts`, `e2e/tests/12-responsive.spec.ts` (nur falls Layout-Assertions brechen)

**Interfaces:**
- Consumes: `previewReanalysisRun`, `startReanalysisRun`, `getCurrentReanalysisRun`, `setReanalysisRunStatus`, `<ReanalyzeDialog>`.
- Produces:
  - DocumentsView: Checkbox pro Tabellenzeile (Desktop) und pro Karte (Mobile, neben der Steuer-Checkbox, `aria-label="{Titel} auswählen"`), dazu „Alle auf dieser Seite“ im Tabellenkopf. Die Auswahl bleibt beim Blättern erhalten und wird bei Filterwechsel geleert. Ist mindestens eins ausgewählt, erscheint eine Aktionsleiste „{n} ausgewählt · Neu analysieren · Auswahl aufheben“. Daneben steht immer der Knopf „Mit aktuellem Modell neu analysieren …“ (`outdated: true`). Beide öffnen `ReanalyzeDialog` mit `preview`, und die Vorschau wird bei OCR-Wechsel neu geladen. Bei `eligible == 0` ist der Bestätigungsknopf deaktiviert.
  - `<ReanalysisRunStatus>`: pollt `getCurrentReanalysisRun` alle 5 s, solange der Lauf aktiv ist (`page.clock`-tauglich, `setInterval` in `onMounted`, Aufräumen in `onUnmounted`). Er zeigt „Neu-Analyse: {done} von {total}“ mit Balken, „wartet auf KI“ bei `WAITING_AI` (mit `last_error`), „pausiert“, und Knöpfe Pausieren/Fortsetzen/Abbrechen (Abbrechen mit `ConfirmDialog`). Ist der Lauf `DONE`, zeigt er die Zusammenfassung mit Link „Vorschläge prüfen“ (`/pruefen?tab=vorschlaege`) und aufklappbaren Fehlern. Er sitzt über der Dokumentliste und ist ausgeblendet, wenn 404 kommt oder der letzte Lauf `CANCELLED`/`DONE` und älter als 24 h ist.

- [ ] **Step 1: E2E-Tests**: `liste: Auswahl zeigt Aktionsleiste und startet Lauf mit IDs`, `liste: Mit aktuellem Modell zeigt Vorschau-Zahlen und OCR lädt neu`, `liste: eligible 0 deaktiviert Start`, `liste: Laufstatus pausieren/fortsetzen` (Polling per `page.clock`), `liste: wartet auf KI wird angezeigt`, `mobile: Karten-Checkbox wählt aus` (Projekt mobile).
- [ ] **Step 2: Fehlschlag prüfen** → FAIL.
- [ ] **Step 3: Implementieren.**
- [ ] **Step 4: Prüfen**: `npx playwright test 44-reanalysis 04-documents 12-responsive 15-documents-upload-dashboard` → PASS in beiden Projekten.
- [ ] **Step 5: Commit** `feat(reanalysis): Massenlauf aus der Dokumentliste (#39)`

### Task 9: Doku + Gesamtlauf

**Files:**
- Modify: `CLAUDE.md` (Projektstruktur: neue Dateien; Migration 019; Datenmodelle `ReanalysisProposal`/`ReanalysisRun`; Architektur-Abschnitt „Re-Analyse“ auf Vorschlag-Modell umschreiben; Notification `REANALYSIS_DONE`), `frontend/src/views/HelpView.vue` (Eintrag „Neu analysieren“, falls dort Funktionen erklärt werden), `README.md` (Changelog-Eintrag „Unreleased“)

- [ ] **Step 1: Doku anpassen.** Der bisherige CLAUDE.md-Satz „Re-Analyse: … alle anderen KI-Felder werden komplett ersetzt“ wird ersetzt durch: Vorschlag, Diff beim Lesen, teilweise Übernahme, reine Titel-/Zusammenfassungsänderung = unverändert, Massenlauf nur ohne wartende Jobs, REVIEWED ausgeschlossen.
- [ ] **Step 2: Gesamte lokale CI**: `pwsh scripts/ci-local.ps1 -SkipDocker` → alles grün. Output lesen (Warnings).
- [ ] **Step 3: Commit** `docs(reanalysis): CLAUDE.md, Hilfe, Changelog (#39)`
