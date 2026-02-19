"""Tests fuer Filing Scopes API."""

import pytest


@pytest.mark.asyncio
class TestListFilingScopes:
    async def test_list_empty(self, client):
        resp = await client.get("/api/filing-scopes")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_after_create(self, client):
        await client.post("/api/filing-scopes", json={
            "name": "Privat",
            "description": "Private Dokumente",
            "keywords": ["privat"],
            "is_default": True,
        })
        resp = await client.get("/api/filing-scopes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Privat"
        assert data[0]["slug"] == "privat"
        assert data[0]["is_default"] is True


@pytest.mark.asyncio
class TestCreateFilingScope:
    async def test_create_basic(self, client):
        resp = await client.post("/api/filing-scopes", json={
            "name": "Praxis Dr. Klotz",
            "description": "Dokumente der Arztpraxis",
            "keywords": ["KBV", "Praxis"],
            "is_default": False,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Praxis Dr. Klotz"
        assert data["slug"] == "praxis-dr-klotz"
        assert data["description"] == "Dokumente der Arztpraxis"
        assert data["keywords"] == ["KBV", "Praxis"]
        assert data["is_default"] is False
        assert data["id"]

    async def test_create_with_color(self, client):
        resp = await client.post("/api/filing-scopes", json={
            "name": "Geschaeftlich",
            "keywords": [],
            "is_default": False,
            "color": "#FF5733",
        })
        assert resp.status_code == 201
        assert resp.json()["color"] == "#FF5733"

    async def test_create_sets_default(self, client):
        # Create first scope as default
        resp1 = await client.post("/api/filing-scopes", json={
            "name": "Privat",
            "keywords": [],
            "is_default": True,
        })
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        # Create second scope as default - should reset first
        resp2 = await client.post("/api/filing-scopes", json={
            "name": "Geschaeftlich",
            "keywords": [],
            "is_default": True,
        })
        assert resp2.status_code == 201
        assert resp2.json()["is_default"] is True

        # Check first scope is no longer default
        scopes = (await client.get("/api/filing-scopes")).json()
        privat = next(s for s in scopes if s["id"] == id1)
        assert privat["is_default"] is False

    async def test_create_duplicate_name(self, client):
        await client.post("/api/filing-scopes", json={
            "name": "Privat",
            "keywords": [],
            "is_default": False,
        })
        resp = await client.post("/api/filing-scopes", json={
            "name": "Privat",
            "keywords": [],
            "is_default": False,
        })
        assert resp.status_code == 409

    async def test_slug_generation(self, client):
        resp = await client.post("/api/filing-scopes", json={
            "name": "Mein Bereich",
            "keywords": [],
            "is_default": False,
        })
        assert resp.status_code == 201
        assert resp.json()["slug"] == "mein-bereich"


@pytest.mark.asyncio
class TestUpdateFilingScope:
    async def test_update_name(self, client):
        create_resp = await client.post("/api/filing-scopes", json={
            "name": "Alt",
            "keywords": [],
            "is_default": False,
        })
        scope_id = create_resp.json()["id"]

        resp = await client.patch(f"/api/filing-scopes/{scope_id}", json={
            "name": "Neu",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "Neu"
        assert resp.json()["slug"] == "neu"

    async def test_update_keywords(self, client):
        create_resp = await client.post("/api/filing-scopes", json={
            "name": "Test",
            "keywords": ["a"],
            "is_default": False,
        })
        scope_id = create_resp.json()["id"]

        resp = await client.patch(f"/api/filing-scopes/{scope_id}", json={
            "keywords": ["x", "y", "z"],
        })
        assert resp.status_code == 200
        assert resp.json()["keywords"] == ["x", "y", "z"]

    async def test_update_set_default(self, client):
        resp1 = await client.post("/api/filing-scopes", json={
            "name": "Scope A",
            "keywords": [],
            "is_default": True,
        })
        id_a = resp1.json()["id"]

        resp2 = await client.post("/api/filing-scopes", json={
            "name": "Scope B",
            "keywords": [],
            "is_default": False,
        })
        id_b = resp2.json()["id"]

        # Set B as default
        await client.patch(f"/api/filing-scopes/{id_b}", json={"is_default": True})

        scopes = (await client.get("/api/filing-scopes")).json()
        a = next(s for s in scopes if s["id"] == id_a)
        b = next(s for s in scopes if s["id"] == id_b)
        assert a["is_default"] is False
        assert b["is_default"] is True

    async def test_update_not_found(self, client):
        resp = await client.patch("/api/filing-scopes/nonexistent", json={"name": "X"})
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeleteFilingScope:
    async def test_delete_scope(self, client):
        # Create default scope first
        await client.post("/api/filing-scopes", json={
            "name": "Standard",
            "keywords": [],
            "is_default": True,
        })
        # Create deletable scope
        create_resp = await client.post("/api/filing-scopes", json={
            "name": "Loeschbar",
            "keywords": [],
            "is_default": False,
        })
        scope_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/filing-scopes/{scope_id}")
        assert resp.status_code == 200

        scopes = (await client.get("/api/filing-scopes")).json()
        assert not any(s["id"] == scope_id for s in scopes)

    async def test_delete_not_found(self, client):
        resp = await client.delete("/api/filing-scopes/nonexistent")
        assert resp.status_code == 404

    async def test_delete_default_denied(self, client):
        create_resp = await client.post("/api/filing-scopes", json={
            "name": "Standard",
            "keywords": [],
            "is_default": True,
        })
        scope_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/filing-scopes/{scope_id}")
        assert resp.status_code == 400
        assert "Standard" in resp.json()["detail"]

    async def test_delete_last_scope_denied(self, client):
        create_resp = await client.post("/api/filing-scopes", json={
            "name": "Einziger",
            "keywords": [],
            "is_default": False,
        })
        scope_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/filing-scopes/{scope_id}")
        assert resp.status_code == 400
