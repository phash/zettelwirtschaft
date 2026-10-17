import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.email_account import EmailAccount, ScheduleType
from app.services import email_scheduler_service
from app.services.email_fetch_service import account_fetch_lock
from app.services.email_scheduler_service import (
    INVALID_CRON_MESSAGE,
    run_email_scheduler,
    run_scheduler_tick,
    should_fetch_now,
)


def test_should_fetch_cron_due():
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = "*/15 * * * *"
    account.last_checked_at = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 3, 3, 10, 16, 0, tzinfo=timezone.utc)
    assert should_fetch_now(account, now) is True


def test_should_fetch_cron_not_due():
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = "*/15 * * * *"
    account.last_checked_at = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 3, 3, 10, 5, 0, tzinfo=timezone.utc)
    assert should_fetch_now(account, now) is False


def test_should_fetch_manual_never():
    account = MagicMock()
    account.schedule_type = ScheduleType.MANUAL
    assert should_fetch_now(account, datetime.now(timezone.utc)) is False


def test_should_fetch_never_checked():
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = "*/15 * * * *"
    account.last_checked_at = None
    assert should_fetch_now(account, datetime.now(timezone.utc)) is True


def test_should_fetch_idle_due():
    account = MagicMock()
    account.schedule_type = ScheduleType.IDLE
    account.last_checked_at = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 3, 3, 10, 6, 0, tzinfo=timezone.utc)  # 6 min > 5 min
    assert should_fetch_now(account, now) is True


def test_should_fetch_idle_not_due():
    account = MagicMock()
    account.schedule_type = ScheduleType.IDLE
    account.last_checked_at = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 3, 3, 10, 3, 0, tzinfo=timezone.utc)  # 3 min < 5 min
    assert should_fetch_now(account, now) is False


def test_should_fetch_idle_never_checked():
    account = MagicMock()
    account.schedule_type = ScheduleType.IDLE
    account.last_checked_at = None
    assert should_fetch_now(account, datetime.now(timezone.utc)) is True


# ---------------------------------------------------------------------------
# Zeitzone (CRON in Ortszeit)
# ---------------------------------------------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")


def _cron_account(expr, last_checked):
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = expr
    account.last_checked_at = last_checked
    return account


@pytest.mark.parametrize("last_checked, due_utc", [
    # Sommer (MESZ, UTC+2): 8 Uhr Ortszeit = 06:00 UTC
    (datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc), datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)),
    # Winter (MEZ, UTC+1): 8 Uhr Ortszeit = 07:00 UTC
    (datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc), datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)),
    # Umstellung auf Sommerzeit (29.03.2026): Lauf am Folgetag um 06:00 UTC
    (datetime(2026, 3, 28, 7, 30, tzinfo=timezone.utc), datetime(2026, 3, 29, 6, 0, tzinfo=timezone.utc)),
    # Umstellung auf Winterzeit (25.10.2026): Lauf am Folgetag um 07:00 UTC
    (datetime(2026, 10, 24, 6, 30, tzinfo=timezone.utc), datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)),
], ids=["summer", "winter", "dst-start", "dst-end"])
def test_cron_is_evaluated_in_local_time(last_checked, due_utc):
    account = _cron_account("0 8 * * *", last_checked)
    assert should_fetch_now(account, due_utc - timedelta(minutes=1), BERLIN) is False
    assert should_fetch_now(account, due_utc, BERLIN) is True


def test_invalid_cron_raises_for_caller():
    with pytest.raises(ValueError):
        should_fetch_now(_cron_account("nonsense", datetime(2026, 1, 1, tzinfo=timezone.utc)),
                         datetime(2026, 1, 2, tzinfo=timezone.utc), BERLIN)


# ---------------------------------------------------------------------------
# Scheduler-Tick: Konten isoliert
# ---------------------------------------------------------------------------


