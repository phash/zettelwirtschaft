"""Neu-Analyse mit Aenderungsvorschlag (Issue #39): Vorschlag anlegen, Diff
berechnen, ganz oder teilweise uebernehmen, verwerfen."""

import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentTag,
    DocumentType,
    ReviewStatus,
    Tag,
)
from app.models.filing_scope import FilingScope
from app.models.reanalysis import ReanalysisProposal
from app.models.review_question import ReviewQuestion
from app.services.reanalysis_service import (
    FieldChange,
    ProposalConflict,
    apply_proposal,
    compute_changes,
    create_proposal,
    discard_proposal,
    get_open_proposal,
    is_substantive,
    load_document_for_review,
)
from tests.factories import make_document
from tests.support.reanalysis_fixtures import make_analysis


@pytest.fixture(autouse=True)
def vectorize_calls(monkeypatch):
    """Keine echten Chroma/Ollama-Aufrufe; Revektorisierungen werden mitgeschrieben."""
    calls: list[str] = []

    async def fake_revectorize(document_id, settings, session_factory):
        calls.append(document_id)

    from app.services import vectorize_service
    monkeypatch.setattr(vectorize_service, "revectorize_document_safe", fake_revectorize)
    return calls


async def _scope(db: AsyncSession, name="Privat", slug="privat", is_default=True, keywords=None) -> FilingScope:
    scope = FilingScope(name=name, slug=slug, is_default=is_default, keywords=keywords)
    db.add(scope)
    await db.flush()
    return scope


async def _doc(db: AsyncSession, scope: FilingScope, **overrides) -> Document:
    """Dokument, das bis auf ``overrides`` genau dem Ergebnis von ``make_analysis()`` entspricht."""
    tags = overrides.pop("tags", ["arzt", "gesundheit"])
    values = dict(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/nicht-da.pdf",
        file_type="pdf",
        file_size_bytes=10,
        file_hash=uuid.uuid4().hex,
        document_type=DocumentType.ARZTRECHNUNG,
        title="Arztrechnung Dr. Muster",
        issuer="Praxis Dr. Muster",
        recipient="Max Mustermann",
        document_date=date(2025, 2, 14),
        amount=Decimal("95.50"),
        currency="EUR",
        reference_number="R-2025-7",
        summary="Rechnung über eine Behandlung.",
        tax_relevant=True,
        tax_category="Aussergewoehnliche_Belastungen",
        tax_year=2025,
        ocr_text="Rechnung der Praxis Dr. Muster",
        ai_confidence=0.5,
        status=DocumentStatus.ACTIVE,
        review_status=ReviewStatus.OK,
        filing_scope_id=scope.id,
    )
    values.update(overrides)
    doc = await make_document(db, **values)
    for name in tags:
        tag = Tag(name=name, is_auto_generated=True)
        db.add(tag)
        await db.flush()
        db.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
    await db.commit()
    return doc


async def _create(session_factory, doc_id: str, settings):
    return await create_proposal(None, session_factory, doc_id, settings, with_ocr=False)


async def _changes(session_factory, doc_id: str, settings) -> list:
    async with session_factory() as session:
        doc = await load_document_for_review(session, doc_id)
        proposal = await get_open_proposal(session, doc_id)
        assert proposal is not None
        return await compute_changes(session, doc, proposal, settings)


async def _apply(session_factory, doc_id: str, settings, fields=None) -> list[str]:
    async with session_factory() as session:
        return await apply_proposal(session, doc_id, settings, fields)


async def _fresh_doc(session_factory, doc_id: str) -> Document:
    async with session_factory() as session:
        return await session.get(Document, doc_id)


async def _proposals(session_factory) -> list[ReanalysisProposal]:
    async with session_factory() as session:
        return list((await session.execute(select(ReanalysisProposal))).scalars().all())


async def _audits(session_factory, doc_id: str) -> list[dict]:
    async with session_factory() as session:
        rows = (await session.execute(
            select(AuditLog.details)
            .where(AuditLog.document_id == doc_id, AuditLog.action == AuditAction.REANALYZED)
            .order_by(AuditLog.id)
        )).scalars().all()
    return [json.loads(r) for r in rows]


async def test_create_proposal_does_not_touch_document(
    db_session, test_session_factory, test_settings, fake_pipeline,
):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH", amount=Decimal("10.00"), tags=["alt"])
    doc_id = doc.id

    outcome = await _create(test_session_factory, doc_id, test_settings)

    assert outcome.status == "proposed"
    assert {c.field for c in outcome.changes} == {"issuer", "amount", "tags"}
    assert fake_pipeline["calls"][0]["pre_ocr_text"] == "Rechnung der Praxis Dr. Muster"
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.issuer == "Alt GmbH"
    assert d.amount == Decimal("10.00")
    assert d.analysis_model is None
    assert [t.name for t in d.tags] == ["alt"]
    proposals = await _proposals(test_session_factory)
    assert len(proposals) == 1
    assert proposals[0].document_id == doc_id
    assert proposals[0].model == test_settings.OLLAMA_MODEL
    assert proposals[0].with_ocr is False


