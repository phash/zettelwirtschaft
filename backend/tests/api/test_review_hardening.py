"""Review: Antwort-Parsing, AuditLog, Datei-Umzug, Statistik, 'So übernehmen'
und Re-Analyse als Hintergrund-Vorgang (Review 1.6.0)."""

import json
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditLog
from app.models.correction_mapping import CorrectionMapping
from app.models.document import Document, DocumentStatus, DocumentTag, DocumentType, ReviewStatus, Tag
from app.models.filing_scope import FilingScope
from app.models.review_question import ReviewQuestion
from app.models.warranty_info import WarrantyInfo
from app.services.analysis_service import AnalysisResult
from app.services.task_service import shutdown_tasks, wait_for_task
from tests.factories import make_document

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture(autouse=True)
async def _cancel_leftover_tasks():
    yield
    await shutdown_tasks()


@pytest.fixture(autouse=True)
def _no_vectorizing(monkeypatch):
    """Keine echten Chroma/Ollama-Aufrufe; Aufrufe werden mitgeschrieben."""
    calls: list[tuple] = []

    async def fake_revectorize(document_id, settings, session_factory):
        calls.append(("revectorize", document_id))

    async def fake_scope_update(doc_ids, scope_id, settings):
        calls.append(("scope", tuple(doc_ids), scope_id))

    from app.services import vectorize_service
    monkeypatch.setattr(vectorize_service, "revectorize_document_safe", fake_revectorize)
    monkeypatch.setattr(vectorize_service, "update_scope_metadata", fake_scope_update)
    return calls


async def _scope(db: AsyncSession, name="Privat", slug="privat", is_default=True) -> FilingScope:
    scope = FilingScope(name=name, slug=slug, is_default=is_default)
    db.add(scope)
    await db.flush()
    return scope


async def _doc(db: AsyncSession, scope: FilingScope, **overrides) -> Document:
    values = dict(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/nicht-da.pdf",
        file_type="pdf",
        file_size_bytes=10,
        file_hash=uuid.uuid4().hex,
        document_type=DocumentType.RECHNUNG,
        title="Testrechnung",
        issuer="Test GmbH",
        ocr_text="Rechnung der Test GmbH ueber 10 EUR",
        ai_confidence=0.5,
        status=DocumentStatus.ACTIVE,
        review_status=ReviewStatus.NEEDS_REVIEW,
        filing_scope_id=scope.id,
    )
    values.update(overrides)
    return await make_document(db, **values)


async def _question(db: AsyncSession, doc: Document, field: str | None, **overrides) -> ReviewQuestion:
    q = ReviewQuestion(document_id=doc.id, question=f"Frage zu {field}", field_affected=field, **overrides)
    db.add(q)
    await db.flush()
    return q


async def _answer(client, question_id: str, answer: str):
    return await client.post(f"/api/review/questions/{question_id}/answer", json={"answer": answer})


# ---------------------------------------------------------------------------
# Antworten
# ---------------------------------------------------------------------------


