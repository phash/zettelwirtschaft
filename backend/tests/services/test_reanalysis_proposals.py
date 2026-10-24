"""Neu-Analyse mit Aenderungsvorschlag (Issue #39): Vorschlag anlegen, Diff
berechnen, ganz oder teilweise uebernehmen, verwerfen."""

import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
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
from app.services.task_service import TaskError
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


async def test_apply_conflicts_when_proposal_replaced_meanwhile(
    db_session, test_session_factory, test_settings, fake_pipeline, monkeypatch,
):
    """Ersetzt eine neue Analyse den Vorschlag waehrend der Uebernahme: 409 statt 500, nichts geaendert."""
    from app.services import reanalysis_service

    privat = await _scope(db_session)
    doc = await _doc(db_session, privat, issuer="Alt GmbH")
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)
    original_plan = reanalysis_service._plan_changes

    async def plan_then_replace(*args, **kwargs):
        plan = await original_plan(*args, **kwargs)
        # Wie create_proposal: alten Vorschlag loeschen, neuen anlegen (andere Session).
        async with test_session_factory() as other:
            old = await get_open_proposal(other, doc_id)
            await other.delete(old)
            await other.flush()
            other.add(ReanalysisProposal(
                document_id=doc_id, model=old.model, with_ocr=False, analysis=old.analysis,
            ))
            await other.commit()
        monkeypatch.setattr(reanalysis_service, "_plan_changes", original_plan)
        return plan

    monkeypatch.setattr(reanalysis_service, "_plan_changes", plan_then_replace)

    with pytest.raises(ProposalConflict) as exc_info:
        await _apply(test_session_factory, doc_id, test_settings)

    assert exc_info.value.message == reanalysis_service.PROPOSAL_REPLACED_MESSAGE
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.issuer == "Alt GmbH"
    assert d.analysis_model is None
    assert len(await _proposals(test_session_factory)) == 1  # der neue Vorschlag bleibt
    assert await _audits(test_session_factory, doc_id) == []


async def test_partial_apply_ignores_needs_review_of_rejected_fields(
    db_session, test_session_factory, test_settings, fake_pipeline,
):
    # needs_review z.B. wegen unplausiblem Betrag — der Betrag wird abgelehnt.
    fake_pipeline["result"] = make_analysis(needs_review=True, amount=60000.0)
    doc = await _doc(db_session, await _scope(db_session), tags=["arzt"])
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)

    assert await _apply(test_session_factory, doc_id, test_settings, {"tags"}) == ["tags"]

    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.review_status == ReviewStatus.OK
    assert d.amount == Decimal("95.50")
    assert sorted(t.name for t in d.tags) == ["arzt", "gesundheit"]


async def test_full_apply_honours_needs_review(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(needs_review=True, amount=60000.0)
    doc = await _doc(db_session, await _scope(db_session), tags=["arzt"])
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)

    assert await _apply(test_session_factory, doc_id, test_settings) == ["amount", "tags"]

    assert (await _fresh_doc(test_session_factory, doc_id)).review_status == ReviewStatus.NEEDS_REVIEW


async def test_rejected_date_keeps_derived_values_on_old_date(
    db_session, test_session_factory, test_settings, fake_pipeline,
):
    fake_pipeline["result"] = make_analysis(warranty_info={
        "has_warranty": True, "product_name": "Laptop", "purchase_date": None,
        "warranty_duration_months": 24, "store_name": "Shop",
    })
    doc = await _doc(db_session, await _scope(db_session), document_date=date(2024, 12, 1), tax_year=None)
    doc_id = doc.id
    outcome = await _create(test_session_factory, doc_id, test_settings)
    preview = {c.field: c for c in outcome.changes}
    assert preview["warranty"].new["purchase_date"] == "2025-02-14"  # Vorschau: neues Datum
    assert preview["tax_year"].new == 2025

    applied = await _apply(test_session_factory, doc_id, test_settings, {"warranty", "tax_year"})

    assert applied == ["tax_year", "warranty"]
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.document_date == date(2024, 12, 1)
    assert d.tax_year == 2024
    assert (d.warranty_info.purchase_date, d.warranty_info.warranty_end_date) == (date(2024, 12, 1), date(2026, 12, 1))
    assert (await _audits(test_session_factory, doc_id))[0]["rejected"] == ["document_date"]


async def test_apply_uses_given_session_factory(
    db_session, test_session_factory, test_settings, fake_pipeline, monkeypatch,
):
    doc = await _doc(db_session, await _scope(db_session), issuer="Alt GmbH")
    doc_id = doc.id
    await _create(test_session_factory, doc_id, test_settings)
    seen = []

    async def fake_revectorize(document_id, settings, session_factory):
        seen.append(session_factory)

    from app.services import vectorize_service
    monkeypatch.setattr(vectorize_service, "revectorize_document_safe", fake_revectorize)
    async with test_session_factory() as session:
        await apply_proposal(session, doc_id, test_settings, session_factory=test_session_factory)

    assert seen == [test_session_factory]


