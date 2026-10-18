"""API /api/split + Erweiterungen von Upload, Jobs und Dokumenten (Spezifikation 12)."""

import json
from pathlib import Path

from sqlalchemy import update

from app.models.document import Document
from app.models.processing_job import JobStatus, ProcessingJob, SplitMode
from app.models.split_batch import SplitBatch, SplitBatchStatus
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_split_flow import (  # noqa: F401 (Fixtures)
    _batch,
    _detect,
    _job,
    _pipeline_mocks,
    _run_until,
    fast_settings,
    ocr_mock,
)


async def test_upload_split_check_sets_force_mode(client, test_session_factory, tmp_path):
    pdf = make_pdf(tmp_path / "stapel.pdf", 2)
    with pdf.open("rb") as handle:
        response = await client.post(
            "/api/documents/upload",
            files={"files": ("stapel.pdf", handle, "application/pdf")},
            data={"split_check": "true"},
        )
    assert response.status_code == 200, response.text
    job_id = response.json()["uploaded"][0]["job_id"]
    assert (await _job(test_session_factory, job_id)).split_mode == SplitMode.FORCE.value

    with pdf.open("rb") as handle:
        response = await client.post(
            "/api/documents/upload", files={"files": ("einzeln.pdf", handle, "application/pdf")},
        )
    job_id = response.json()["uploaded"][0]["job_id"]
    assert (await _job(test_session_factory, job_id)).split_mode == SplitMode.AUTO.value


async def test_batch_detail_pages_and_confirm(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 4, blank={3}))

    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert detail["status"] == "PROPOSED"
    assert detail["origin"] == "AUTO"
    assert detail["page_count"] == 4 and detail["pages_done"] == 4
    assert [p["blank"] for p in detail["pages"]] == [False, False, True, False]
    assert "text" not in json.dumps(detail["pages"])
    # FORCE: auch ohne Schnittvorschlag im Editor; jeder Uebergang bewertet
    assert [b["after_page"] for b in detail["boundaries"]] == [1, 2, 3]
    assert {b["decision"] for b in detail["boundaries"]} == {"none"}
    assert all(isinstance(b["score"], float) and b["reasons"] is not None for b in detail["boundaries"])
    assert detail["parts"] == []

    thumb = await client.get(f"/api/split/{batch.id}/pages/1/thumbnail")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/png"
    preview = await client.get(f"/api/split/{batch.id}/pages/4/preview")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert (await client.get(f"/api/split/{batch.id}/pages/9/thumbnail")).status_code == 404
    assert (await client.get("/api/split/..%5C..%5Cx/pages/1/thumbnail")).status_code == 404

    jobs = (await client.get("/api/jobs", params={"status": "AWAITING_SPLIT"})).json()["items"]
    assert [(j["id"], j["split_batch_id"], j["split_role"]) for j in jobs] == [(job_id, batch.id, "SOURCE")]

    invalid = await client.post(
        f"/api/split/{batch.id}/confirm",
        json={"version": batch.version, "parts": [{"pages": [1, 2]}, {"pages": [4]}]},
    )
    assert invalid.status_code == 422
    assert "Seite 3" in invalid.json()["detail"]

    body = {"version": batch.version, "parts": [{"pages": [1, 2]}, {"pages": [4]}], "excluded": [3]}
    confirmed = await client.post(f"/api/split/{batch.id}/confirm", json=body)
    assert confirmed.status_code == 202, confirmed.text
    assert len(confirmed.json()["job_ids"]) == 2
    again = await client.post(f"/api/split/{batch.id}/confirm", json=body)
    assert again.status_code == 409
    assert again.json()["detail"] == "Der Stapel wurde inzwischen geändert. Bitte neu laden."

    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert detail["status"] == "SPLITTING" and detail["excluded"] == [3]
    assert [(p["index"], p["pages"], p["job_status"]) for p in detail["parts"]] == [
        (1, [1, 2], "PENDING"), (2, [4], "PENDING"),
    ]
    part_jobs = (await client.get("/api/jobs", params={"status": "PENDING"})).json()["items"]
    assert sorted((j["page_from"], j["page_to"]) for j in part_jobs) == [(1, 2), (4, 4)]

    retry = await client.post(f"/api/jobs/{job_id}/retry")
    assert retry.status_code == 409  # SPLIT