class TestAnswerParsing:
    @pytest.mark.parametrize("answer,expected", [
        ("1.234,56", 1234.56),
        ("12,50 €", 12.5),
        ("EUR 7", 7.0),
        ("1,234.56", 1234.56),
        ("-3,10", -3.1),
    ])
    async def test_german_amounts(self, client, db_session, answer, expected):
        doc = await _doc(db_session, await _scope(db_session), amount=Decimal("1.00"))
        q = await _question(db_session, doc, "amount")
        await db_session.commit()

        resp = await _answer(client, q.id, answer)
        assert resp.status_code == 200, resp.text
        detail = (await client.get(f"/api/review/documents/{doc.id}")).json()
        assert detail["confident_fields"]["amount"] == expected

    @pytest.mark.parametrize("answer", ["NaN", "Infinity", "zwölf", ".", ",", "- €"])
    async def test_invalid_amount_keeps_value_and_question_open(self, client, db_session, answer):
        doc = await _doc(db_session, await _scope(db_session), amount=Decimal("5.00"))
        q = await _question(db_session, doc, "amount")
        await db_session.commit()

        resp = await _answer(client, q.id, answer)
        assert resp.status_code == 422
        detail = (await client.get(f"/api/review/documents/{doc.id}")).json()
        assert detail["confident_fields"]["amount"] == 5.0
        assert detail["questions"][0]["is_answered"] is False

    @pytest.mark.parametrize("answer,expected", [
        ("Arztrechnung", "ARZTRECHNUNG"),
        ("handwerkerrechnung", "HANDWERKER_RECHNUNG"),
        ("quittung", "QUITTUNG"),
        ("AMTLICHES_SCHREIBEN", "AMTLICHES_SCHREIBEN"),
    ])
    async def test_document_type_labels(self, client, db_session, answer, expected):
        doc = await _doc(db_session, await _scope(db_session), document_type=DocumentType.SONSTIGES)
        q = await _question(db_session, doc, "document_type")
        await db_session.commit()

        resp = await _answer(client, q.id, answer)
        assert resp.status_code == 200, resp.text
        detail = (await client.get(f"/api/review/documents/{doc.id}")).json()
        assert detail["document_type"] == expected

    async def test_german_date_with_month_name(self, client, db_session):
        doc = await _doc(db_session, await _scope(db_session))
        q = await _question(db_session, doc, "document_date")
        await db_session.commit()

        resp = await _answer(client, q.id, "2. Juni 2025")
        assert resp.status_code == 200
        detail = (await client.get(f"/api/review/documents/{doc.id}")).json()
        assert detail["confident_fields"]["document_date"] == "2025-06-02"

    async def test_unclear_yes_no_rejected(self, client, db_session):
        doc = await _doc(db_session, await _scope(db_session))
        q = await _question(db_session, doc, "tax_relevant")
        await db_session.commit()
        assert (await _answer(client, q.id, "vielleicht")).status_code == 422

    async def test_answer_for_deleted_document_404(self, client, db_session):
        doc = await _doc(db_session, await _scope(db_session), status=DocumentStatus.DELETED)
        q = await _question(db_session, doc, "title")
        await db_session.commit()
        assert (await _answer(client, q.id, "Neu")).status_code == 404


