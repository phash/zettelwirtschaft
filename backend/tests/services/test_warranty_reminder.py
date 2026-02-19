"""Tests fuer Garantie-Erinnerungen."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.document import Document, DocumentType, TaxCategory
from app.models.notification import Notification
from app.models.warranty_info import WarrantyInfo, WarrantyType
from app.services.warranty_reminder_service import check_warranty_reminders


async def _create_doc_with_warranty(session, end_date: date) -> WarrantyInfo:
    """Hilfsfunktion: Erstellt ein Dokument mit Garantie."""
    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        original_filename="receipt.pdf",
        stored_filename=f"{doc_id}_receipt.pdf",
        file_path=f"./data/archive/{doc_id}_receipt.pdf",
        file_type="pdf",
        file_size_bytes=1000,
        file_hash=str(uuid.uuid4()),
        document_type=DocumentType.QUITTUNG,
        title="Testprodukt",
        ocr_text="test",
    )
    session.add(doc)
    await session.flush()

    warranty = WarrantyInfo(
        document_id=doc_id,
        product_name="Testprodukt",
        purchase_date=end_date - timedelta(days=730),
        warranty_end_date=end_date,
        warranty_type=WarrantyType.LEGAL,
        warranty_duration_months=24,
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

    async def test_creates_notification_at_expiry(self, db_session):
        """Erinnerung am Tag des Ablaufs."""
        end_date = date.today()
        await _create_doc_with_warranty(db_session, end_date)

        count = await check_warranty_reminders(db_session)
        assert count == 1

        result = await db_session.execute(select(Notification))
        notif = result.scalar_one()
        assert "abgelaufen" in notif.title

    async def test_no_duplicate_reminders(self, db_session):
        """Keine doppelten Erinnerungen."""
        end_date = date.today() + timedelta(days=90)
        await _create_doc_with_warranty(db_session, end_date)

        count1 = await check_warranty_reminders(db_session)
        assert count1 == 1

        count2 = await check_warranty_reminders(db_session)
        assert count2 == 0
