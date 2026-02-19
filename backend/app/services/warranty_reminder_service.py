"""Garantie-Erinnerungen: Prueft taeglich auf ablaufende Garantien."""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.warranty_info import WarrantyInfo

logger = logging.getLogger(__name__)

REMINDER_DAYS = [90, 30, 0]


async def check_warranty_reminders(session: AsyncSession) -> int:
    """Prueft alle Garantien und erstellt Benachrichtigungen fuer ablaufende."""
    today = date.today()
    created = 0

    for days in REMINDER_DAYS:
        target_date = today + timedelta(days=days)
        stmt = (
            select(WarrantyInfo)
            .where(WarrantyInfo.warranty_end_date == target_date)
            .where(WarrantyInfo.reminder_sent.is_(False))
        )
        result = await session.execute(stmt)
        warranties = result.scalars().all()

        for w in warranties:
            if days == 0:
                title = f"Garantie abgelaufen: {w.product_name}"
                msg = f"Die Garantie fuer '{w.product_name}' ist heute abgelaufen."
                ntype = NotificationType.WARRANTY_EXPIRED
            else:
                title = f"Garantie laeuft in {days} Tagen ab: {w.product_name}"
                msg = (
                    f"Die Garantie fuer '{w.product_name}' laeuft am "
                    f"{w.warranty_end_date.strftime('%d.%m.%Y')} ab ({days} Tage)."
                )
                ntype = NotificationType.WARRANTY_EXPIRING

            notification = Notification(
                type=ntype,
                title=title,
                message=msg,
                document_id=w.document_id,
            )
            session.add(notification)
            w.reminder_sent = True
            created += 1

    if created:
        await session.commit()
        logger.info("%d Garantie-Erinnerungen erstellt", created)

    return created


async def run_warranty_reminder(session_factory, settings) -> None:
    """Background-Task: Prueft einmal taeglich auf ablaufende Garantien."""
    logger.info("Garantie-Reminder-Service gestartet")
    while True:
        try:
            async with session_factory() as session:
                await check_warranty_reminders(session)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Fehler im Garantie-Reminder-Service")
        # Einmal pro Stunde pruefen
        await asyncio.sleep(3600)
