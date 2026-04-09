"""Tests fuer erweitertes Rueckfrage-System API."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.models.filing_scope import FilingScope
from app.models.review_question import ReviewQuestion
from app.models.correction_mapping import CorrectionMapping


async def _create_scope(db: AsyncSession, **overrides) -> FilingScope:
    """Erstellt einen Test-Ablagebereich."""
    defaults = dict(
        name="Privat",
        slug="privat",
        is_default=True,
    )
    defaults.update(overrides)
    scope = FilingScope(**defaults)
    db.add(scope)
    await db.flush()
    return scope


async def _create_doc(db: AsyncSession, scope: FilingScope, **overrides) -> Document:
    """Erstellt ein Test-Dokument mit Pflichtfeldern."""
    defaults = dict(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/2024/03/RECHNUNG/abc_test.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        file_hash=f"hash_{uuid.uuid4().hex[:12]}",
        document_type=DocumentType.RECHNUNG,
        title="Testrechnung",
        issuer="Test GmbH",
        ocr_text="OCR Text der Testrechnung",
        ocr_confidence=0.9,
        ai_confidence=0.5,
        status=DocumentStatus.ACTIVE,
        review_status=ReviewStatus.NEEDS_REVIEW,
        filing_scope_id=scope.id,
    )
    defaults.update(overrides)
    doc = Document(**defaults)
    db.add(doc)
    await db.flush()
    return doc


async def _create_question(
    db: AsyncSession, doc: Document, **overrides
) -> ReviewQuestion:
    """Erstellt eine Test-Rueckfrage."""
    defaults = dict(
        document_id=doc.id,
        question="Wie lautet der Titel?",
        field_affected="title",
        is_answered=False,
    )
    defaults.update(overrides)
    q = ReviewQuestion(**defaults)
    db.add(q)
    await db.flush()
    return q


# =========================================================================
# Bestehende Empty-State / Not-Found Tests
# =========================================================================


@pytest.mark.asyncio
class TestReviewPending:
    async def test_pending_empty(self, client):
        resp = await client.get("/api/review/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["documents"] == []
        assert data["total"] == 0


@pytest.mark.asyncio
class TestReviewStats:
    async def test_stats_empty(self, client):
        resp = await client.get("/api/review/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["open_documents"] == 0
        assert data["open_questions"] == 0
        assert data["answered_questions"] == 0


@pytest.mark.asyncio
class TestReviewDocument:
    async def test_detail_not_found(self, client):
        resp = await client.get("/api/review/documents/nonexistent")
        assert resp.status_code == 404

    async def test_approve_not_found(self, client):
        resp = await client.post("/api/review/documents/nonexistent/approve")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestReviewAnswer:
    async def test_answer_not_found(self, client):
        resp = await client.post("/api/review/questions/nonexistent/answer", json={"answer": "test"})
        assert resp.status_code == 404

    async def test_answer_empty(self, client):
        resp = await client.post("/api/review/questions/nonexistent/answer", json={"answer": ""})
        assert resp.status_code == 400


# =========================================================================
# Tests mit Daten: _update_field_from_answer (alle Branches)
# =========================================================================


@pytest.mark.asyncio
class TestAnswerFieldUpdateTitle:
    """Beantworte Rueckfrage mit field_affected='title'."""

    async def test_answer_updates_title(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, title="Alter Titel")
        q = await _create_question(
            db_session, doc,
            question="Wie lautet der Titel?",
            field_affected="title",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Neuer Titel"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Pruefen ob Titel aktualisiert
        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.status_code == 200
        assert detail.json()["confident_fields"]["title"] == "Neuer Titel"


@pytest.mark.asyncio
class TestAnswerFieldUpdateIssuer:
    """Beantworte Rueckfrage mit field_affected='issuer'."""

    async def test_answer_updates_issuer(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, issuer="Alte Firma")
        q = await _create_question(
            db_session, doc,
            question="Wer ist der Aussteller?",
            field_affected="issuer",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Neue Firma GmbH"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["issuer"] == "Neue Firma GmbH"


@pytest.mark.asyncio
class TestAnswerFieldUpdateAmount:
    """Beantworte Rueckfrage mit field_affected='amount' (Komma-Parsing)."""

    async def test_answer_amount_with_comma(self, client, db_session: AsyncSession):
        """'119,50' soll zu 119.5 geparst werden."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Wie hoch ist der Betrag?",
            field_affected="amount",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "119,50"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["amount"] == 119.5

    async def test_answer_amount_with_dot(self, client, db_session: AsyncSession):
        """'250.75' soll zu 250.75 geparst werden."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Wie hoch ist der Betrag?",
            field_affected="amount",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "250.75"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["amount"] == 250.75

    async def test_answer_amount_invalid_text(self, client, db_session: AsyncSession):
        """'abc' soll nicht crashen (ValueError wird gefangen)."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, amount=100.0)
        q = await _create_question(
            db_session, doc,
            question="Wie hoch ist der Betrag?",
            field_affected="amount",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "abc"},
        )
        assert resp.status_code == 200
        # Betrag sollte unveraendert bleiben
        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["amount"] == 100.0