async def test_document_detail_shows_split_provenance(
    client, fast_settings, test_session_factory, tmp_path, ocr_mock,
):
    _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "scan_0427.pdf", 3))
    response = await client.post(
        f"/api/split/{batch.id}/confirm",
        json={"version": batch.version, "parts": [{"pages": [1]}, {"pages": [2, 3]}]},
    )
    job_ids = response.json()["job_ids"]
    await _run_until(test_session_factory, fast_settings, job_ids)
    doc_id = (await _job(test_session_factory, job_ids[1])).document_id

    doc = (await client.get(f"/api/documents/{doc_id}")).json()
    assert doc["split_batch_id"] == batch.id
    assert doc["split_part_index"] == 2 and doc["split_part_count"] == 2
    assert doc["source_pages"] == "2-3"
    assert doc["split_source_filename"] == "scan_0427.pdf"
    assert doc["can_undo_split"] is True  # frisch archiviert, unbearbeitet
    # PATCH-Antwort traegt die Herkunft ebenfalls (Frontend ersetzt das Dokument)
    patched = (await client.patch(f"/api/documents/{doc_id}", json={"title": "Neu"})).json()
    assert patched["split_part_count"] == 2
    assert patched["can_undo_split"] is False  # die Bearbeitung sperrt "rückgängig"


async def test_keep_whole_and_discard_endpoints(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    job_a, batch_a = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "a.pdf", 2))
    job_b, batch_b = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "b.pdf", 3))

    assert (await client.post(f"/api/jobs/{job_a}/retry")).status_code == 409  # AWAITING_SPLIT
    stale = await client.post(f"/api/split/{batch_a.id}/keep-whole", json={"version": batch_a.version + 1})
    assert stale.status_code == 409
    kept = await client.post(f"/api/split/{batch_a.id}/keep-whole", json={"version": batch_a.version})
    assert kept.status_code == 200 and kept.json()["status"] == "KEPT_WHOLE"
    assert (await _job(test_session_factory, job_a)).status == JobStatus.PENDING

    discarded = await client.post(f"/api/split/{batch_b.id}/discard", json={"version": batch_b.version})
    assert discarded.json()["status"] == "DISCARDED"
    assert await _job(test_session_factory, job_b) is None


async def test_delete_awaiting_job_discards_batch(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "a.pdf", 2))
    assert (await client.delete(f"/api/jobs/{job_id}")).status_code == 204
    assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DISCARDED
    assert not Path(batch.file_path).exists()


async def test_source_missing_returns_code(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "a.pdf", 2))
    Path(batch.file_path).unlink()
    response = await client.post(
        f"/api/split/{batch.id}/confirm", json={"version": batch.version, "parts": [{"pages": [1, 2]}]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SPLIT_SOURCE_MISSING"
    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert detail["status"] == "PROPOSED" and detail["error_code"] == "SPLIT_SOURCE_MISSING"


async def test_split_progress_in_job_list(client, fast_settings, test_session_factory, tmp_path, ocr_mock):
    job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "a.pdf", 2))
    async with test_session_factory() as session:
        await session.execute(update(SplitBatch).where(SplitBatch.id == batch.id).values(
            status=SplitBatchStatus.DETECTING, pages_done=1,
        ))
        await session.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(
            status=JobStatus.PROCESSING,
        ))
        await session.commit()
    jobs = (await client.get("/api/jobs", params={"status": "PROCESSING"})).json()["items"]
    assert jobs[0]["split_progress"] == {"pages_done": 1, "page_count": 2}
    status = (await client.get(f"/api/documents/{job_id}/status")).json()
    assert status["split_progress"] == {"pages_done": 1, "page_count": 2}

    listed = (await client.get("/api/split", params={"status": "DETECTING"})).json()
    assert [b["id"] for b in listed["items"]] == [batch.id] and listed["total"] == 1
    assert (await client.get("/api/split", params={"status": "KAPUTT"})).status_code == 422


async def test_request_document_split_endpoint(client, fast_settings, test_session_factory, tmp_path):
    archive = Path(fast_settings.ARCHIVE_DIR) / "2026"
    archive.mkdir(parents=True)
    from app.services.archive_service import _compute_file_hash
    from tests.factories import make_document

    multi = make_pdf(archive / "multi.pdf", 3)
    single = make_pdf(archive / "single.pdf", 1)
    async with test_session_factory() as session:
        doc = await make_document(session, file_path=str(multi), file_hash=_compute_file_hash(multi))
        one = await make_document(session, file_path=str(single), file_hash=_compute_file_hash(single))
        await session.commit()

    response = await client.post(f"/api/documents/{doc.id}/split")
    assert response.status_code == 202, response.text
    body = response.json()
    job = await _job(test_session_factory, body["job_id"])
    assert job.split_mode == "FORCE" and job.source == "SPLIT_REQUEST" and job.split_batch_id == body["batch_id"]
    detail = (await client.get(f"/api/split/{body['batch_id']}")).json()
    assert detail["origin"] == "MANUAL" and detail["status"] == "DETECTING"
    assert detail["source_document_id"] == doc.id

    assert (await client.post(f"/api/documents/{doc.id}/split")).status_code == 409
    assert (await client.post(f"/api/documents/{one.id}/split")).status_code == 422
    assert (await client.post("/api/documents/00000000-0000-4000-8000-000000000000/split")).status_code == 404
    async with test_session_factory() as session:
        await session.execute(update(Document).where(Document.id == one.id).values(status="DELETED"))
        await session.commit()
    assert (await client.post(f"/api/documents/{one.id}/split")).status_code == 422
