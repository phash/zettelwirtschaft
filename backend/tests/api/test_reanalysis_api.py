"""API der Neu-Analyse mit Aenderungsvorschlag (Issue #39)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.background_task import BackgroundTask, TaskStatus
from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.models.filing_scope import FilingScope
from app.models.reanalysis import ReanalysisProposal
from app.services.task_service import wait_for_task
from tests.factories import make_document
from tests.support.reanalysis_fixtures import make_analysis


@pytest.fixture(autouse=True)
def vectorize_calls(monkeypatch):
    """Keine echten Chroma/Ollama-Aufrufe."""
    calls: list[str] = []

    async def fake_revectorize(document_id, settings, session_factory):
        calls.append(document_id)

    async def fake_delete_vectors(document_id, settings):
        return None

    from app.services import vectorize_service
    monkeypatch.setattr(vectorize_service, "revectorize_document_safe", fake_revectorize)
    monkeypatch.setattr(vectorize_service, "delete_document_vectors", fake_delete_vectors)
    return calls


async def _doc(db, **overrides) -> Document:
    """Dokument, das bis auf ``overrides`` dem Ergebnis von ``make_analysis(tags=[])`` entspricht."""
    values = dict(
        file_path="/archive/nicht-da.pdf",
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
        review_status=ReviewStatus.OK,
    )
    values.update(overrides)
    doc = await make_document(db, **values)
    await db.commit()
    return doc


async def _propose(db, doc: Document, model="gemma-test", **analysis_overrides) -> ReanalysisProposal:
    analysis_overrides.setdefault("tags", [])
    proposal = ReanalysisProposal(
        document_id=doc.id,
        model=model,
        with_ocr=False,
        analysis=make_analysis(**analysis_overrides).to_dict(),
    )
    db.add(proposal)
    await db.commit()
    return proposal


async def _scopes(db) -> tuple[FilingScope, FilingScope]:
    privat = FilingScope(name="Privat", slug="privat", is_default=True)
    praxis = FilingScope(name="Praxis", slug="praxis", is_default=False, keywords='["Dr. Muster"]')
    db.add_all([privat, praxis])
    await db.flush()
    return privat, praxis


async def _proposal_count(session_factory) -> int:
    async with session_factory() as session:
        return len((await session.execute(select(ReanalysisProposal))).scalars().all())


# --- POST /review/documents/{id}/reanalyze -------------------------------


async def test_reanalyze_with_ocr_body(client, db_session, test_session_factory, fake_pipeline):
    doc = await _doc(db_session, ocr_text="")  # ohne Text: nur mit with_ocr erlaubt
    resp = await client.post(f"/api/review/documents/{doc.id}/reanalyze", json={"with_ocr": True})
    assert resp.status_code == 202
    assert resp.json()["kind"] == "reanalyze"
    await wait_for_task(resp.json()["id"], timeout=10)
    async with test_session_factory() as session:
        task = await session.get(BackgroundTask, resp.json()["id"])
    # Gestartet; die Datei fehlt im Archiv, das meldet der Vorgang.
    assert task.error and "Datei" in task.error


async def test_reanalyze_without_ocr_text_still_400_without_flag(client, db_session):
    doc = await _doc(db_session, ocr_text="")
    resp = await client.post(f"/api/review/documents/{doc.id}/reanalyze", json={"with_ocr": False})
    assert resp.status_code == 400
    assert "OCR-Text" in resp.json()["detail"]


@pytest.mark.parametrize("kwargs", [{}, {"content": b""}, {"json": {}}, {"json": None}])
async def test_reanalyze_empty_body_still_works(client, db_session, test_session_factory, fake_pipeline, kwargs):
    doc = await _doc(db_session, amount=Decimal("10"))
    resp = await client.post(f"/api/review/documents/{doc.id}/reanalyze", **kwargs)
    assert resp.status_code == 202
    await wait_for_task(resp.json()["id"], timeout=10)
    assert await _proposal_count(test_session_factory) == 1


# --- GET /reanalysis/proposals/{id} ---------------------------------------


async def test_get_proposal_404_when_none(client, db_session):
    doc = await _doc(db_session)
    assert (await client.get(f"/api/reanalysis/proposals/{doc.id}")).status_code == 404


async def test_get_proposal_has_german_labels(client, db_session):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0, sender="Neue Praxis")
    resp = await client.get(f"/api/reanalysis/proposals/{doc.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == doc.id
    assert body["document_title"] == doc.title
    assert body["model"] == "gemma-test"
    assert body["with_ocr"] is False
    changes = {c["field"]: c for c in body["changes"]}
    assert changes["amount"]["label"] == "Betrag"
    assert changes["issuer"]["label"] == "Aussteller"
    assert changes["issuer"]["old"] == "Praxis Dr. Muster"
    assert changes["issuer"]["new"] == "Neue Praxis"


# --- POST apply / discard --------------------------------------------------


async def test_apply_selected_fields(client, db_session, test_session_factory):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0, sender="Neue Praxis")
    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/apply", json={"fields": ["issuer"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == doc.id
    assert body["issuer"] == "Neue Praxis"
    assert float(body["amount"]) == 95.5
    assert body["analysis_model"] == "gemma-test"
    assert body["has_proposal"] is False
    assert await _proposal_count(test_session_factory) == 0


async def test_apply_without_body_applies_all(client, db_session):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0, sender="Neue Praxis")
    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/apply")
    assert resp.status_code == 200
    assert resp.json()["issuer"] == "Neue Praxis"
    assert float(resp.json()["amount"]) == 120.0


async def test_apply_tags_shows_in_response(client, db_session):
    doc = await _doc(db_session)
    await _propose(db_session, doc, tags=["neu"])
    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/apply", json={"fields": ["tags"]})
    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()["tags"]] == ["neu"]


async def test_apply_unknown_field_422(client, db_session, test_session_factory):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0)
    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/apply", json={"fields": ["gibtsnicht"]})
    assert resp.status_code == 422
    assert await _proposal_count(test_session_factory) == 1


async def test_apply_without_proposal_404(client, db_session):
    doc = await _doc(db_session)
    assert (await client.post(f"/api/reanalysis/proposals/{doc.id}/apply")).status_code == 404


async def test_apply_conflict_409(client, db_session, test_session_factory):
    """Bereichswechsel, aber die Datei fehlt im Archiv -> 409, nichts geaendert."""
    privat, _ = await _scopes(db_session)
    doc = await _doc(db_session, filing_scope_id=privat.id)
    await _propose(db_session, doc)
    resp = await client.get(f"/api/reanalysis/proposals/{doc.id}")
    assert "filing_scope_id" in [c["field"] for c in resp.json()["changes"]]

    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/apply", json={"fields": ["filing_scope_id"]})
    assert resp.status_code == 409
    assert "Datei" in resp.json()["detail"]
    assert await _proposal_count(test_session_factory) == 1


async def test_discard_204(client, db_session, test_session_factory):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0)
    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/discard")
    assert resp.status_code == 204
    assert await _proposal_count(test_session_factory) == 0
    async with test_session_factory() as session:
        assert (await session.get(Document, doc.id)).analysis_model == "gemma-test"
    assert (await client.post(f"/api/reanalysis/proposals/{doc.id}/discard")).status_code == 404


# --- GET /reanalysis/proposals ---------------------------------------------


async def test_list_filter_by_field_ignores_text_only(client, db_session):
    a = await _doc(db_session)
    b = await _doc(db_session)
    c = await _doc(db_session)
    await _propose(db_session, a, amount=120.0, title="Anderer Titel")
    await _propose(db_session, b, sender="Neue Praxis")
    await _propose(db_session, c, title="Nur anderer Titel", summary="Nur andere Zusammenfassung")

    everything = (await client.get("/api/reanalysis/proposals")).json()
    # c enthaelt nur Text-Aenderungen -> nicht sichtbar, nicht gezaehlt
    assert {i["document_id"] for i in everything["items"]} == {a.id, b.id}
    assert everything["total"] == 2

    by_amount = (await client.get("/api/reanalysis/proposals?field=amount")).json()
    assert [i["document_id"] for i in by_amount["items"]] == [a.id]
    assert by_amount["total"] == 1
    assert by_amount["field_counts"] == everything["field_counts"]  # Zaehler ignorieren den Filter

    # Textfelder sind kein Filterkriterium
    by_title = (await client.get("/api/reanalysis/proposals?field=title")).json()
    assert by_title["items"] == [] and by_title["total"] == 0

    assert (await client.get("/api/reanalysis/proposals?field=gibtsnicht")).status_code == 422


async def test_field_counts(client, db_session):
    a = await _doc(db_session)
    b = await _doc(db_session)
    await _propose(db_session, a, amount=120.0, sender="Neue Praxis", title="Neuer Titel")
    await _propose(db_session, b, amount=130.0)
    counts = (await client.get("/api/reanalysis/proposals")).json()["field_counts"]
    assert counts == {"amount": 2, "issuer": 1}


async def test_list_paginates(client, db_session):
    for _ in range(3):
        await _propose(db_session, await _doc(db_session), amount=120.0)
    first = (await client.get("/api/reanalysis/proposals?page=1&page_size=2")).json()
    second = (await client.get("/api/reanalysis/proposals?page=2&page_size=2")).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {i["document_id"] for i in first["items"]}.isdisjoint(i["document_id"] for i in second["items"])
    assert (await client.get("/api/reanalysis/proposals?page_size=0")).status_code == 422


# --- Sammel-Aktionen ---------------------------------------------------------


async def test_apply_all_task(client, db_session, test_session_factory):
    a = await _doc(db_session)
    b = await _doc(db_session)
    await _propose(db_session, a, amount=120.0)
    await _propose(db_session, b, sender="Neue Praxis")
    resp = await client.post("/api/reanalysis/proposals/apply-all", json={})
    assert resp.status_code == 202
    task = resp.json()
    assert task["kind"] == "reanalysis_apply_all" and task["key"] == "global"
    await wait_for_task(task["id"], timeout=10)
    async with test_session_factory() as session:
        stored = await session.get(BackgroundTask, task["id"])
        assert stored.result == {"applied": 2, "conflicts": 0, "errors": 0}
        assert (await session.get(Document, a.id)).amount == Decimal("120")
        assert (await session.get(Document, b.id)).issuer == "Neue Praxis"
    assert await _proposal_count(test_session_factory) == 0


async def test_apply_all_with_field_filter_counts_conflicts(client, db_session, test_session_factory):
    privat, praxis = await _scopes(db_session)
    conflicting = await _doc(db_session, filing_scope_id=privat.id)  # Datei fehlt -> Konflikt
    plain = await _doc(db_session, filing_scope_id=praxis.id)
    await _propose(db_session, conflicting)
    await _propose(db_session, plain, amount=120.0)

    resp = await client.post("/api/reanalysis/proposals/apply-all", json={"field": "filing_scope_id"})
    assert resp.status_code == 202
    await wait_for_task(resp.json()["id"], timeout=10)
    async with test_session_factory() as session:
        stored = await session.get(BackgroundTask, resp.json()["id"])
    assert stored.result == {"applied": 0, "conflicts": 1, "errors": 0}
    assert await _proposal_count(test_session_factory) == 2  # plain hat keine Bereichs-Aenderung


async def test_apply_all_field_filter_applies_all_changes_of_selected(client, db_session, test_session_factory):
    doc = await _doc(db_session)
    other = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0, sender="Neue Praxis")
    await _propose(db_session, other, sender="Andere Praxis")
    resp = await client.post("/api/reanalysis/proposals/apply-all", json={"field": "amount"})
    await wait_for_task(resp.json()["id"], timeout=10)
    async with test_session_factory() as session:
        stored = await session.get(BackgroundTask, resp.json()["id"])
        assert stored.result == {"applied": 1, "conflicts": 0, "errors": 0}
        changed = await session.get(Document, doc.id)
        assert changed.amount == Decimal("120") and changed.issuer == "Neue Praxis"
        assert (await session.get(Document, other.id)).issuer == "Praxis Dr. Muster"
    assert await _proposal_count(test_session_factory) == 1


async def test_apply_all_unexpected_error_is_counted_and_continues(client, db_session, test_session_factory, monkeypatch):
    from app.api import reanalysis as api
    from app.services.reanalysis_service import apply_proposal as real_apply

    a = await _doc(db_session)
    b = await _doc(db_session)
    await _propose(db_session, a, amount=120.0)
    await _propose(db_session, b, amount=130.0)

    async def flaky(session, document_id, *args, **kwargs):
        if document_id == a.id:
            raise RuntimeError("boom")
        return await real_apply(session, document_id, *args, **kwargs)

    monkeypatch.setattr(api, "apply_proposal", flaky)
    resp = await client.post("/api/reanalysis/proposals/apply-all", json={})
    await wait_for_task(resp.json()["id"], timeout=10)
    async with test_session_factory() as session:
        stored = await session.get(BackgroundTask, resp.json()["id"])
        assert stored.status == TaskStatus.SUCCEEDED
        assert stored.result == {"applied": 1, "conflicts": 0, "errors": 1}
        assert (await session.get(Document, b.id)).amount == Decimal("130")


async def test_apply_empty_fields_422(client, db_session, test_session_factory):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0)
    resp = await client.post(f"/api/reanalysis/proposals/{doc.id}/apply", json={"fields": []})
    assert resp.status_code == 422
    assert await _proposal_count(test_session_factory) == 1


async def test_apply_all_second_start_conflicts(client, db_session):
    db_session.add(BackgroundTask(kind="reanalysis_apply_all", key="global", status=TaskStatus.RUNNING))
    await db_session.commit()
    resp = await client.post("/api/reanalysis/proposals/apply-all", json={})
    assert resp.status_code == 409


async def test_discard_all_with_filter(client, db_session, test_session_factory):
    a = await _doc(db_session)
    b = await _doc(db_session)
    await _propose(db_session, a, amount=120.0)
    await _propose(db_session, b, sender="Neue Praxis")

    resp = await client.post("/api/reanalysis/proposals/discard-all", json={"field": "amount"})
    assert resp.status_code == 200
    assert resp.json() == {"discarded": 1}
    assert await _proposal_count(test_session_factory) == 1

    resp = await client.post("/api/reanalysis/proposals/discard-all", json={})
    assert resp.json() == {"discarded": 1}
    assert await _proposal_count(test_session_factory) == 0


# --- DocumentResponse / Loeschen ------------------------------------------------


async def test_document_response_has_model_and_proposal_flag(client, db_session):
    doc = await _doc(db_session, analysis_model="altes-modell")
    body = (await client.get(f"/api/documents/{doc.id}")).json()
    assert body["analysis_model"] == "altes-modell"
    assert body["has_proposal"] is False

    await _propose(db_session, doc, amount=120.0)
    assert (await client.get(f"/api/documents/{doc.id}")).json()["has_proposal"] is True

    # Nur Text-Aenderungen zaehlen nicht als Vorschlag
    other = await _doc(db_session)
    await _propose(db_session, other, title="Nur anderer Titel")
    assert (await client.get(f"/api/documents/{other.id}")).json()["has_proposal"] is False


async def test_deleted_document_proposal_hidden(client, db_session, test_session_factory):
    doc = await _doc(db_session)
    await _propose(db_session, doc, amount=120.0)
    assert (await client.delete(f"/api/documents/{doc.id}")).status_code == 204

    assert await _proposal_count(test_session_factory) == 0
    listing = (await client.get("/api/reanalysis/proposals")).json()
    assert listing["items"] == [] and listing["total"] == 0 and listing["field_counts"] == {}
    assert (await client.get(f"/api/reanalysis/proposals/{doc.id}")).status_code == 404
    async with test_session_factory() as session:
        assert (await session.get(Document, doc.id)).status == DocumentStatus.DELETED
