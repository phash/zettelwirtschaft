"""Tests fuer Steuerpaket-Export API."""

import pytest
from datetime import date


@pytest.mark.asyncio
class TestTaxYears:
    async def test_tax_years_empty(self, client):
        resp = await client.get("/api/tax/years")
        assert resp.status_code == 200
        assert resp.json()["years"] == []


@pytest.mark.asyncio
class TestTaxSummary:
    async def test_summary_no_documents(self, client):
        resp = await client.get("/api/tax/summary/2025")
        assert resp.status_code == 200
        data = resp.json()
        assert data["year"] == 2025
        assert data["total_documents"] == 0
        assert data["categories"] == []

    async def test_summary_invalid_year(self, client):
        resp = await client.get("/api/tax/summary/1899")
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestTaxValidation:
    async def test_validate_empty(self, client):
        resp = await client.get("/api/tax/validate/2025")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["total_documents"] == 0


@pytest.mark.asyncio
class TestTaxExport:
    async def test_export_no_documents(self, client):
        resp = await client.post("/api/tax/export", json={"year": 2025})
        assert resp.status_code == 400

    async def test_export_invalid_year(self, client):
        resp = await client.post("/api/tax/export", json={"year": 1899})
        assert resp.status_code == 400
