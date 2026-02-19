"""Tests fuer erweitertes Rueckfrage-System API."""

import pytest


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