class TestAnswerSideEffects:
    async def test_tax_relevant_answer_derives_tax_year(self, client, db_session):
        """Regression: tax_year blieb NULL -> Beleg fehlte in Steuer-Ansicht und Export."""
        doc = await _doc(db_session, await _scope(db_session), document_date=date(2024, 5, 3), amount=Decimal("80.00"))
        q = await _question(db_session, doc, "tax_relevant")
        await db_session.commit()

        assert (await _answer(client, q.id, "Ja")).status_code == 200

        years = (await client.get("/api/tax/years")).json()["years"]
        assert years == [2024]
        summary = (await client.get("/api/tax/summary/2024")).json()
        assert summary["total_documents"] == 1
        assert summary["total_amount"] == 80.0

        # Das abgeleitete Jahr ist keine Benutzer-Eingabe: nicht im AuditLog,
        # sonst schuetzte die Re-Analyse es vor einem besseren KI-Wert
        # (gleiche Regel wie PATCH /documents).
        rows = (await db_session.execute(
            select(AuditLog.details).where(
                AuditLog.document_id == doc.id, AuditLog.action == AuditAction.UPDATED,
            )
        )).scalars().all()
        assert [set(json.loads(r)) for r in rows] == [{"tax_relevant"}]

    async def test_answer_writes_audit_log(self, client, db_session):
        doc = await _doc(db_session, await _scope(db_session), title="Alt")
        q = await _question(db_session, doc, "title")
        doc_id = doc.id
        await db_session.commit()

        assert (await _answer(client, q.id, "Neu")).status_code == 200

        rows = (await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == AuditAction.UPDATED)
        )).scalars().all()
        assert len(rows) == 1
        assert json.loads(rows[0].details) == {"title": {"old": "Alt", "new": "Neu"}}

    async def test_title_answer_updates_fts(self, client, db_session, _no_vectorizing):
        doc = await _doc(db_session, await _scope(db_session), title="Unklar")
        q = await _question(db_session, doc, "title")
        doc_id = doc.id
        await db_session.commit()

        assert (await _answer(client, q.id, "Zahnarztrechnung")).status_code == 200

        hits = (await db_session.execute(
            text("SELECT doc_id FROM documents_fts WHERE documents_fts MATCH 'Zahnarztrechnung'")
        )).all()
        assert [h[0] for h in hits] == [doc_id]
        assert _no_vectorizing == [("revectorize", doc_id)]

    async def test_scope_answer_moves_file_and_records_names(
        self, client, db_session, test_settings, _no_vectorizing,
    ):
        archive = Path(test_settings.ARCHIVE_DIR)
        privat = await _scope(db_session)
        praxis = await _scope(db_session, "Praxis", "praxis", is_default=False)
        source = archive / "privat/2024/03/RECHNUNG/abc_test.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"%PDF-1.4")
        doc = await _doc(db_session, privat, file_path=str(source))
        q = await _question(db_session, doc, "filing_scope")
        doc_id, praxis_id = doc.id, praxis.id
        await db_session.commit()

        resp = await _answer(client, q.id, "praxis")
        assert resp.status_code == 200, resp.text

        db_session.expire_all()
        reloaded = await db_session.get(Document, doc_id)
        assert reloaded.filing_scope_id == praxis_id
        target = archive / "praxis/2024/03/RECHNUNG/abc_test.pdf"
        assert Path(reloaded.file_path) == target.resolve()
        assert target.is_file()
        assert not source.exists()
        assert not (archive / "privat/2024").exists()  # leere Ordner aufgeraeumt
        assert (await client.get(f"/api/documents/{doc_id}/file")).status_code == 200
        # Nur Chroma-Metadaten nachziehen, kein Re-Embedding
        assert _no_vectorizing == [("scope", (doc_id,), praxis_id)]

        mapping =(await db_session.execute(
            select(CorrectionMapping).where(CorrectionMapping.field == "filing_scope")
        )).scalar_one()
        assert (mapping.original_value, mapping.corrected_value) == ("Privat", "Praxis")

    async def test_scope_corrections_in_few_shot_contain_no_uuids(self, client, db_session):
        from app.services.analysis_service import _format_correction_examples, _load_correction_examples

        privat = await _scope(db_session)
        await _scope(db_session, "Praxis", "praxis", is_default=False)
        questions = []
        for _ in range(2):
            doc = await _doc(db_session, privat)
            questions.append(await _question(db_session, doc, "filing_scope"))
        await db_session.commit()
        for q in questions:
            assert (await _answer(client, q.id, "Praxis")).status_code == 200

        block = _format_correction_examples(await _load_correction_examples(db_session))
        assert "Praxis" in block
        assert not UUID_RE.search(block)

    async def test_new_scope_answer_creates_scope_with_unique_slug(self, client, db_session):
        privat = await _scope(db_session)
        doc = await _doc(db_session, privat)
        q = await _question(db_session, doc, "filing_scope")
        await db_session.commit()

        assert (await _answer(client, q.id, "NEU: €")).status_code == 200
        created = (await db_session.execute(select(FilingScope).where(FilingScope.name == "€"))).scalar_one()
        assert created.slug.startswith("bereich-")


# ---------------------------------------------------------------------------
# Freigeben / So übernehmen / Statistik / Liste
# ---------------------------------------------------------------------------