async def _add_accounts(factory, *specs) -> list[int]:
    ids = []
    async with factory() as s:
        for name, schedule, cron in specs:
            account = EmailAccount(name=name, imap_host="h", username="u", encrypted_password="x",
                                   schedule_type=schedule, cron_expression=cron)
            s.add(account)
            await s.flush()
            ids.append(account.id)
        await s.commit()
    return ids


def _stats(remaining=0):
    return {"total": 0, "relevant": 0, "irrelevant": 0, "skipped": 0, "failed": 0,
            "remaining": remaining, "error": None}


@pytest.fixture(autouse=True)
def _clear_backlog():
    email_scheduler_service._backlog.clear()
    yield
    email_scheduler_service._backlog.clear()


async def test_invalid_cron_does_not_block_following_accounts(test_session_factory, test_settings):
    bad, good = await _add_accounts(
        test_session_factory, ("A kaputt", ScheduleType.CRON, "61 * * * *"), ("B ok", ScheduleType.IDLE, None),
    )
    async with test_session_factory() as s:  # beide schon einmal gelaufen
        for acc in (await s.execute(select(EmailAccount))).scalars():
            acc.last_checked_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await s.commit()
    fetch = AsyncMock(return_value=_stats())
    with patch.object(email_scheduler_service, "fetch_emails_for_account", fetch):
        await run_scheduler_tick(test_session_factory, test_settings, datetime(2026, 1, 2, tzinfo=timezone.utc))

    assert [c.args[0] for c in fetch.await_args_list] == [good]
    async with test_session_factory() as s:
        assert (await s.get(EmailAccount, bad)).last_error == INVALID_CRON_MESSAGE


async def test_failing_account_does_not_skip_the_next(test_session_factory, test_settings):
    first, second = await _add_accounts(
        test_session_factory, ("A", ScheduleType.IDLE, None), ("B", ScheduleType.IDLE, None),
    )
    fetch = AsyncMock(side_effect=[RuntimeError("kaputt"), _stats()])
    with patch.object(email_scheduler_service, "fetch_emails_for_account", fetch):
        await run_scheduler_tick(test_session_factory, test_settings)
    assert [c.args[0] for c in fetch.await_args_list] == [first, second]


async def test_tick_skips_account_with_running_fetch(test_session_factory, test_settings):
    [account_id] = await _add_accounts(test_session_factory, ("A", ScheduleType.IDLE, None))
    fetch = AsyncMock(return_value=_stats())
    async with account_fetch_lock(account_id):
        with patch.object(email_scheduler_service, "fetch_emails_for_account", fetch):
            await run_scheduler_tick(test_session_factory, test_settings)
    fetch.assert_not_awaited()


async def test_backlog_continues_on_next_tick(test_session_factory, test_settings):
    [account_id] = await _add_accounts(test_session_factory, ("A", ScheduleType.CRON, "0 8 * * *"))
    fetch = AsyncMock(side_effect=[_stats(remaining=5), _stats()])
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    with patch.object(email_scheduler_service, "fetch_emails_for_account", fetch):
        await run_scheduler_tick(test_session_factory, test_settings, now)  # nie gelaufen -> faellig
        async with test_session_factory() as s:
            (await s.get(EmailAccount, account_id)).last_checked_at = now
            await s.commit()
        # Laut Zeitplan erst morgen faellig, aber Limit erreicht -> sofort weiter
        await run_scheduler_tick(test_session_factory, test_settings, now + timedelta(minutes=1))
        await run_scheduler_tick(test_session_factory, test_settings, now + timedelta(minutes=2))
    assert fetch.await_count == 2


async def test_scheduler_loop_runs_ticks_until_cancelled(test_session_factory, test_settings):
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    tick = AsyncMock()
    with patch.object(email_scheduler_service.asyncio, "sleep", sleep), \
         patch.object(email_scheduler_service, "run_scheduler_tick", tick):
        await run_email_scheduler(test_session_factory, test_settings)
    tick.assert_awaited_once()
