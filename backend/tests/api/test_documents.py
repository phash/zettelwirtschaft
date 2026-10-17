from pathlib import Path

from httpx import AsyncClient


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


    async def test_upload_oversized_rejected_and_not_stored(
        self, client: AsyncClient, test_settings, sample_pdf: Path,
    ):
        test_settings.MAX_UPLOAD_SIZE_MB = 1
        big = sample_pdf.read_bytes() + b"0" * (1024 * 1024 + 10)
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("gross.pdf", big, "application/pdf"))],
        )
        data = resp.json()
        assert data["uploaded"] == []
        assert "zu groß" in data["rejected"][0]["error"]
        assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []

    async def test_upload_magic_mismatch_removes_file(self, client: AsyncClient, test_settings):
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("fake.pdf", b"MZ\x90\x00not a pdf", "application/pdf"))],
        )
        assert "überein" in resp.json()["rejected"][0]["error"]
        assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []

    async def test_upload_tif_alias_accepted(self, client: AsyncClient, tmp_path: Path):
        from PIL import Image

        src = tmp_path / "scan.tif"
        Image.new("L", (20, 20), 255).save(src, "TIFF")
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("scan.tif", src.read_bytes(), "image/tiff"))],
        )
        data = resp.json()
        assert data["rejected"] == []
        status = await client.get(f"/api/documents/{data['uploaded'][0]['job_id']}/status")
        assert status.json()["file_type"] == "tiff"


# Dashboard-Stats: siehe test_document_crud.TestDashboardStats


class TestDocumentStatusEndpoint:
    async def test_get_status(self, client: AsyncClient, sample_pdf: Path):
        # Erst hochladen
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", ("test.pdf", sample_pdf.read_bytes(), "application/pdf"))],
        )
        uploaded = resp.json()["uploaded"][0]
        # Upload liefert die Job-ID (das Dokument entsteht erst bei der Archivierung)
        assert "document_id" not in uploaded
        job_id = uploaded["job_id"]

        # Status abrufen
        resp = await client.get(f"/api/documents/{job_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == job_id
        # Im Test-Client laeuft kein Queue-Worker -> Job bleibt PENDING, noch kein Dokument
        assert data["status"] == "PENDING"
        assert data["document_id"] is None

    async def test_status_not_found(self, client: AsyncClient):
        resp = await client.get("/api/documents/nonexistent-id/status")
        assert resp.status_code == 404