@pytest.mark.asyncio
class TestAnswerFieldUpdateDocumentDate:
    """Beantworte Rueckfrage mit field_affected='document_date'."""

    async def test_answer_date_dd_mm_yyyy(self, client, db_session: AsyncSession):
        """'15.01.2024' (DD.MM.YYYY) soll geparst werden."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Wann wurde das Dokument ausgestellt?",
            field_affected="document_date",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "15.01.2024"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["document_date"] == "2024-01-15"

    async def test_answer_date_yyyy_mm_dd(self, client, db_session: AsyncSession):
        """'2024-01-15' (YYYY-MM-DD) soll geparst werden."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Wann wurde das Dokument ausgestellt?",
            field_affected="document_date",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "2024-01-15"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["document_date"] == "2024-01-15"

    async def test_answer_date_invalid(self, client, db_session: AsyncSession):
        """Ungueltiges Datum soll nicht crashen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Wann wurde das Dokument ausgestellt?",
            field_affected="document_date",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "kein datum"},
        )
        assert resp.status_code == 200
        # document_date bleibt None
        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["document_date"] is None


@pytest.mark.asyncio
class TestAnswerFieldUpdateDocumentType:
    """Beantworte Rueckfrage mit field_affected='document_type'."""

    async def test_answer_type_valid(self, client, db_session: AsyncSession):
        """'RECHNUNG' soll DocumentType.RECHNUNG setzen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(
            db_session, scope, document_type=DocumentType.SONSTIGES
        )
        q = await _create_question(
            db_session, doc,
            question="Welcher Dokumenttyp?",
            field_affected="document_type",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "RECHNUNG"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["document_type"] == "RECHNUNG"

    async def test_answer_type_invalid(self, client, db_session: AsyncSession):
        """'INVALID_TYPE' soll nicht crashen, Typ bleibt unveraendert."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(
            db_session, scope, document_type=DocumentType.SONSTIGES
        )
        q = await _create_question(
            db_session, doc,
            question="Welcher Dokumenttyp?",
            field_affected="document_type",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "INVALID_TYPE"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["document_type"] == "SONSTIGES"


@pytest.mark.asyncio
class TestAnswerFieldUpdateTaxRelevant:
    """Beantworte Rueckfrage mit field_affected='tax_relevant'."""

    async def test_answer_tax_relevant_ja(self, client, db_session: AsyncSession):
        """'ja' soll tax_relevant=True setzen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Ist das Dokument steuerrelevant?",
            field_affected="tax_relevant",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "ja"},
        )
        assert resp.status_code == 200

        # tax_relevant ueber documents detail pruefen
        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.status_code == 200
        assert detail.json()["tax_relevant"] is True

    async def test_answer_tax_relevant_nein(self, client, db_session: AsyncSession):
        """'nein' soll tax_relevant=False setzen (nicht in ja/yes/true/1)."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Ist das Dokument steuerrelevant?",
            field_affected="tax_relevant",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "nein"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.json()["tax_relevant"] is False

    async def test_answer_tax_relevant_yes(self, client, db_session: AsyncSession):
        """'yes' soll tax_relevant=True setzen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Steuerrelevant?",
            field_affected="tax_relevant",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "yes"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.json()["tax_relevant"] is True

    async def test_answer_tax_relevant_1(self, client, db_session: AsyncSession):
        """'1' soll tax_relevant=True setzen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Steuerrelevant?",
            field_affected="tax_relevant",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "1"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.json()["tax_relevant"] is True


@pytest.mark.asyncio
class TestAnswerFieldUpdateFilingScope:
    """Beantworte Rueckfrage mit field_affected='filing_scope'."""

    async def test_answer_existing_scope(self, client, db_session: AsyncSession):
        """Bestehender Ablagebereich-Name soll filing_scope_id setzen."""
        scope = await _create_scope(db_session, name="Privat", slug="privat")
        scope2 = await _create_scope(
            db_session, name="Geschaeftlich", slug="geschaeftlich", is_default=False
        )
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Welcher Ablagebereich?",
            field_affected="filing_scope",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Geschaeftlich"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["filing_scope"] == "Geschaeftlich"

    async def test_answer_new_scope(self, client, db_session: AsyncSession):
        """'NEU: Praxis' soll neuen Ablagebereich erstellen und zuweisen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Welcher Ablagebereich?",
            field_affected="filing_scope",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "NEU: Praxis"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["filing_scope"] == "Praxis"

    async def test_answer_new_scope_creates_record(self, client, db_session: AsyncSession):
        """'NEU: Verein' soll ein FilingScope-Objekt in der DB erstellen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Welcher Ablagebereich?",
            field_affected="filing_scope",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "NEU: Verein"},
        )
        assert resp.status_code == 200

        # Pruefen ob der Scope in der DB existiert
        result = await db_session.execute(
            select(FilingScope).where(FilingScope.name == "Verein")
        )
        new_scope = result.scalar_one_or_none()
        assert new_scope is not None
        assert new_scope.slug == "verein"
        assert new_scope.is_default is False

    async def test_answer_nonexistent_scope_ignored(self, client, db_session: AsyncSession):
        """Nicht existierender Scope-Name (ohne NEU:) soll keine Aenderung bewirken."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Welcher Ablagebereich?",
            field_affected="filing_scope",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "GibtEsNicht"},
        )
        assert resp.status_code == 200

        # Scope bleibt beim Original
        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["filing_scope"] == "Privat"


