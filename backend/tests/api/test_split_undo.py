"""API "Aufteilung rückgängig" (Spezifikation 12, Etappe 3): POST
/api/split/{id}/undo, can_undo am Stapel und can_undo_split am Teildokument."""

from unittest.mock import AsyncMock, patch

from app.models.processing_job import JobStatus
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_split_flow import (  # noqa: F401 (Fixtures)
    _batch,
    _confirm,
    _detect,
    _job,
    _pipeline_mocks,
    _run_until,
    fast_settings,
    ocr_mock,
)


async def _done(settings, factory, tmp_path):
    job_id, batch = await _detect(settings, factory, make_pdf(tmp_path / "s.pdf", 4))
    job_ids = await _confirm(factory, settings, batch, [[1, 2], [3, 4]])
    await _run_until(factory, settings, job_ids)
    parts = [(await _job(factory, i)).document_id for i in job_ids]
    return job_id, await _batch(factory, batch.id), parts


async def test_undo_endpoint_and_flags(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    job_id, batch, parts = await _done(fast_settings, test_session_factory, tmp_path)

    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert detail["status"] == "DONE" and detail["can_undo"] is True and detail["undo_blocked_reason"] is None
    doc = (await client.get(f"/api/documents/{parts[0]}")).json()
    assert doc["can_undo_split"] is True and doc["split_undo_blocked_reason"] is None
    assert doc["split_part_index"] == 1 and doc["split_part_count"] == 2

    with patch("app.services.vectorize_service.delete_document_vectors", new_callable=AsyncMock) as delete:
        response = await client.post(f"/api/split/{batch.id}/undo", json={"version": detail["version"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROPOSED" and sorted(body["removed_document_ids"]) == sorted(parts)
    assert body["restored_document_id"] is None
    assert {call.args[0] for call in delete.await_args_list} == set(parts)  # Vektoren der Teile
    assert (await client.get(f"/api/documents/{parts[0]}")).status_code == 404  # soft-deleted
    assert (await _job(test_session_factory, job_id)).status == JobStatus.AWAITING_SPLIT

    again = await client.post(f"/api/split/{batch.id}/undo", json={"version": body and detail["version"]})
    assert again.status_code == 409


async def test_undo_blocked_after_editing_a_part(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    _, batch, parts = await _done(fast_settings, test_session_factory, tmp_path)
    with patch("app.services.vectorize_service.revectorize_document_safe", new_callable=AsyncMock):
        edited = await client.patch(f"/api/documents/{parts[1]}", json={"title": "Eigener Titel"})
    assert edited.status_code == 200, edited.text

    doc = (await client.get(f"/api/documents/{parts[0]}")).json()
    assert doc["can_undo_split"] is False
    assert "bearbeitet" in doc["split_undo_blocked_reason"]
    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert detail["can_undo"] is False and "bearbeitet" in detail["undo_blocked_reason"]

    response = await client.post(f"/api/split/{batch.id}/undo", json={"version": detail["version"]})
    assert response.status_code == 409
    assert "bearbeitet" in response.json()["detail"]


async def test_undo_not_offered_while_proposed(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert detail["can_undo"] is False and detail["undo_blocked_reason"] is None
    response = await client.post(f"/api/split/{batch.id}/undo", json={"version": detail["version"]})
    assert response.status_code == 409
    assert (await client.post("/api/split/unbekannt/undo", json={"version": 0})).status_code == 404
