from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting


async def get_db_setting(session: AsyncSession, key: str, default: str = "") -> str:
    """Liest eine Einstellung aus der Datenbank. Fallback auf default falls nicht vorhanden."""
    result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    s = result.scalar_one_or_none()
    return s.value if s and s.value is not None else default


async def set_db_setting(session: AsyncSession, key: str, value: str) -> None:
    """Schreibt eine Einstellung in die Datenbank (Upsert)."""
    result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    s = result.scalar_one_or_none()
    if s:
        s.value = value
        s.updated_at = datetime.now(timezone.utc)
    else:
        session.add(SystemSetting(key=key, value=value, updated_at=datetime.now(timezone.utc)))