# =========================================================================
# Tests: Vollstaendiger Review-Workflow
# =========================================================================


@pytest.mark.asyncio
class TestReviewWorkflow:
    """End-to-End Review-Workflow mit echten Daten."""

    async def test_pending_returns_document(self, client, db_session: AsyncSession):
        """Ein Dokument mit NEEDS_REVIEW soll in /review/pending erscheinen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        await _create_question(db_session, doc)
        await db_session.commit()

        resp = await client.get("/api/review/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["documents"][0]["id"] == doc.id
        assert data["documents"][0]["open_questions"] == 1
        assert data["documents"][0]["total_questions"] == 1

    async def test_pending_excludes_deleted(self, client, db_session: AsyncSession):
        """Geloeschte Dokumente mit NEEDS_REVIEW sollen NICHT erscheinen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(
            db_session, scope, status=DocumentStatus.DELETED
        )
        await _create_question(db_session, doc)
        await db_session.commit()

        resp = await client.get("/api/review/pending")
        assert resp.json()["total"] == 0

    async def test_pending_excludes_reviewed(self, client, db_session: AsyncSession):
        """Bereits reviewed Dokumente sollen NICHT in pending erscheinen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(
            db_session, scope, review_status=ReviewStatus.REVIEWED
        )
        await _create_question(db_session, doc)
        await db_session.commit()

        resp = await client.get("/api/review/pending")
        assert resp.json()["total"] == 0

    async def test_document_detail_returns_questions(self, client, db_session: AsyncSession):
        """GET /review/documents/{id} soll Fragen mit Details zurueckgeben."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, summary="Zusammenfassung")
        q1 = await _create_question(
            db_session, doc,
            question="Frage 1",
            field_affected="title",
            priority=1,
        )
        q2 = await _create_question(
            db_session, doc,
            question="Frage 2",
            field_affected="amount",
            priority=5,
        )
        await db_session.commit()

        resp = await client.get(f"/api/review/documents/{doc.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc.id
        assert data["summary"] == "Zusammenfassung"
        assert len(data["questions"]) == 2
        # Hoechste Prioritaet zuerst
        assert data["questions"][0]["priority"] == 5
        assert data["questions"][1]["priority"] == 1

    async def test_document_detail_ocr_text_truncated(self, client, db_session: AsyncSession):
        """OCR-Text soll auf 2000 Zeichen begrenzt werden."""
        scope = await _create_scope(db_session)
        long_text = "A" * 3000
        doc = await _create_doc(db_session, scope, ocr_text=long_text)
        await db_session.commit()

        resp = await client.get(f"/api/review/documents/{doc.id}")
        assert resp.status_code == 200
        assert len(resp.json()["ocr_text"]) == 2000

    async def test_document_detail_confident_fields(self, client, db_session: AsyncSession):
        """confident_fields soll alle relevanten Felder enthalten."""
        scope = await _create_scope(db_session, name="Privat", slug="privat")
        doc = await _create_doc(
            db_session, scope,
            title="Mein Dokument",
            issuer="Muster AG",
            amount=99.99,
            currency="EUR",
        )
        await db_session.commit()

        resp = await client.get(f"/api/review/documents/{doc.id}")
        data = resp.json()
        cf = data["confident_fields"]
        assert cf["title"] == "Mein Dokument"
        assert cf["issuer"] == "Muster AG"
        assert cf["amount"] == 99.99
        assert cf["currency"] == "EUR"
        assert cf["filing_scope"] == "Privat"

    async def test_answer_all_questions_returns_all_answered(self, client, db_session: AsyncSession):
        """Nach Beantwortung aller Fragen soll all_answered=True sein."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q1 = await _create_question(
            db_session, doc,
            question="Frage 1",
            field_affected="title",
        )
        q2 = await _create_question(
            db_session, doc,
            question="Frage 2",
            field_affected="issuer",
        )
        await db_session.commit()

        # Erste Frage beantworten
        resp1 = await client.post(
            f"/api/review/questions/{q1.id}/answer",
            json={"answer": "Antwort 1"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["all_answered"] is False

        # Zweite Frage beantworten
        resp2 = await client.post(
            f"/api/review/questions/{q2.id}/answer",
            json={"answer": "Antwort 2"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["all_answered"] is True

    async def test_approve_sets_reviewed(self, client, db_session: AsyncSession):
        """POST /review/documents/{id}/approve soll review_status auf REVIEWED setzen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        await db_session.commit()

        resp = await client.post(f"/api/review/documents/{doc.id}/approve")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["review_status"] == "REVIEWED"

        # Dokument sollte nicht mehr in pending erscheinen
        pending = await client.get("/api/review/pending")
        assert pending.json()["total"] == 0

    async def test_skip_sets_ok(self, client, db_session: AsyncSession):
        """POST /review/documents/{id}/skip soll review_status auf OK setzen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        await db_session.commit()

        resp = await client.post(f"/api/review/documents/{doc.id}/skip")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Dokument sollte nicht mehr in pending erscheinen
        pending = await client.get("/api/review/pending")
        assert pending.json()["total"] == 0

    async def test_skip_not_found(self, client):
        """POST /review/documents/{id}/skip mit ungueltigem ID soll 404 geben."""
        resp = await client.post("/api/review/documents/nonexistent/skip")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestReviewStatsWithData:
    """Review-Statistiken mit echten Daten."""

    async def test_stats_counts(self, client, db_session: AsyncSession):
        """Stats sollen offene Dokumente und Fragen korrekt zaehlen."""
        scope = await _create_scope(db_session)

        # Dokument 1: 2 offene Fragen
        doc1 = await _create_doc(db_session, scope, file_hash="stats_h1")
        await _create_question(db_session, doc1, question="F1", field_affected="title")
        await _create_question(db_session, doc1, question="F2", field_affected="issuer")

        # Dokument 2: 1 offene + 1 beantwortete Frage
        doc2 = await _create_doc(db_session, scope, file_hash="stats_h2")
        await _create_question(db_session, doc2, question="F3", field_affected="amount")
        await _create_question(
            db_session, doc2,
            question="F4", field_affected="title",
            is_answered=True, answer="Antwort",
        )

        await db_session.commit()

        resp = await client.get("/api/review/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["open_documents"] == 2
        assert data["open_questions"] == 3  # F1 + F2 + F3
        assert data["answered_questions"] == 1  # F4

    async def test_stats_excludes_deleted_documents(self, client, db_session: AsyncSession):
        """Geloeschte Dokumente sollen nicht in open_documents gezaehlt werden."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(
            db_session, scope,
            status=DocumentStatus.DELETED,
        )
        await _create_question(db_session, doc, question="F1")
        await db_session.commit()

        resp = await client.get("/api/review/stats")
        data = resp.json()
        assert data["open_documents"] == 0
        # Offene Fragen werden trotzdem gezaehlt (kein Status-Filter auf ReviewQuestion)
        assert data["open_questions"] == 1


