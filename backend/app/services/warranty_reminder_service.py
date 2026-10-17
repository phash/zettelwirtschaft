"""Garantie-Erinnerungen: prueft stuendlich auf ablaufende Garantien.

Drei Erinnerungen pro Garantie (Flags verhindern Doppelte):

- 90 Tage: Enddatum in (heute+30, heute+90]
- 30 Tage: Enddatum in [heute, heute+30] — setzt auch das 90-Tage-Flag, damit
  ein spaet erfasster Beleg nicht zwei Erinnerungen auf einmal bekommt
- abgelaufen: Enddatum < heute, hoechstens 7 Tage her (Nachholfenster fuer
  Service-Ausfaelle) und nicht vor der Erfassung der Garantie

Vorher lagen die Fenster bei [heute+N-7, heute+N] — ein Beleg, der 15 Tage
vor Ablauf gescannt wurde, bekam nie eine Erinnerung, und die Texte nannten
immer "90"/"30 Tage", egal wie viele Tage wirklich blieben.
"""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.warranty_info import WarrantyInfo, select_active_document_warranties

logger = logging.getLogger(__name__)

FIRST_REMINDER_DAYS = 90
SECOND_REMINDER_DAYS = 30
# Nachholfenster fuer die "abgelaufen"-Meldung: deckt Service-Ausfaelle ab,
# ohne Wochen spaeter noch ueber laengst abgelaufene Garantien zu informieren.
EXPIRED_LOOKBACK_DAYS = 7
CHECK_INTERVAL_SECONDS = 3600


def _days_phrase(days: int) -> str:
    if days == 0:
        return "heute"
    if days == 1:
        return "morgen"
    return f"in {days} Tagen"


def _expiring_notification(w: WarrantyInfo, today: date) -> Notification:
    days = (w.warranty_end_date - today).days
    end = w.warranty_end_date.strftime("%d.%m.%Y")
    return Notification(
        type=NotificationType.WARRANTY_EXPIRING,
        title=f"Garantie läuft {_days_phrase(days)} ab: {w.product_name}",
        message=f"Die Garantie für '{w.product_name}' läuft am {end} ab.",
        document_id=w.document_id,
    )


def _expired_notification(w: WarrantyInfo) -> Notification:
    end = w.warranty_end_date.strftime("%d.%m.%Y")
    return Notification(
        type=NotificationType.WARRANTY_EXPIRED,
        title=f"Garantie abgelaufen: {w.product_name}",
        message=f"Die Garantie für '{w.product_name}' ist am {end} abgelaufen.",
        document_id=w.document_id,
    )


async def check_warranty_reminders(session: AsyncSession) -> int:
    """Prueft alle Garantien (ohne geloeschte Dokumente) und erstellt faellige
    Benachrichtigungen. Gibt die Anzahl neuer Benachrichtigungen zurueck."""
    today = date.today()
    first_threshold = today + timedelta(days=FIRST_REMINDER_DAYS)
    expired_since = today - timedelta(days=EXPIRED_LOOKBACK_DAYS)

    stmt = (
        select_active_document_warranties()
        .where(WarrantyInfo.warranty_end_date >= expired_since)
        .where(WarrantyInfo.warranty_end_date <= first_threshold)
        .where(or_(
            WarrantyInfo.reminder_90d_sent.is_(False),
            WarrantyInfo.reminder_30d_sent.is_(False),
            WarrantyInfo.reminder_0d_sent.is_(False),
        ))
    )
    warranties = (await session.execute(stmt)).scalars().all()

    created = 0
    for w in warranties:
        days_left = (w.warranty_end_date - today).days
        notification = None
        if days_left < 0:
            recorded_on = w.created_at.date() if w.created_at else expired_since
            if not w.reminder_0d_sent and w.warranty_end_date >= recorded_on:
                notification = _expired_notification(w)
            # Abgelaufen: keine "laeuft ab"-Meldung mehr nachholen.
            w.reminder_0d_sent = w.reminder_30d_sent = w.reminder_90d_sent = True
        elif days_left <= SECOND_REMINDER_DAYS:
            if not w.reminder_30d_sent:
                notification = _expiring_notification(w, today)
            w.reminder_30d_sent = w.reminder_90d_sent = True
        elif not w.reminder_90d_sent:
            notification = _expiring_notification(w, today)
            w.reminder_90d_sent = True

        if notification is not None:
            session.add(notification)
            w.reminder_sent = True  # Backward-Kompatibilitaet
            created += 1

    await session.commit()
    if created:
        logger.info("%d Garantie-Erinnerungen erstellt", created)
    return created


async def run_warranty_reminder(session_factory) -> None:
    """Background-Task: prueft einmal pro Stunde auf faellige Erinnerungen."""
    logger.info("Garantie-Reminder-Service gestartet")
    while True:
        try:
            async with session_factory() as session:
                await check_warranty_reminders(session)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Fehler im Garantie-Reminder-Service")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
