"""Scheduler fuer automatischen E-Mail-Abruf.

Cron-Expressions werden in **UTC** ausgewertet (siehe `should_fetch_now`).
Wer „taeglich um 8 Uhr Lokalzeit" will und in MESZ lebt, traegt `0 6 * * *` ein
(8h - 2h Sommerzeit). Future Work: Settings-Option `TZ` mit `ZoneInfo`.
"""

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


def _ensure_utc(dt: datetime) -> datetime:
    """Naive Datetime defensiv als UTC interpretieren."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def should_fetch_now(account, now: datetime) -> bool:
    """Prueft ob ein Konto jetzt abgerufen werden soll.

    Bei CRON-Schedule: vermeidet Drift nach Outages (H-15) — wenn der naechste
    geplante Lauf laut letztem Check in der Vergangenheit liegt, triggert sofort
    EIN Fetch (kein Catch-up von verpassten Slots, das wuerde nur LLM-Kosten
    verschwenden).
    """
    if account.schedule_type == ScheduleType.MANUAL:
        return False

    if account.schedule_type == ScheduleType.IDLE:
        if not account.last_checked_at:
            return True
        diff = (now - _ensure_utc(account.last_checked_at)).total_seconds()
        return diff >= 300

    if account.schedule_type == ScheduleType.CRON:
        if not account.cron_expression:
            return False
        if not account.last_checked_at:
            return True
        last_checked = _ensure_utc(account.last_checked_at)
        cron = croniter(account.cron_expression, last_checked)
        next_run = _ensure_utc(cron.get_next(datetime))
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
