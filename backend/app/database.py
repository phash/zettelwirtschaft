from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
)


# K-2 (Re-Review): SQLite ignoriert ondelete=SET NULL/CASCADE per Default,
# bis "PRAGMA foreign_keys=ON" pro Verbindung gesetzt wird. Ohne diesen
# Listener waren die FK-Constraints aus Migration 013 (chat_messages,
# documents.filing_scope_id, document_tags, email_accounts etc.) auf
# Schema-Ebene korrekt, aber zur Laufzeit wirkungslos.
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_conn, _):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# init_db() entfernt — Schema laeuft seit Phase-6/B-01 ausschliesslich ueber
# Alembic-Migrationen (siehe backend/migrate.py + backend/alembic/).
