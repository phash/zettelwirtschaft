from pathlib import Path

from httpx import AsyncClient


class TestJobsEndpoint:
    async def test_list_jobs_empty(self, client: AsyncClient):
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    async def test_list_jobs_after_upload(self, client: AsyncClient, sample_pdf: Path):
        # Datei hochladen
        await client.post(
            "/api/documents/upload",
            files=[("files", ("test.pdf", sample_pdf.read_bytes(), "application/pdf"))],
        )

        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["original_filename"] == "test.pdf"

    async def test_list_jobs_filter_by_status(
        self, client: AsyncClient, sample_pdf: Path
    ):
        await client.post(
            "/api/documents/upload",
            files=[("files", ("test.pdf", sample_pdf.read_bytes(), "application/pdf"))],
        )

        # Filter auf PENDING
        resp = await client.get("/api/jobs?status=PENDING")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

        # Filter auf COMPLETED (sollte leer sein, da kein Worker laeuft)
        resp = await client.get("/api/jobs?status=COMPLETED")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_jobs_pagination(self, client: AsyncClient, sample_pdf: Path):
        # Mehrere Dateien hochladen
        pdf_bytes = sample_pdf.read_bytes()
        for i in range(3):
            await client.post(
                "/api/documents/upload",
                files=[("files", (f"doc{i}.pdf", pdf_bytes, "application/pdf"))],
            )

        # Erste Seite mit page_size=2
        resp = await client.get("/api/jobs?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3

        # Zweite Seite
        resp = await client.get("/api/jobs?page=2&page_size=2")
        data = resp.json()
        assert len(data["items"]) == 1


import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.models.processing_job import (  # noqa: E402
    JOB_ERROR_DUPLICATE,
    JobSource,
    JobStatus,
    ProcessingJob,
)
from app.services import queue_worker_service  # noqa: E402
from tests.factories import make_document  # noqa: E402


async def _job(db: AsyncSession, file_path: Path | str = "/x/a.pdf", **overrides) -> ProcessingJob:
    fields = dict(
        original_filename="a.pdf", stored_filename="x_a.pdf", file_path=str(file_path),
        file_type="pdf", file_size_bytes=1, source=JobSource.UPLOAD, status=JobStatus.PENDING,
    )
    fields.update(overrides)
    job = ProcessingJob(**fields)
    db.add(job)
    await db.commit()
    return job


class TestJobListValidation:
    async def test_unknown_status_is_422(self, client: AsyncClient):
        assert (await client.get("/api/jobs?status=BOGUS")).status_code == 422
        assert (await client.get("/api/jobs?status=PENDING,BOGUS")).status_code == 422

    async def test_lowercase_status_accepted(self, client: AsyncClient, db_session: AsyncSession):
        await _job(db_session)
        resp = await client.get("/api/jobs?status=pending")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_list_is_slim_and_links_document(self, client: AsyncClient, db_session: AsyncSession):
        doc = await make_document(db_session, commit=True)
        await _job(db_session, status=JobStatus.COMPLETED, document_id=doc.id, ocr_text="Volltext")

        item = (await client.get("/api/jobs")).json()["items"][0]

        assert "ocr_text" not in item and "analysis_result" not in item
        assert item["document_id"] == doc.id
        # Der Link aus der Upload-Ansicht fuehrt zum echten Dokument
        assert (await client.get(f"/api/documents/{item['document_id']}")).status_code == 200


class TestRetry:
    async def test_retry_unknown_job_is_404(self, client: AsyncClient):
        assert (await client.post("/api/jobs/gibt-es-nicht/retry")).status_code == 404

    async def test_retry_requires_failed(self, client: AsyncClient, db_session: AsyncSession):
        job = await _job(db_session, status=JobStatus.COMPLETED)
        resp = await client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 400
        assert "können" in resp.json()["detail"]

    async def test_retry_duplicate_is_400(self, client: AsyncClient, db_session: AsyncSession, sample_pdf: Path):
        job = await _job(db_session, sample_pdf, status=JobStatus.FAILED, error_code=JOB_ERROR_DUPLICATE)
        resp = await client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 400
        assert "bereits archiviert" in resp.json()["detail"]

    async def test_retry_resets_fields(self, client: AsyncClient, db_session: AsyncSession, sample_pdf: Path):
        job = await _job(
            db_session, sample_pdf, status=JobStatus.FAILED, error_message="kaputt",
            retry_count=3,
        )
        resp = await client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 200

        status = (await client.get(f"/api/documents/{job.id}/status")).json()
        assert status["status"] == "PENDING"
        assert status["retry_count"] == 0
        assert status["error_message"] is None
        assert status["error_code"] is None


class TestDismissJob:
    async def test_dismiss_failed_job_removes_upload_file(
        self, client: AsyncClient, db_session: AsyncSession, test_settings,
    ):
        upload = Path(test_settings.UPLOAD_DIR) / "x_dup.pdf"
        upload.write_bytes(b"%PDF")
        job = await _job(db_session, upload, status=JobStatus.FAILED, error_code=JOB_ERROR_DUPLICATE)

        assert (await client.delete(f"/api/jobs/{job.id}")).status_code == 204
        assert not upload.exists()
        assert (await client.get(f"/api/documents/{job.id}/status")).status_code == 404

    async def test_dismiss_never_deletes_outside_upload_dir(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path: Path,
    ):
        outside = tmp_path / "fremd.pdf"
        outside.write_bytes(b"%PDF")
        job = await _job(db_session, outside, status=JobStatus.FAILED)

        assert (await client.delete(f"/api/jobs/{job.id}")).status_code == 204
        assert outside.exists()

    async def test_dismiss_requires_failed(self, client: AsyncClient, db_session: AsyncSession):
        job = await _job(db_session, status=JobStatus.PROCESSING)
        assert (await client.delete(f"/api/jobs/{job.id}")).status_code == 400


class TestQueueControl:
    @pytest.fixture(autouse=True)
    def _reset_pause(self):
        queue_worker_service.resume_queue()
        yield
        queue_worker_service.resume_queue()

    async def test_pause_resume_and_status(self, client: AsyncClient):
        assert (await client.get("/api/jobs/queue-status")).json() == {"paused": False}
        assert (await client.post("/api/jobs/pause")).json()["paused"] is True
        assert (await client.get("/api/jobs/queue-status")).json() == {"paused": True}
        assert (await client.post("/api/jobs/resume")).json()["paused"] is False
        assert queue_worker_service.is_queue_paused() is False