async def test_second_proposal_replaces_first(db_session, test_session_factory, test_settings, fake_pipeline):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH")
    doc_id = doc.id

    await _create(test_session_factory, doc_id, test_settings)
    fake_pipeline["result"] = make_analysis(sender="Zweite Praxis")
    await _create(test_session_factory, doc_id, test_settings)

    proposals = await _proposals(test_session_factory)
    assert len(proposals) == 1
    assert proposals[0].analysis["sender"] == "Zweite Praxis"


async def test_only_title_summary_changed_is_unchanged(
    db_session, test_session_factory, test_settings, fake_pipeline,
):
    doc = await _doc(db_session, await _scope(db_session), title="Alter Titel", summary="Alte Zusammenfassung")
    doc_id = doc.id

    outcome = await _create(test_session_factory, doc_id, test_settings)

    assert outcome.status == "unchanged"
    assert outcome.changes == []
    assert await _proposals(test_session_factory) == []
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.analysis_model == test_settings.OLLAMA_MODEL
    assert d.title == "Alter Titel"


def test_is_substantive_ignores_reworded_texts():
    title, summary = FieldChange("title", "a", "b"), FieldChange("summary", "a", "b")
    assert not is_substantive([title, summary])
    assert is_substantive([title, FieldChange("amount", "1.00", "2.00")])
    assert not is_substantive([])


async def test_identical_values_yield_no_change(db_session, test_session_factory, test_settings):
    doc = await _doc(db_session, await _scope(db_session))
    doc_id = doc.id
    async with test_session_factory() as session:
        loaded = await load_document_for_review(session, doc_id)
        proposal = ReanalysisProposal(
            document_id=doc_id, model=test_settings.OLLAMA_MODEL, analysis=make_analysis().to_dict(),
        )
        assert await compute_changes(session, loaded, proposal, test_settings) == []


async def test_changes_are_json_ready(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(warranty_info={
        "has_warranty": True, "product_name": "Laptop", "purchase_date": "2025-02-14",
        "warranty_duration_months": 24, "store_name": "Shop",
    })
    doc = await _doc(
        db_session, await _scope(db_session),
        document_type=DocumentType.RECHNUNG, document_date=date(2025, 1, 1), amount=None, tags=[],
    )
    changes = {c.field: c for c in (await _create(test_session_factory, doc.id, test_settings)).changes}

    assert (changes["document_type"].old, changes["document_type"].new) == ("RECHNUNG", "ARZTRECHNUNG")
    assert (changes["document_date"].old, changes["document_date"].new) == ("2025-01-01", "2025-02-14")
    assert (changes["amount"].old, changes["amount"].new) == (None, "95.50")
    assert (changes["tags"].old, changes["tags"].new) == ([], ["arzt", "gesundheit"])
    assert changes["warranty"].old is None
    assert changes["warranty"].new["warranty_end_date"] == "2027-02-14"
    assert changes["warranty"].new["retailer"] == "Shop"
    json.dumps([c.__dict__ for c in changes.values()])


async def test_protected_field_not_in_changes(client, db_session, test_session_factory, test_settings, fake_pipeline):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH")
    doc_id = doc.id
    assert (await client.patch(f"/api/documents/{doc_id}", json={"title": "Mein Titel"})).status_code == 200

    await _create(test_session_factory, doc_id, test_settings)

    fields = [c.field for c in await _changes(test_session_factory, doc_id, test_settings)]
    assert fields == ["issuer"]


async def test_user_edit_after_proposal_drops_field(
    client, db_session, test_session_factory, test_settings, fake_pipeline, vectorize_calls,
):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH")
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)
    assert [c.field for c in await _changes(test_session_factory, doc_id, test_settings)] == ["issuer"]

    resp = await client.patch(f"/api/documents/{doc_id}", json={"issuer": "Mein Aussteller"})
    assert resp.status_code == 200
    calls_after_edit = list(vectorize_calls)  # PATCH revektorisiert selbst

    assert await _changes(test_session_factory, doc_id, test_settings) == []
    assert await _apply(test_session_factory, doc_id, test_settings, {"issuer"}) == []
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.issuer == "Mein Aussteller"
    assert d.analysis_model == test_settings.OLLAMA_MODEL
    assert vectorize_calls == calls_after_edit  # nichts uebernommen -> keine Revektorisierung