async def _user_audit(db: AsyncSession, doc_id: str, action: AuditAction, details: dict) -> None:
    db.add(AuditLog(document_id=doc_id, action=action, details=json.dumps(details)))
    await db.commit()


async def test_user_edited_warranty_not_proposed(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(
        sender="Neue Praxis",
        warranty_info={"has_warranty": True, "product_name": "Laptop", "warranty_duration_months": 24},
    )
    doc = await _doc(db_session, await _scope(db_session))
    await _user_audit(db_session, doc.id, AuditAction.UPDATED, {"warranty_info.product_name": {"old": "A", "new": "B"}})

    outcome = await _create(test_session_factory, doc.id, test_settings)

    assert [c.field for c in outcome.changes] == ["issuer"]


async def test_user_set_scope_not_proposed(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(sender="Neue Praxis")
    privat = await _scope(db_session)
    await _scope(db_session, name="Praxis", slug="praxis", is_default=False, keywords='["Dr. Muster"]')
    doc = await _doc(db_session, privat)
    await _user_audit(db_session, doc.id, AuditAction.UPDATED, {"filing_scope_id": {"old": None, "new": privat.id}})

    outcome = await _create(test_session_factory, doc.id, test_settings)

    assert [c.field for c in outcome.changes] == ["issuer"]


async def test_manually_removed_tag_not_reproposed(db_session, test_session_factory, test_settings, fake_pipeline):
    fake_pipeline["result"] = make_analysis(sender="Neue Praxis")
    doc = await _doc(db_session, await _scope(db_session), tags=["arzt"])
    await _user_audit(db_session, doc.id, AuditAction.TAG_REMOVED, {"tag": "gesundheit"})

    outcome = await _create(test_session_factory, doc.id, test_settings)

    assert [c.field for c in outcome.changes] == ["issuer"]


# ---------------------------------------------------------------------------
# Option "Text neu erkennen"
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ocr(monkeypatch):
    """Ersetzt ocr_service.extract_text im reanalysis_service; zeichnet Aufrufe auf."""
    state = {"result": None, "calls": []}

    async def fake_extract(file_path, file_type, settings):
        state["calls"].append((file_path, file_type))
        return state["result"]

    monkeypatch.setattr("app.services.reanalysis_service.extract_text", fake_extract)
    return state


def _ocr_result(text_: str, confidence: float):
    from app.services.ocr_service import OcrResult
    return OcrResult(full_text=text_, page_count=1, average_confidence=confidence)


def _archived_file(tmp_path) -> Path:
    archived = tmp_path / "beleg.pdf"
    archived.write_bytes(b"%PDF-1.4")
    return archived


async def _create_ocr(session_factory, doc_id: str, settings):
    return await create_proposal(None, session_factory, doc_id, settings, with_ocr=True)


async def test_ocr_option_uses_new_text(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, tmp_path,
):
    archived = _archived_file(tmp_path)
    doc = await _doc(db_session, await _scope(db_session), file_path=str(archived), ocr_confidence=0.6)
    doc_id = doc.id
    fake_ocr["result"] = _ocr_result("Neuer, besserer Text", 0.9)

    outcome = await _create_ocr(test_session_factory, doc_id, test_settings)

    assert fake_pipeline["calls"][0]["pre_ocr_text"] == "Neuer, besserer Text"
    assert outcome.status == "proposed"
    assert outcome.ocr_note is None
    change = next(c for c in outcome.changes if c.field == "ocr_text")
    assert change.new == {"text": "Neuer, besserer Text", "confidence": 0.9}
    assert change.old["confidence"] == 0.6
    assert fake_ocr["calls"] == [(archived, "pdf")]
    proposals = await _proposals(test_session_factory)
    assert proposals[0].with_ocr is True
    assert proposals[0].ocr_text == "Neuer, besserer Text"
    # Das Dokument bleibt unveraendert, bis der Vorschlag uebernommen wird.
    assert (await _fresh_doc(test_session_factory, doc_id)).ocr_text == "Rechnung der Praxis Dr. Muster"


async def test_ocr_only_change_is_substantive(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, tmp_path,
):
    doc = await _doc(
        db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)), ocr_confidence=0.6,
    )
    fake_ocr["result"] = _ocr_result("Anderer Text", 0.9)

    outcome = await _create_ocr(test_session_factory, doc.id, test_settings)

    assert [c.field for c in outcome.changes] == ["ocr_text"]
    assert outcome.status == "proposed"


