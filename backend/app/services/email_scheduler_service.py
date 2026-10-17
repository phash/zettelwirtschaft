"""Scheduler fuer automatischen E-Mail-Abruf.

Cron-Expressions werden in der **lokalen Zeitzone** (Setting ``TIMEZONE``,
Default ``Europe/Berlin``) ausgewertet: ``0 8 * * *`` = taeglich 8 Uhr Ortszeit,
auch ueber Sommer-/Winterzeit-Wechsel hinweg. Verglichen wird in UTC.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo

from croniter import CroniterError, croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.email_account import EmailAccount, ScheduleType
from app.services.email_fetch_service import account_fetch_lock, fetch_emails_for_account

logger = logging.getLogger(__name__)

SCHEDULER_POLL_INTERVAL = 60
IDLE_INTERVAL_SECONDS = 300
INVALID_CRON_MESSAGE = "Ungültiger CRON-Ausdruck — bitte im Konto korrigieren."


def should_fetch_now(account, now: datetime, tz: tzinfo = timezone.utc) -> bool:
    """Prueft ob ein Konto jetzt abgerufen werden soll.

    Bei CRON-Schedule: vermeidet Drift nach Outages (H-15) — wenn der naechste
    geplante Lauf laut letztem Check in der Vergangenheit liegt, triggert sofort
    EIN Fetch (kein Catch-up von verpassten Slots, das wuerde nur LLM-Kosten
    verschwenden). Die Cron-Felder gelten in ``tz``.

    Raises: CroniterError bei ungueltigem Ausdruck (Aufrufer protokolliert).
    """
    if account.schedule_type == ScheduleType.MANUAL:
        return False

    if account.schedule_type == ScheduleType.IDLE:
        if not account.last_checked_at:
            return True
        diff = (now - account.last_checked_at).total_seconds()
        return diff >= IDLE_INTERVAL_SECONDS

    if account.schedule_type == ScheduleType.CRON:
        if not account.cron_expression:
            return False
        if not account.last_checked_at:
            return True
        last_checked_local = account.last_checked_at.astimezone(tz)
        cron = croniter(account.cron_expression, last_checked_local)
        next_run = cron.get_next(datetime)
        return now >= next_run

    return False


@dataclass(frozen=True)
class _ScheduleSnapshot:
    id: int
    name: str
    schedule_type: ScheduleType
    cron_expression: str | None
    last_checked_at: datetime | None


# Konten, deren letzter Lauf am EMAIL_MAX_PER_RUN-Limit endete — werden beim
# naechsten Tick unabhaengig vom Zeitplan weiter abgearbeitet.
_backlog: set[int] = set()


async def _load_active_accounts(session_factory: async_sessionmaker[AsyncSession]) -> list[_ScheduleSnapshot]:
    async with session_factory() as session:
        result = await session.execute(
            select(EmailAccount).where(EmailAccount.is_active == True)  # noqa: E712
        )
        return [
            _ScheduleSnapshot(
                id=a.id,
                name=a.name,
                schedule_type=a.schedule_type,
                cron_expression=a.cron_expression,
                last_checked_at=a.last_checked_at,
            )
            for a in result.scalars().all()
        ]


async def _set_last_error(
    session_factory: async_sessionmaker[AsyncSession], account_id: int, message: str
) -> None:
    async with session_factory() as session:
        account = await session.get(EmailAccount, account_id)
        if account is not None and account.last_error != message:
            account.last_error = message
            await session.commit()


async def run_scheduler_tick(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime | None = None,
) -> None:
    """Ein Scheduler-Durchlauf: jedes faellige Konto isoliert abrufen.

    Fehler eines Kontos (ungueltiger Cron, IMAP, DB) betreffen nie die
    nachfolgenden Konten; jedes Konto arbeitet mit eigenen Sessions.
    """
    now = now or datetime.now(timezone.utc)
    tz = settings.local_tz
    for snapshot in await _load_active_accounts(session_factory):
        try:
            try:
                due = snapshot.id in _backlog or should_fetch_now(snapshot, now, tz)
            except (CroniterError, ValueError, KeyError):
                logger.warning("Ungueltiger CRON-Ausdruck fuer %s: %r", snapshot.name, snapshot.cron_expression)
                await _set_last_error(session_factory, snapshot.id, INVALID_CRON_MESSAGE)
                continue
            if not due:
                continue

            lock = account_fetch_lock(snapshot.id)
            if lock.locked():
                logger.info("Abruf fuer %s laeuft bereits — Scheduler ueberspringt", snapshot.name)
                continue
            async with lock:
                logger.info("Automatischer E-Mail-Abruf fuer: %s", snapshot.name)
                stats = await fetch_emails_for_account(snapshot.id, session_factory, settings)
            logger.info("Abruf %s: %s", snapshot.name, stats)
            if stats.get("remaining") and snapshot.schedule_type != ScheduleType.MANUAL:
                _backlog.add(snapshot.id)
            else:
                _backlog.discard(snapshot.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fehler beim automatischen Abruf fuer %s", snapshot.name)


async def run_email_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Background-Task: prueft periodisch ob E-Mail-Konten abgerufen werden muessen."""
    logger.info(
        "E-Mail-Scheduler gestartet (Intervall: %ds, Zeitzone: %s)",
        SCHEDULER_POLL_INTERVAL, settings.local_tz,
    )

    while True:
        try:
            await asyncio.sleep(SCHEDULER_POLL_INTERVAL)
            await run_scheduler_tick(session_factory, settings)
        except asyncio.CancelledError:
            logger.info("E-Mail-Scheduler beendet")
            return
        except Exception:
            logger.exception("Unerwarteter Fehler im E-Mail-Scheduler")
            await asyncio.sleep(30)
