import io
import struct
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.processing_job import JobStatus, ProcessingJob


class TestUploadEndpoint:
    async def test_upload_single_pdf(self, client: AsyncClient, sample_pdf: Path):
        pdf_bytes = sample_pdf.read_bytes()
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("rechnung.pdf", pdf_bytes, "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["uploaded"]) == 1
        assert len(data["rejected"]) == 0
        assert data["uploaded"][0]["status"] == "PENDING"
        assert data["uploaded"][0]["original_filename"] == "rechnung.pdf"

    async def test_upload_multiple_files(
        self,
        client: AsyncClient,
        sample_pdf: Path,
        sample_jpg: Path,
    ):
        resp = await client.post(
            "/api/documents/upload",
            files=[
                ("files", ("doc1.pdf", sample_pdf.read_bytes(), "application/pdf")),
                ("files", ("foto.jpg", sample_jpg.read_bytes(), "image/jpeg")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["uploaded"]) == 2

    async def test_upload_invalid_extension(self, client: AsyncClient):
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("script.exe", b"MZ\x90\x00bad", "application/octet-stream"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["uploaded"]) == 0
        assert len(data["rejected"]) == 1
        assert "nicht erlaubt" in data["rejected"][0]["error"]

    async def test_upload_partial_success(
        self,
        client: AsyncClient,
        sample_pdf: Path,
    ):
        """Ein gueltiges und ein ungueltiges File: partial success."""
        resp = await client.post(
            "/api/documents/upload",
            files=[
                ("files", ("good.pdf", sample_pdf.read_bytes(), "application/pdf")),
                ("files", ("bad.exe", b"MZ\x90\x00", "application/octet-stream")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["uploaded"]) == 1
        assert len(data["rejected"]) == 1


class TestDocumentStatusEndpoint:
    async def test_get_status(self, client: AsyncClient, sample_pdf: Path):
        # Erst hochladen
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("test.pdf", sample_pdf.read_bytes(), "application/pdf"))],
        )
        doc_id = resp.json()["uploaded"][0]["document_id"]

        # Status abrufen
        resp = await client.get(f"/api/documents/{doc_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc_id
        assert data["status"] in ["PENDING", "PROCESSING", "COMPLETED"]

    async def test_status_not_found(self, client: AsyncClient):
        resp = await client.get("/api/documents/nonexistent-id/status")
        assert resp.status_code == 404
