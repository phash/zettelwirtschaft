"""Scheduler fuer automatischen E-Mail-Abruf."""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.email_account import EmailAccount, ScheduleType
from app.services.email_fetch_service import fetch_emails_for_account

logger = logging.getLogger(__name__)

SCHEDULER_POLL_INTERVAL = 60


def should_fetch_now(account, now: datetime) -> bool:
    """Prueft ob ein Konto jetzt abgerufen werden soll."""
    if account.schedule_type == ScheduleType.MANUAL:
        return False

    if account.schedule_type == ScheduleType.IDLE:
        if not account.last_checked_at:
            return True
        diff = (now - account.last_checked_at).total_seconds()
        return diff >= 300

    if account.schedule_type == ScheduleType.CRON:
        if not account.cron_expression:
            return False
        if not account.last_checked_at:
            return True
        cron = croniter(account.cron_expression, account.last_checked_at)
        next_run = cron.get_next(datetime)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        return now >= next_run

    return False


async def run_email_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Background-Task: prueft periodisch ob E-Mail-Konten abgerufen werden muessen."""
    logger.info("E-Mail-Scheduler gestartet (Intervall: %ds)", SCHEDULER_POLL_INTERVAL)

    while True:
        try:
            await asyncio.sleep(SCHEDULER_POLL_INTERVAL)
            now = datetime.now(timezone.utc)

            async with session_factory() as session:
                result = await session.execute(
                    select(EmailAccount).where(EmailAccount.is_active == True)  # noqa: E712
                )
                accounts = result.scalars().all()

                for account in accounts:
                    if should_fetch_now(account, now):
                        logger.info("Automatischer E-Mail-Abruf fuer: %s", account.name)
                        try:
                            stats = await fetch_emails_for_account(account, session, settings)
                            logger.info("Abruf %s: %s", account.name, stats)
                            await session.commit()
                        except Exception:
                            logger.exception("Fehler beim automatischen Abruf fuer %s", account.name)
                            await session.rollback()

        except asyncio.CancelledError:
            logger.info("E-Mail-Scheduler beendet")
            return
        except Exception:
            logger.exception("Unerwarteter Fehler im E-Mail-Scheduler")
            await asyncio.sleep(30)
