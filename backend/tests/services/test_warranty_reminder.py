"""Tests fuer Garantie-Erinnerungen."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.document import DocumentStatus, DocumentType
from app.models.notification import Notification, NotificationType
from app.models.warranty_info import WarrantyInfo, WarrantyType
from app.services.warranty_reminder_service import check_warranty_reminders
from tests.factories import make_document


async def _create_doc_with_warranty(
    session,
    end_date: date,
    *,
    status: DocumentStatus = DocumentStatus.ACTIVE,
    created_days_ago: int = 0,
) -> WarrantyInfo:
    """Hilfsfunktion: Erstellt ein Dokument mit Garantie."""
    doc = await make_document(
        session, document_type=DocumentType.QUITTUNG, title="Testprodukt", status=status
    )

    warranty = WarrantyInfo(
        document_id=doc.id,
        product_name="Testprodukt",
        purchase_date=end_date - timedelta(days=730),
        warranty_end_date=end_date,
        warranty_type=WarrantyType.LEGAL,
        warranty_duration_months=24,
        created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
    )
    session.add(warranty)
    await session.commit()
    return warranty


@pytest.mark.asyncio
class TestWarrantyReminder:
    async def test_no_expiring(self, db_session):
        """Keine Erinnerungen wenn nichts ablaeuft."""
        count = await check_warranty_reminders(db_session)
        assert count == 0

    async def test_creates_notification_at_90_days(self, db_session):
        """Erinnerung 90 Tage vor Ablauf."""
        end_date = date.today() + timedelta(days=90)
        await _create_doc_with_warranty(db_session, end_date)

        count = await check_warranty_reminders(db_session)
        assert count == 1

        result = await db_session.execute(select(Notification))
        notif = result.scalar_one()
        assert "90 Tagen" in notif.title
        assert notif.is_read is False

    async def test_creates_notification_on_last_day(self, db_session):
        """Am Enddatum gilt die Garantie noch: 'läuft heute ab' (nicht 'abgelaufen')."""
        end_date = date.today()
        await _create_doc_with_warranty(db_session, end_date)

        count = await check_warranty_reminders(db_session)
        assert count == 1

        result = await db_session.execute(select(Notification))
        notif = result.scalar_one()
        assert notif.type == NotificationType.WARRANTY_EXPIRING
        assert "läuft heute ab" in notif.title

    async def test_creates_notification_after_expiry(self, db_session):
        """Abgelaufen (gestern) -> 'abgelaufen' mit echtem Datum."""
        end_date = date.today() - timedelta(days=1)
        await _create_doc_with_warranty(db_session, end_date, created_days_ago=400)

        count = await check_warranty_reminders(db_session)
        assert count == 1

        result = await db_session.execute(select(Notification))
        notif = result.scalar_one()
        assert notif.type == NotificationType.WARRANTY_EXPIRED
        assert "abgelaufen" in notif.title
        assert f"ist am {end_date.strftime('%d.%m.%Y')} abgelaufen" in notif.message

    async def test_no_duplicate_reminders(self, db_session):
        """Keine doppelten Erinnerungen."""
        end_date = date.today() + timedelta(days=90)
        await _create_doc_with_warranty(db_session, end_date)

        count1 = await check_warranty_reminders(db_session)
        assert count1 == 1

        count2 = await check_warranty_reminders(db_session)
        assert count2 == 0


@pytest.mark.asyncio
class TestWarrantyReminderEdgeCases:
    """Regressionen: Fenster [N-7, N] liessen kurzfristige Garantien aus,
    Texte nannten immer '90'/'30 Tage', geloeschte Dokumente bekamen Erinnerungen."""

    async def _titles(self, db_session) -> list[str]:
        result = await db_session.execute(select(Notification.title))
        return [row[0] for row in result.all()]

    async def test_15_days_left_gets_one_reminder_with_real_days(self, db_session):
        await _create_doc_with_warranty(db_session, date.today() + timedelta(days=15))

        assert await check_warranty_reminders(db_session) == 1
        assert await self._titles(db_session) == ["Garantie läuft in 15 Tagen ab: Testprodukt"]
        # 90-Tage-Erinnerung wird nicht nachgeholt
        assert await check_warranty_reminders(db_session) == 0

    async def test_60_days_left_gets_first_reminder(self, db_session):
        warranty = await _create_doc_with_warranty(db_session, date.today() + timedelta(days=60))

        assert await check_warranty_reminders(db_session) == 1
        assert await self._titles(db_session) == ["Garantie läuft in 60 Tagen ab: Testprodukt"]
        await db_session.refresh(warranty)
        assert warranty.reminder_90d_sent is True
        assert warranty.reminder_30d_sent is False

    async def test_90_then_30_day_reminder(self, db_session):
        warranty = await _create_doc_with_warranty(db_session, date.today() + timedelta(days=30))
        warranty.reminder_90d_sent = True
        await db_session.commit()

        assert await check_warranty_reminders(db_session) == 1
        assert await self._titles(db_session) == ["Garantie läuft in 30 Tagen ab: Testprodukt"]

    async def test_expired_three_days_ago_in_lookback(self, db_session):
        end = date.today() - timedelta(days=3)
        await _create_doc_with_warranty(db_session, end, created_days_ago=500)

        assert await check_warranty_reminders(db_session) == 1
        result = await db_session.execute(select(Notification))
        notif = result.scalar_one()
        assert notif.message == f"Die Garantie für 'Testprodukt' ist am {end.strftime('%d.%m.%Y')} abgelaufen."

    async def test_expired_longer_than_lookback_is_silent(self, db_session):
        await _create_doc_with_warranty(db_session, date.today() - timedelta(days=10), created_days_ago=500)
        assert await check_warranty_reminders(db_session) == 0

    async def test_receipt_scanned_after_expiry_is_silent(self, db_session):
        """Beleg erst nach Ablauf erfasst -> keine 'abgelaufen'-Meldung."""
        await _create_doc_with_warranty(db_session, date.today() - timedelta(days=2), created_days_ago=0)
        assert await check_warranty_reminders(db_session) == 0

    async def test_deleted_document_gets_no_reminder(self, db_session):
        await _create_doc_with_warranty(
            db_session, date.today() + timedelta(days=15), status=DocumentStatus.DELETED,
        )
        assert await check_warranty_reminders(db_session) == 0

    async def test_rearm_after_end_date_change(self, db_session):
        warranty = await _create_doc_with_warranty(db_session, date.today() + timedelta(days=20))
        assert await check_warranty_reminders(db_session) == 1

        warranty.warranty_end_date = date.today() + timedelta(days=80)
        warranty.rearm_reminders()
        await db_session.commit()

        assert await check_warranty_reminders(db_session) == 1
        assert "in 80 Tagen" in (await self._titles(db_session))[-1]