# =========================================================================
# Tests: CorrectionMapping-Erstellung bei Feld-Updates
# =========================================================================


@pytest.mark.asyncio
class TestCorrectionMappingCreation:
    """Testen, dass CorrectionMappings bei Aenderungen erstellt werden."""

    async def test_title_change_creates_correction(self, client, db_session: AsyncSession):
        """Aenderung von title soll CorrectionMapping erstellen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, title="Alter Titel")
        q = await _create_question(
            db_session, doc,
            question="Titel?",
            field_affected="title",
        )
        await db_session.commit()

        await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Neuer Titel"},
        )

        result = await db_session.execute(
            select(CorrectionMapping).where(
                CorrectionMapping.field == "title"
            )
        )
        mapping = result.scalar_one_or_none()
        assert mapping is not None
        assert mapping.original_value == "Alter Titel"
        assert mapping.corrected_value == "Neuer Titel"
        assert mapping.occurrence_count == 1
        assert mapping.auto_apply is False

    async def test_issuer_change_creates_correction(self, client, db_session: AsyncSession):
        """Aenderung von issuer soll CorrectionMapping erstellen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, issuer="Firma A")
        q = await _create_question(
            db_session, doc,
            question="Aussteller?",
            field_affected="issuer",
        )
        await db_session.commit()

        await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Firma B"},
        )

        result = await db_session.execute(
            select(CorrectionMapping).where(CorrectionMapping.field == "issuer")
        )
        mapping = result.scalar_one_or_none()
        assert mapping is not None
        assert mapping.original_value == "Firma A"
        assert mapping.corrected_value == "Firma B"

    async def test_same_value_no_correction(self, client, db_session: AsyncSession):
        """Gleicher Wert soll kein CorrectionMapping erstellen."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, title="Gleicher Titel")
        q = await _create_question(
            db_session, doc,
            question="Titel?",
            field_affected="title",
        )
        await db_session.commit()

        await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Gleicher Titel"},
        )

        result = await db_session.execute(select(CorrectionMapping))
        mappings = result.scalars().all()
        assert len(mappings) == 0


# =========================================================================
# Tests: Reanalyze-Endpoint
# =========================================================================


@pytest.mark.asyncio
class TestReanalyzeEndpoint:
    """Tests fuer POST /review/documents/{id}/reanalyze."""

    async def test_reanalyze_not_found(self, client):
        """Nicht existierendes Dokument soll 404 geben."""
        resp = await client.post("/api/review/documents/nonexistent/reanalyze")
        assert resp.status_code == 404

    async def test_reanalyze_no_ocr_text(self, client, db_session: AsyncSession):
        """Dokument ohne OCR-Text soll 400 geben."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, ocr_text="")
        await db_session.commit()

        resp = await client.post(f"/api/review/documents/{doc.id}/reanalyze")
        assert resp.status_code == 400
        assert "OCR-Text" in resp.json()["detail"]

    async def test_reanalyze_whitespace_only_ocr(self, client, db_session: AsyncSession):
        """Dokument mit nur Whitespace als OCR-Text soll 400 geben."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, ocr_text="   \n\t  ")
        await db_session.commit()

        resp = await client.post(f"/api/review/documents/{doc.id}/reanalyze")
        assert resp.status_code == 400


# =========================================================================
# Tests: Weitere field_affected Branches
# =========================================================================


@pytest.mark.asyncio
class TestAnswerFieldUpdateRecipient:
    """Beantworte Rueckfrage mit field_affected='recipient'."""

    async def test_answer_updates_recipient(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Wer ist der Empfaenger?",
            field_affected="recipient",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Max Mustermann"},
        )
        assert resp.status_code == 200

        # Empfaenger ueber Dokument-Detail pruefen
        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.status_code == 200
        assert detail.json()["recipient"] == "Max Mustermann"


@pytest.mark.asyncio
class TestAnswerFieldUpdateSummary:
    """Beantworte Rueckfrage mit field_affected='summary'."""

    async def test_answer_updates_summary(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Kurzbeschreibung?",
            field_affected="summary",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Rechnung fuer Reparatur"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["summary"] == "Rechnung fuer Reparatur"


@pytest.mark.asyncio
class TestAnswerFieldUpdateReferenceNumber:
    """Beantworte Rueckfrage mit field_affected='reference_number'."""

    async def test_answer_updates_reference_number(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Rechnungsnummer?",
            field_affected="reference_number",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "2024-001"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.json()["reference_number"] == "2024-001"


@pytest.mark.asyncio
class TestAnswerWithNoFieldAffected:
    """Beantworte Rueckfrage ohne field_affected (nur Frage+Antwort)."""

    async def test_answer_without_field_still_marks_answered(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Allgemeine Frage ohne Feldbezug",
            field_affected=None,
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Ja, ist korrekt"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["all_answered"] is True


@pytest.mark.asyncio
class TestAnswerAlreadyAnsweredQuestion:
    """Bereits beantwortete Frage erneut beantworten."""

    async def test_re_answer_overwrites(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope, title="Original")
        q = await _create_question(
            db_session, doc,
            question="Titel?",
            field_affected="title",
            is_answered=True,
            answer="Erste Antwort",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "Zweite Antwort"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["title"] == "Zweite Antwort"


@pytest.mark.asyncio
class TestAnswerDateSlashFormat:
    """Beantworte Rueckfrage mit Datum im Slash-Format (DD/MM/YYYY)."""

    async def test_answer_date_dd_slash_mm_slash_yyyy(self, client, db_session: AsyncSession):
        """'15/01/2024' soll geparst werden (DD/MM/YYYY)."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Datum?",
            field_affected="document_date",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "15/01/2024"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["document_date"] == "2024-01-15"


