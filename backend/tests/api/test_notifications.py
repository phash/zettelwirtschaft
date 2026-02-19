"""Tests fuer Benachrichtigungs-API."""

import pytest


@pytest.mark.asyncio
class TestNotificationList:
    async def test_list_empty(self, client):
        resp = await client.get("/api/notifications")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_unread_only(self, client):
        resp = await client.get("/api/notifications?unread_only=true")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
class TestNotificationCount:
    async def test_count_zero(self, client):
        resp = await client.get("/api/notifications/count")
        assert resp.status_code == 200
        assert resp.json()["unread"] == 0


@pytest.mark.asyncio
class TestNotificationMarkRead:
    async def test_mark_read_not_found(self, client):
        resp = await client.post("/api/notifications/nonexistent/read")
        assert resp.status_code == 404

    async def test_mark_all_read(self, client):
        resp = await client.post("/api/notifications/read-all")
        assert resp.status_code == 200
