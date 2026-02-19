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