@pytest.mark.asyncio
class TestNewScopeDeduplication:
    """'NEU: Praxis' soll keinen Duplikat erstellen wenn Scope existiert."""

    async def test_new_scope_reuses_existing(self, client, db_session: AsyncSession):
        scope = await _create_scope(db_session, name="Privat", slug="privat")
        praxis = await _create_scope(
            db_session, name="Praxis", slug="praxis", is_default=False
        )
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Ablagebereich?",
            field_affected="filing_scope",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "NEU: Praxis"},
        )
        assert resp.status_code == 200

        # Kein neuer Scope erstellt, bestehender wiederverwendet
        result = await db_session.execute(
            select(FilingScope).where(FilingScope.name == "Praxis")
        )
        scopes = result.scalars().all()
        assert len(scopes) == 1

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["filing_scope"] == "Praxis"


@pytest.mark.asyncio
class TestAmountWithSpaces:
    """Betrag mit Leerzeichen soll korrekt geparst werden."""

    async def test_answer_amount_with_spaces(self, client, db_session: AsyncSession):
        """'1 250,50' soll zu 1250.5 geparst werden."""
        scope = await _create_scope(db_session)
        doc = await _create_doc(db_session, scope)
        q = await _create_question(
            db_session, doc,
            question="Betrag?",
            field_affected="amount",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/review/questions/{q.id}/answer",
            json={"answer": "1 250,50"},
        )
        assert resp.status_code == 200

        detail = await client.get(f"/api/review/documents/{doc.id}")
        assert detail.json()["confident_fields"]["amount"] == 1250.5
