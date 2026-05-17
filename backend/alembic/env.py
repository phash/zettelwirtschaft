import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import event, pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base
import app.models  # noqa: F401 - alle Modelle fuer Alembic registrieren

# Alembic Config
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()


def run_migrations_offline() -> None:
    """Migrationen im 'offline' Modus ausfuehren."""
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Migrationen im 'online' Modus mit async Engine ausfuehren."""
    connectable = create_async_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,
    )

    # K-2 (Re-Review): SQLite ignoriert FK-Constraints (z.B. ondelete=SET NULL)
    # solange "PRAGMA foreign_keys=ON" nicht pro Verbindung gesetzt wird.
    # batch_alter_table-Migrationen brauchen FK-Enforcement, damit die Tabellen-
    # Rebuilds bestehende Refs konsistent halten.
    if settings.DATABASE_URL.startswith("sqlite"):
        @event.listens_for(connectable.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_conn, _):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Migrationen im 'online' Modus ausfuehren."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