class TestReviewActions:
    async def test_approve_writes_audit_and_rejects_deleted(self, client, db_session):
        scope = await _scope(db_session)
        doc = await _doc(db_session, scope)
        await _question(db_session, doc, "title")
        deleted = await _doc(db_session, scope, status=DocumentStatus.DELETED)
        doc_id, deleted_id = doc.id, deleted.id
        await db_session.commit()

        assert (await client.post(f"/api/review/documents/{deleted_id}/approve")).status_code == 404
        assert (await client.post(f"/api/review/documents/{deleted_id}/accept")).status_code == 404
        assert (await client.get(f"/api/review/documents/{deleted_id}")).status_code == 404

        resp = await client.post(f"/api/review/documents/{doc_id}/approve")
        assert resp.status_code == 200
        audit = (await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == AuditAction.REVIEWED)
        )).scalar_one()
        assert json.loads(audit.details) == {"action": "approve", "open_questions": 1}

    async def test_accept_writes_audit(self, client, db_session):
        doc = await _doc(db_session, await _scope(db_session))
        doc_id = doc.id
        await db_session.commit()

        assert (await client.post(f"/api/review/documents/{doc_id}/accept")).status_code == 200
        db_session.expire_all()
        assert (await db_session.get(Document, doc_id)).review_status == ReviewStatus.REVIEWED
        audit = (await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == AuditAction.REVIEWED)
        )).scalar_one()
        assert json.loads(audit.details)["action"] == "accept"

    async def test_stats_ignore_questions_of_approved_documents(self, client, db_session):
        scope = await _scope(db_session)
        open_doc = await _doc(db_session, scope)
        await _question(db_session, open_doc, "title")
        approved = await _doc(db_session, scope, review_status=ReviewStatus.REVIEWED)
        await _question(db_session, approved, "issuer")
        await db_session.commit()

        stats = (await client.get("/api/review/stats")).json()
        assert stats["open_documents"] == 1
        assert stats["open_questions"] == 1

    async def test_pending_exposes_no_server_paths(self, client, db_session):
        await _doc(db_session, await _scope(db_session), thumbnail_path="C:/data/thumbnails/x.png")
        await db_session.commit()

        item = (await client.get("/api/review/pending")).json()["documents"][0]
        assert "thumbnail_path" not in item
        assert item["has_thumbnail"] is True
        assert item["document_type"] == "RECHNUNG"


# ---------------------------------------------------------------------------
# Re-Analyse (Hintergrund-Vorgang, Haupt-Pipeline)
# ---------------------------------------------------------------------------


