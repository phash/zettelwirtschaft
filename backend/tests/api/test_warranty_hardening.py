"""Garantien: PATCH-Validierung, Status-Grenzen, geloeschte Dokumente (Review 1.6.0)."""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import DocumentStatus
from app.models.warranty_info import WarrantyInfo, WarrantyType
from tests.factories import make_document


async def _warranty(db, end: date, *, status=DocumentStatus.ACTIVE, purchase: date | None = None) -> WarrantyInfo:
    doc = await make_document(db, status=status)
    w = WarrantyInfo(
        document_id=doc.id,
        product_name="Laptop",
        purchase_date=purchase or date(2024, 1, 31),
        warranty_end_date=end,
        warranty_type=WarrantyType.LEGAL,
        warranty_duration_months=24,
        reminder_sent=True,
        reminder_90d_sent=True,
        reminder_30d_sent=True,
    )
    db.add(w)
    await db.flush()
    return w


@pytest.mark.asyncio
class TestWarrantyPatchValidation:
    async def test_invalid_type_rejected_and_list_keeps_working(self, client, db_session):
        """Regression: 'FOO' landete in der DB und brach Liste + Detail dauerhaft (500)."""
        w = await _warranty(db_session, date.today() + timedelta(days=100))
        w_id = w.id
        await db_session.commit()

        resp = await client.patch(f"/api/warranties/{w_id}", json={"warranty_type": "FOO"})
        assert resp.status_code == 422
        assert (await client.get("/api/warranties")).status_code == 200

        resp = await client.patch(f"/api/warranties/{w_id}", json={"warranty_type": "EXTENDED"})
        assert resp.status_code == 200
        assert resp.json()["warranty_type"] == "EXTENDED"

    @pytest.mark.parametrize("body", [
        {"warranty_end_date": None},
        {"warranty_duration_months": None},
        {"warranty_type": None},
        {"warranty_duration_months": -1},
        {"warranty_duration_months": 0},
        {"warranty_duration_months": 601},
    ])
    async def test_null_and_out_of_range_rejected(self, client, db_session, body):
        w = await _warranty(db_session, date.today() + timedelta(days=100))
        await db_session.commit()

        resp = await client.patch(f"/api/warranties/{w.id}", json=body)
        assert resp.status_code == 422

    async def test_nullable_fields_may_be_cleared(self, client, db_session):
        w = await _warranty(db_session, date.today() + timedelta(days=100))
        await db_session.commit()
        resp = await client.patch(f"/api/warranties/{w.id}", json={"notes": None, "product_category": None})
        assert resp.status_code == 200

    async def test_duration_change_recomputes_end_date(self, client, db_session):
        w = await _warranty(db_session, date(2026, 1, 31), purchase=date(2024, 1, 31))
        await db_session.commit()

        resp = await client.patch(f"/api/warranties/{w.id}", json={"warranty_duration_months": 25})
        assert resp.status_code == 200
        assert resp.json()["warranty_end_date"] == "2026-02-28"

    async def test_end_date_change_rearms_reminders_and_is_audited(self, client, db_session):
        w = await _warranty(db_session, date.today() + timedelta(days=10))
        w_id, doc_id = w.id, w.document_id
        await db_session.commit()

        new_end = date.today() + timedelta(days=200)
        resp = await client.patch(f"/api/warranties/{w_id}", json={"warranty_end_date": new_end.isoformat()})
        assert resp.status_code == 200
        assert resp.json()["reminder_sent"] is False

        db_session.expire_all()
        reloaded = await db_session.get(WarrantyInfo, w_id)
        assert not (reloaded.reminder_90d_sent or reloaded.reminder_30d_sent or reloaded.reminder_0d_sent)
        audit = (await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == AuditAction.UPDATED)
        )).scalar_one()
        assert "warranty_info.warranty_end_date" in json.loads(audit.details)

    async def test_patch_of_deleted_document_warranty_404(self, client, db_session):
        w = await _warranty(db_session, date.today() + timedelta(days=10), status=DocumentStatus.DELETED)
        await db_session.commit()
        assert (await client.patch(f"/api/warranties/{w.id}", json={"notes": "x"})).status_code == 404


@pytest.mark.asyncio
class TestWarrantyStatusBoundaries:
    async def test_unknown_status_filter_rejected(self, client):
        assert (await client.get("/api/warranties?status=bogus")).status_code == 422

    async def test_one_definition_for_list_stats_and_is_expired(self, client, db_session):
        today = date.today()
        await _warranty(db_session, today)                          # letzter Tag: aktiv + laeuft ab
        await _warranty(db_session, today - timedelta(days=1))      # abgelaufen
        await _warranty(db_session, today + timedelta(days=90))     # laeuft ab
        await _warranty(db_session, today + timedelta(days=91))     # aktiv
        await _warranty(db_session, today + timedelta(days=5), status=DocumentStatus.DELETED)
        await db_session.commit()

        stats = (await client.get("/api/warranties/stats")).json()
        assert stats == {"total": 4, "active": 3, "expiring_soon": 2, "expired": 1}

        async def ends(status):
            items = (await client.get(f"/api/warranties?status={status}")).json()
            return sorted((date.fromisoformat(i["warranty_end_date"]) - today).days for i in items)

        assert await ends("active") == [0, 90, 91]
        assert await ends("expiring") == [0, 90]
        assert await ends("expired") == [-1]

        items = {(date.fromisoformat(i["warranty_end_date"]) - today).days: i
                 for i in (await client.get("/api/warranties")).json()}
        assert items[0]["is_expired"] is False
        assert items[-1]["is_expired"] is True
        assert "document_thumbnail" not in items[0]
