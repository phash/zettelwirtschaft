"""Tests fuer Benachrichtigungs-API."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType


async def _create_notifications(db: AsyncSession) -> list[Notification]:
    """Erstellt Testbenachrichtigungen: 2 ungelesen, 1 gelesen."""
    n1 = Notification(
        type=NotificationType.WARRANTY_EXPIRING,
        title="Garantie laeuft ab",
        message="Die Garantie fuer Laptop laeuft in 30 Tagen ab.",
        is_read=False,
    )
    n2 = Notification(
        type=NotificationType.PROCESSING_DONE,
        title="Verarbeitung abgeschlossen",
        message="Dokument Rechnung-2024.pdf wurde erfolgreich verarbeitet.",
        is_read=False,
    )
    n3 = Notification(
        type=NotificationType.SYSTEM,
        title="Backup erstellt",
        message="Automatisches Backup wurde erfolgreich erstellt.",
        is_read=True,
    )
    db.add_all([n1, n2, n3])
    await db.flush()
    return [n1, n2, n3]


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

    async def test_list_with_data(self, client, db_session: AsyncSession):
        await _create_notifications(db_session)
        await db_session.commit()

        resp = await client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    async def test_list_unread_only_with_data(self, client, db_session: AsyncSession):
        await _create_notifications(db_session)
        await db_session.commit()

        resp = await client.get("/api/notifications?unread_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Alle zurueckgegebenen muessen ungelesen sein
        assert all(not n["is_read"] for n in data)

    async def test_list_pagination(self, client, db_session: AsyncSession):
        await _create_notifications(db_session)
        await db_session.commit()

        # Erste Seite mit Limit 2
        resp = await client.get("/api/notifications?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        # Zweite Seite mit Offset 2
        resp = await client.get("/api/notifications?limit=2&offset=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


@pytest.mark.asyncio
class TestNotificationCount:
    async def test_count_zero(self, client):
        resp = await client.get("/api/notifications/count")
        assert resp.status_code == 200
        assert resp.json()["unread"] == 0

    async def test_count_with_data(self, client, db_session: AsyncSession):
        await _create_notifications(db_session)
        await db_session.commit()

        resp = await client.get("/api/notifications/count")
        assert resp.status_code == 200
        assert resp.json()["unread"] == 2


@pytest.mark.asyncio
class TestNotificationMarkRead:
    async def test_mark_read_not_found(self, client):
        resp = await client.post("/api/notifications/nonexistent/read")
        assert resp.status_code == 404

    async def test_mark_all_read(self, client):
        resp = await client.post("/api/notifications/read-all")
        assert resp.status_code == 200

    async def test_mark_read(self, client, db_session: AsyncSession):
        notifications = await _create_notifications(db_session)
        await db_session.commit()

        # Eine ungelesene Benachrichtigung als gelesen markieren
        unread_id = notifications[0].id
        resp = await client.post(f"/api/notifications/{unread_id}/read")
        assert resp.status_code == 200

        # Ungelesene Anzahl muss auf 1 sinken
        count_resp = await client.get("/api/notifications/count")
        assert count_resp.json()["unread"] == 1

    async def test_mark_all_read_with_data(self, client, db_session: AsyncSession):
        await _create_notifications(db_session)
        await db_session.commit()

        # Alle als gelesen markieren
        resp = await client.post("/api/notifications/read-all")
        assert resp.status_code == 200

        # Ungelesene Anzahl muss 0 sein
        count_resp = await client.get("/api/notifications/count")
        assert count_resp.json()["unread"] == 0