def _analysis(**overrides) -> AnalysisResult:
    values = dict(
        document_type="ARZTRECHNUNG",
        confidence=0.91,
        title="Arztrechnung Dr. Muster",
        sender="Praxis Dr. Muster",
        recipient="Max Mustermann",
        document_date="2025-02-14",
        amount=95.5,
        currency="EUR",
        reference_number="R-2025-7",
        tags=["arzt", "gesundheit"],
        summary="Rechnung über eine Behandlung.",
        tax_relevant=True,
        tax_category="Aussergewoehnliche_Belastungen",
        tax_year=None,
        warranty_info=None,
        needs_review=False,
        review_questions=[],
    )
    values.update(overrides)
    return AnalysisResult(**values)


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Ersetzt analyze_document (Haupt-Pipeline) und zeichnet die Aufrufe auf."""
    state = {"result": _analysis(), "calls": []}

    async def fake_analyze(file_path, file_type, settings, filing_scopes=None, session=None, pre_ocr_text=None):
        state["calls"].append({"pre_ocr_text": pre_ocr_text, "session": session, "scopes": filing_scopes})
        return None, state["result"]

    monkeypatch.setattr("app.services.reanalysis_service.analyze_document", fake_analyze)
    return state


async def _run_reanalysis(client, doc_id: str) -> dict:
    resp = await client.post(f"/api/review/documents/{doc_id}/reanalyze")
    assert resp.status_code == 202, resp.text
    task = resp.json()
    assert task["kind"] == "reanalyze" and task["key"] == doc_id
    await wait_for_task(task["id"], timeout=5)
    return (await client.get(f"/api/tasks/{task['id']}")).json()


class TestReanalysis:
    async def test_overwrites_all_ai_fields_via_main_pipeline(
        self, client, db_session, fake_pipeline, _no_vectorizing,
    ):
        doc = await _doc(db_session, await _scope(db_session), title="Alt", ocr_text="Arzt OCR")
        doc_id = doc.id
        await db_session.commit()

        task = await _run_reanalysis(client, doc_id)
        assert task["status"] == "SUCCEEDED", task
        assert task["result"]["document_type"] == "ARZTRECHNUNG"
        assert _no_vectorizing == [("revectorize", doc_id)]
        assert fake_pipeline["calls"][0]["pre_ocr_text"] == "Arzt OCR"
        assert fake_pipeline["calls"][0]["session"] is not None  # Few-Shot-Korrekturen

        db_session.expire_all()
        d = await db_session.get(Document, doc_id)
        assert d.document_type == DocumentType.ARZTRECHNUNG
        assert d.title == "Arztrechnung Dr. Muster"
        assert d.issuer == "Praxis Dr. Muster"
        assert d.recipient == "Max Mustermann"
        assert d.reference_number == "R-2025-7"
        assert d.document_date == date(2025, 2, 14)
        assert d.amount == Decimal("95.50")
        assert d.tax_relevant is True
        assert d.tax_category == "Aussergewoehnliche_Belastungen"
        assert d.tax_year == 2025  # abgeleitet aus dem Belegdatum
        assert d.review_status == ReviewStatus.OK
        tags = (await db_session.execute(
            select(Tag.name).join(DocumentTag).where(DocumentTag.document_id == doc_id)
        )).scalars().all()
        assert sorted(tags) == ["arzt", "gesundheit"]
        hits = (await db_session.execute(
            text("SELECT doc_id FROM documents_fts WHERE documents_fts MATCH 'gesundheit'")
        )).all()
        assert [h[0] for h in hits] == [doc_id]
        audit = (await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == AuditAction.REANALYZED)
        )).scalar_one()
        assert "title" in json.loads(audit.details)["overwritten"]

    async def test_user_edits_and_manual_tags_are_protected(self, client, db_session, fake_pipeline):
        scope = await _scope(db_session)
        doc = await _doc(db_session, scope, review_status=ReviewStatus.OK)
        doc_id = doc.id
        await db_session.commit()

        assert (await client.patch(f"/api/documents/{doc_id}", json={"title": "Mein Titel"})).status_code == 200
        assert (await client.post(f"/api/documents/{doc_id}/tags", json={"name": "wichtig"})).status_code == 200
        auto = Tag(name="alt-auto", is_auto_generated=True)
        db_session.add(auto)
        await db_session.flush()
        db_session.add(DocumentTag(document_id=doc_id, tag_id=auto.id))
        await db_session.commit()

        task = await _run_reanalysis(client, doc_id)
        assert task["status"] == "SUCCEEDED", task
        assert task["result"]["protected_fields"] == ["title"]

        db_session.expire_all()
        d = await db_session.get(Document, doc_id)
        assert d.title == "Mein Titel"
        assert d.issuer == "Praxis Dr. Muster"
        tags = (await db_session.execute(
            select(Tag.name).join(DocumentTag).where(DocumentTag.document_id == doc_id)
        )).scalars().all()
        assert sorted(tags) == ["arzt", "gesundheit", "wichtig"]

    async def test_questions_kept_replaced_and_status(self, client, db_session, fake_pipeline):
        fake_pipeline["result"] = _analysis(needs_review=True, review_questions=["Ist der Betrag korrekt?"])
        scope = await _scope(db_session)
        doc = await _doc(db_session, scope)
        answered = await _question(db_session, doc, "title", is_answered=True, answer="X")
        stale = await _question(db_session, doc, None)
        unrelated = await _question(db_session, doc, "kontonummer")
        ids = (doc.id, answered.id, stale.id, unrelated.id)
        await db_session.commit()

        task = await _run_reanalysis(client, ids[0])
        assert task["status"] == "SUCCEEDED", task
        assert task["result"]["needs_review"] is True

        db_session.expire_all()
        remaining = {q.id: q for q in (await db_session.execute(
            select(ReviewQuestion).where(ReviewQuestion.document_id == ids[0])
        )).scalars().all()}
        assert ids[1] in remaining  # beantwortete Frage bleibt
        assert ids[2] not in remaining  # Freitext der alten Analyse ersetzt
        assert ids[3] in remaining  # unabhaengige offene Frage bleibt
        assert any(q.question == "Ist der Betrag korrekt?" for q in remaining.values())
        assert (await db_session.get(Document, ids[0])).review_status == ReviewStatus.NEEDS_REVIEW

    async def test_creates_and_removes_warranty(self, client, db_session, fake_pipeline):
        fake_pipeline["result"] = _analysis(warranty_info={
            "has_warranty": True, "product_name": "Laptop", "purchase_date": "2025-02-14",
            "warranty_duration_months": 24, "warranty_end_date": None, "store_name": "Shop",
        })
        doc = await _doc(db_session, await _scope(db_session))
        doc_id = doc.id
        await db_session.commit()

        assert (await _run_reanalysis(client, doc_id))["status"] == "SUCCEEDED"
        w = (await db_session.execute(select(WarrantyInfo).where(WarrantyInfo.document_id == doc_id))).scalar_one()
        assert w.warranty_end_date == date(2027, 2, 14)

        fake_pipeline["result"] = _analysis(warranty_info=None)
        assert (await _run_reanalysis(client, doc_id))["status"] == "SUCCEEDED"
        db_session.expire_all()
        assert (await db_session.execute(
            select(WarrantyInfo).where(WarrantyInfo.document_id == doc_id)
        )).scalar_one_or_none() is None

    async def test_llm_failure_leaves_document_untouched(self, client, db_session, fake_pipeline):
        fake_pipeline["result"] = AnalysisResult(needs_review=True, llm_error="unreachable")
        doc = await _doc(db_session, await _scope(db_session), title="Bleibt")
        doc_id = doc.id
        await db_session.commit()

        task = await _run_reanalysis(client, doc_id)
        assert task["status"] == "FAILED"
        assert "nicht erreichbar" in task["error"]
        db_session.expire_all()
        assert (await db_session.get(Document, doc_id)).title == "Bleibt"

    async def test_auto_apply_corrections(self, client, db_session, fake_pipeline):
        fake_pipeline["result"] = _analysis(sender="AMAZON EU S.A R.L.")
        db_session.add(CorrectionMapping(
            field="issuer", original_value="Amazon EU S.a r.l.", corrected_value="Amazon",
            occurrence_count=3, auto_apply=True,
        ))
        doc = await _doc(db_session, await _scope(db_session))
        doc_id = doc.id
        await db_session.commit()

        assert (await _run_reanalysis(client, doc_id))["status"] == "SUCCEEDED"
        db_session.expire_all()
        assert (await db_session.get(Document, doc_id)).issuer == "Amazon"

    async def test_scope_change_moves_file(self, client, db_session, fake_pipeline, test_settings):
        archive = Path(test_settings.ARCHIVE_DIR)
        privat = await _scope(db_session)
        praxis = FilingScope(name="Praxis", slug="praxis", is_default=False, keywords='["Dr. Muster"]')
        db_session.add(praxis)
        source = archive / "privat/2025/02/RECHNUNG/abc.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"%PDF-1.4")
        doc = await _doc(db_session, privat, file_path=str(source), ocr_text="Praxis Dr. Muster")
        doc_id, praxis_id = doc.id, praxis.id
        await db_session.commit()

        assert (await _run_reanalysis(client, doc_id))["status"] == "SUCCEEDED"
        db_session.expire_all()
        d = await db_session.get(Document, doc_id)
        assert d.filing_scope_id == praxis_id
        assert Path(d.file_path).is_file()
        assert Path(d.file_path).is_relative_to((archive / "praxis").resolve())
        assert not source.exists()

    async def test_validation_errors(self, client, db_session, fake_pipeline):
        scope = await _scope(db_session)
        deleted = await _doc(db_session, scope, status=DocumentStatus.DELETED)
        empty = await _doc(db_session, scope, ocr_text="  ")
        ids = (deleted.id, empty.id)
        await db_session.commit()

        assert (await client.post(f"/api/review/documents/{ids[0]}/reanalyze")).status_code == 404
        resp = await client.post(f"/api/review/documents/{ids[1]}/reanalyze")
        assert resp.status_code == 400
        assert "OCR-Text" in resp.json()["detail"]
        assert fake_pipeline["calls"] == []
