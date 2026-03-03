from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models.email_account import ScheduleType
from app.services.email_scheduler_service import should_fetch_now


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
