"""SQLite-Laufzeitverhalten: Verbindungs-PRAGMAs, FK-Semantik, UTC-Zeitstempel."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import DateTime, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db_types import UTCDateTime
from app.database import SQLITE_BUSY_TIMEOUT_MS, Base, configure_sqlite_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_keys", [True, False])
async def test_sqlite_engine_pragmas(tmp_path, foreign_keys):
    """Ohne busy_timeout scheiterte jede ueberlappende Schreib-Transaktion
    sofort mit "database is locked" (HTTP 500). App und Tests nutzen
    foreign_keys=True, Alembic foreign_keys=False."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    configure_sqlite_engine(eng.sync_engine, foreign_keys=foreign_keys)
    try:
        async with eng.connect() as conn:
            assert (await conn.execute(text("PRAGMA busy_timeout"))).scalar() == SQLITE_BUSY_TIMEOUT_MS
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == int(foreign_keys)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_test_engine_enforces_foreign_keys(db_session):
    """Regression: die Test-Engine lief ohne FK-Pruefung — Tests bestanden, die
    in Produktion an FK-Constraints scheitern."""
    from app.models.chat_message import ChatMessage, MessageRole

    db_session.add(ChatMessage(role=MessageRole.USER, content="x", filing_scope_id="gibt-es-nicht"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_deleting_scope_nulls_chat_messages(db_session):
    from app.models.chat_message import ChatMessage, MessageRole
    from app.models.filing_scope import FilingScope

    scope = FilingScope(name="Praxis", slug="praxis")
    db_session.add(scope)
    await db_session.flush()
    msg = ChatMessage(role=MessageRole.USER, content="Frage", filing_scope_id=scope.id)
    db_session.add(msg)
    await db_session.commit()

    await db_session.delete(scope)
    await db_session.commit()
    await db_session.refresh(msg)
    assert msg.filing_scope_id is None


@pytest.mark.asyncio
async def test_deleting_document_cascades_document_tags(db_session):
    from app.models.document import DocumentTag, Tag
    from tests.factories import make_document

    doc = await make_document(db_session)
    tag = Tag(name="rechnung")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
    await db_session.commit()

    await db_session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc.id})
    await db_session.commit()
    remaining = (await db_session.execute(select(DocumentTag))).scalars().all()
    assert remaining == []


def test_all_timestamp_columns_use_utc_type():
    """Guard: SQLite liefert DateTime-Spalten offsetlos zurueck — der Browser
    las sie als Ortszeit (1-2 h Versatz). Alle Zeitstempel-Spalten muessen
    UTCDateTime nutzen."""
    import app.models  # noqa: F401 - alle Modelle registrieren

    offenders = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, DateTime) and not isinstance(col.type, UTCDateTime)
    ]
    assert offenders == []


@pytest.mark.asyncio
async def test_api_timestamps_carry_utc_offset(client, db_session):
    from app.models.filing_scope import FilingScope

    db_session.add(FilingScope(name="Privat", slug="privat", is_default=True))
    await db_session.commit()
    scopes = (await client.get("/api/filing-scopes")).json()
    created = datetime.fromisoformat(scopes[0]["created_at"].replace("Z", "+00:00"))
    assert created.utcoffset() is not None
    assert abs((datetime.now(timezone.utc) - created).total_seconds()) < 300
