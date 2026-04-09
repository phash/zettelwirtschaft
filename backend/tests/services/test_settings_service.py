"""Tests fuer Settings-Service (DB-basierte Key-Value-Einstellungen)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.models.system_setting  # noqa: F401  (registriert Tabelle bei Base.metadata)

from app.services.settings_service import get_db_setting, set_db_setting


@pytest.mark.asyncio
class TestGetDbSetting:
    async def test_get_default_when_not_set(self, db_session: AsyncSession):
        value = await get_db_setting(db_session, "nicht_vorhanden", default="fallback")
        assert value == "fallback"

    async def test_get_default_empty_string(self, db_session: AsyncSession):
        value = await get_db_setting(db_session, "nicht_vorhanden")
        assert value == ""


@pytest.mark.asyncio
class TestSetDbSetting:
    async def test_set_and_get(self, db_session: AsyncSession):
        await set_db_setting(db_session, "theme", "dark")
        await db_session.flush()

        value = await get_db_setting(db_session, "theme")
        assert value == "dark"

    async def test_set_update_existing(self, db_session: AsyncSession):
        await set_db_setting(db_session, "language", "de")
        await db_session.flush()

        await set_db_setting(db_session, "language", "en")
        await db_session.flush()

        value = await get_db_setting(db_session, "language")
        assert value == "en"

    async def test_set_multiple_keys(self, db_session: AsyncSession):
        await set_db_setting(db_session, "key_a", "wert_a")
        await set_db_setting(db_session, "key_b", "wert_b")
        await set_db_setting(db_session, "key_c", "wert_c")
        await db_session.flush()

        assert await get_db_setting(db_session, "key_a") == "wert_a"
        assert await get_db_setting(db_session, "key_b") == "wert_b"
        assert await get_db_setting(db_session, "key_c") == "wert_c"