async def test_ocr_worse_is_discarded(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, tmp_path,
):
    doc = await _doc(
        db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)),
        ocr_confidence=0.8, issuer="Alt GmbH",
    )
    fake_ocr["result"] = _ocr_result("Verrauschter Text", 0.4)

    outcome = await _create_ocr(test_session_factory, doc.id, test_settings)

    assert outcome.ocr_note == "discarded_worse"
    assert fake_pipeline["calls"][0]["pre_ocr_text"] == "Rechnung der Praxis Dr. Muster"
    assert "ocr_text" not in {c.field for c in outcome.changes}
    proposals = await _proposals(test_session_factory)
    assert proposals[0].ocr_text is None
    assert proposals[0].ocr_note == "discarded_worse"


async def test_ocr_failure_falls_back_to_old_text(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, tmp_path,
):
    doc = await _doc(
        db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)), issuer="Alt GmbH",
    )
    fake_ocr["result"] = None

    outcome = await _create_ocr(test_session_factory, doc.id, test_settings)

    assert outcome.ocr_note == "discarded_worse"
    assert fake_pipeline["calls"][0]["pre_ocr_text"] == "Rechnung der Praxis Dr. Muster"


async def test_ocr_works_without_stored_text(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, tmp_path,
):
    doc = await _doc(
        db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)),
        ocr_text=None, ocr_confidence=None,
    )
    fake_ocr["result"] = _ocr_result("Erstmals erkannter Text", 0.7)

    outcome = await _create_ocr(test_session_factory, doc.id, test_settings)

    assert outcome.status == "proposed"
    assert fake_pipeline["calls"][0]["pre_ocr_text"] == "Erstmals erkannter Text"
    assert any(c.field == "ocr_text" for c in outcome.changes)


async def test_ocr_without_any_text_is_task_error(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, tmp_path,
):
    doc = await _doc(db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)), ocr_text=None)
    fake_ocr["result"] = None

    with pytest.raises(TaskError):
        await _create_ocr(test_session_factory, doc.id, test_settings)
    assert fake_pipeline["calls"] == []


async def test_ocr_missing_file_is_task_error(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr,
):
    doc = await _doc(db_session, await _scope(db_session))  # file_path zeigt ins Leere

    with pytest.raises(TaskError, match="Datei"):
        await _create_ocr(test_session_factory, doc.id, test_settings)
    assert fake_ocr["calls"] == []


async def test_ocr_releases_analysis_slot_before_analysis(
    db_session, test_session_factory, test_settings, monkeypatch, tmp_path,
):
    """Die OCR haelt den Slot, gibt ihn vor der Analyse aber wieder frei (nicht reentrant)."""
    from app.services.analysis_service import analysis_slot

    doc = await _doc(db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)))
    seen = {}

    async def fake_extract(file_path, file_type, settings):
        seen["locked_during_ocr"] = analysis_slot().locked()
        return _ocr_result("Neuer Text", 0.99)

    async def fake_analyze(file_path, file_type, settings, filing_scopes=None, session=None, pre_ocr_text=None):
        seen["locked_during_analyze"] = analysis_slot().locked()
        return None, make_analysis()

    monkeypatch.setattr("app.services.reanalysis_service.extract_text", fake_extract)
    monkeypatch.setattr("app.services.reanalysis_service.analyze_document", fake_analyze)

    await _create_ocr(test_session_factory, doc.id, test_settings)

    assert seen == {"locked_during_ocr": True, "locked_during_analyze": False}


async def test_apply_ocr_updates_text_and_fts(
    db_session, test_session_factory, test_settings, fake_pipeline, fake_ocr, vectorize_calls, tmp_path,
):
    doc = await _doc(
        db_session, await _scope(db_session), file_path=str(_archived_file(tmp_path)), ocr_confidence=0.6,
    )
    doc_id = doc.id
    fake_ocr["result"] = _ocr_result("Zahnarztrechnung Blutdruckmessung", 0.9)
    await _create_ocr(test_session_factory, doc_id, test_settings)

    applied = await _apply(test_session_factory, doc_id, test_settings, {"ocr_text"})

    assert applied == ["ocr_text"]
    d = await _fresh_doc(test_session_factory, doc_id)
    assert d.ocr_text == "Zahnarztrechnung Blutdruckmessung"
    assert d.ocr_confidence == pytest.approx(0.9)
    async with test_session_factory() as session:
        hits = (await session.execute(
            text("SELECT doc_id FROM documents_fts WHERE documents_fts MATCH 'Blutdruckmessung'")
        )).all()
    assert [h[0] for h in hits] == [doc_id]
    assert vectorize_calls == [doc_id]