async def test_partial_apply(db_session, test_session_factory, test_settings, fake_pipeline, vectorize_calls):
    fake_pipeline["result"] = make_analysis(
        needs_review=True,
        confidence=0.4,
        review_questions=[
            {"question": "Stimmt der Betrag?", "field_affected": "amount"},
            {"question": "Stimmt der Typ?", "field_affected": "document_type"},
            "Ist das eine Praxisrechnung?",
        ],
    )
    doc = await _doc(
        db_session, await _scope(db_session),
        amount=Decimal("10.00"), document_type=DocumentType.RECHNUNG, ai_confidence=0.8,
    )
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)

    applied = await _apply(test_session_factory, doc_id, test_settings, {"amount"})

    assert applied == ["amount"]
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.amount == Decimal("95.50")
    assert d.document_type == DocumentType.RECHNUNG
    assert d.ai_confidence == pytest.approx(0.4)
    assert d.analysis_model == test_settings.OLLAMA_MODEL
    assert d.review_status == ReviewStatus.NEEDS_REVIEW
    async with test_session_factory() as session:
        questions = (await session.execute(
            select(ReviewQuestion).where(ReviewQuestion.document_id == doc_id)
        )).scalars().all()
    assert {(q.question, q.field_affected) for q in questions} == {
        ("Stimmt der Betrag?", "amount"),
        ("Ist das eine Praxisrechnung?", None),
    }
    assert await _proposals(test_session_factory) == []
    assert await _audits(test_session_factory, doc_id) == [
        {"applied": ["amount"], "rejected": ["document_type"], "model": test_settings.OLLAMA_MODEL},
    ]
    assert vectorize_calls == [doc_id]


async def test_reviewed_stays_reviewed_after_apply(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(needs_review=True, review_questions=["Ist der Betrag korrekt?"])
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH", review_status=ReviewStatus.REVIEWED)
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)

    assert await _apply(test_session_factory, doc_id, test_settings) == ["issuer"]

    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.issuer == "Praxis Dr. Muster"
    assert d.review_status == ReviewStatus.REVIEWED


async def test_discard_sets_model_and_removes_proposal(db_session, test_session_factory, test_settings, fake_pipeline):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH")
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)

    async with test_session_factory() as session:
        assert await discard_proposal(session, doc_id) is True
        await session.commit()
    async with test_session_factory() as session:
        assert await discard_proposal(session, doc_id) is False

    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.issuer == "Alt GmbH"
    assert d.analysis_model == test_settings.OLLAMA_MODEL
    assert await _proposals(test_session_factory) == []
    assert await _audits(test_session_factory, doc_id) == [{"discarded": True}]


async def test_open_proposal_of_deleted_document_is_ignored(
    db_session, test_session_factory, test_settings, fake_pipeline,
):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH")
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)
    doc.status = DocumentStatus.DELETED
    await db_session.commit()

    async with test_session_factory() as session:
        assert await get_open_proposal(session, doc_id) is None


async def test_proposal_scope_deleted_is_dropped(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(filing_scope="Praxis", filing_scope_confidence=0.9)
    privat = await _scope(db_session)
    praxis = await _scope(db_session, name="Praxis", slug="praxis", is_default=False)
    doc = await _doc(db_session, privat)
    doc_id, praxis_id = doc.id, praxis.id

    outcome = await _create(test_session_factory, doc_id, test_settings)
    assert [(c.field, c.new) for c in outcome.changes] == [
        ("filing_scope_id", {"id": praxis_id, "name": "Praxis"}),
    ]

    await db_session.delete(praxis)
    await db_session.commit()

    assert await _changes(test_session_factory, doc_id, test_settings) == []
    assert await _apply(test_session_factory, doc_id, test_settings) == []
    assert (await _fresh_doc(test_session_factory, doc_id)).filing_scope_id == privat.id


async def test_apply_missing_file_conflict(db_session, test_session_factory, test_settings, fake_pipeline):
    archive = Path(test_settings.ARCHIVE_DIR)
    privat = await _scope(db_session)
    await _scope(db_session, name="Praxis", slug="praxis", is_default=False, keywords='["Dr. Muster"]')
    source = archive / "privat/2025/02/ARZTRECHNUNG/abc.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4")
    doc = await _doc(db_session, privat, file_path=str(source), issuer="Alt GmbH")
    doc_id = doc.id
    outcome = await _create(test_session_factory, doc_id, test_settings)
    assert {c.field for c in outcome.changes} == {"issuer", "filing_scope_id"}
    source.unlink()

    with pytest.raises(ProposalConflict):
        await _apply(test_session_factory, doc_id, test_settings)

    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.issuer == "Alt GmbH"
    assert d.filing_scope_id == privat.id
    assert d.file_path == str(source)
    assert d.analysis_model is None
    assert len(await _proposals(test_session_factory)) == 1
    assert await _audits(test_session_factory, doc_id) == []
