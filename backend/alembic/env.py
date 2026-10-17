import asyncio
import logging
from collections import Counter
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base, configure_sqlite_engine
import app.models  # noqa: F401 - alle Modelle fuer Alembic registrieren

# Alembic Config
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
logger = logging.getLogger("alembic.env")

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


def _foreign_key_violations(connection) -> Counter:
    """PRAGMA foreign_key_check als Anzahl je (Kind-Tabelle, Eltern-Tabelle).

    Bewusst ohne rowid/fkid: batch_alter_table baut Tabellen per INSERT SELECT
    neu auf — Tabellen ohne INTEGER-PK bekommen dabei neue rowids, und die
    FK-Reihenfolge (fkid) kann sich aendern. Dieselbe Altlast saehe nach dem
    Rebuild sonst wie eine neue Verletzung aus und blockierte das Update.
    """
    if connection.dialect.name != "sqlite":
        return Counter()
    rows = connection.execute(text("PRAGMA foreign_key_check")).fetchall()
    return Counter((row[0], row[2]) for row in rows)


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    # FK-Pruefung ist waehrend der Migration aus (siehe run_async_migrations).
    # Danach explizit pruefen: NEUE Verletzungen brechen die Migration ab
    # (Rollback), Altlasten aus der Zeit vor K-2 (FKs waren bis v1.3 nie aktiv)
    # werden nur protokolliert — sonst kaeme so eine Installation nie mehr ueber
    # das Update hinaus.
    existing = _foreign_key_violations(connection)
    if existing:
        logger.warning(
            "%d bestehende FK-Verletzung(en) vor der Migration (table, parent): %s",
            existing.total(), dict(existing),
        )
    with context.begin_transaction():
        context.run_migrations()
        # Counter-Differenz: nur Paare, deren Anzahl gestiegen ist.
        new = _foreign_key_violations(connection) - existing
        if new:
            raise RuntimeError(
                f"Migration erzeugt {new.total()} FK-Verletzung(en) (table, parent): {dict(new)}"
            )


async def run_async_migrations() -> None:
    """Migrationen im 'online' Modus mit async Engine ausfuehren."""
    connectable = create_async_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,
    )

    # FK-Enforcement fuer Migrationen AUS (die App-Engine schaltet es zur
    # Laufzeit an, K-2). batch_alter_table baut SQLite-Tabellen per Kopie +
    # DROP + RENAME um; mit "PRAGMA foreign_keys=ON" wird der DROP einer
    # Eltern-Tabelle wie ein DELETE behandelt und loest die ON-DELETE-Aktionen
    # der Kinder aus — Migration 010 hat so alle processed_emails geloescht
    # (CASCADE) und processing_jobs.email_account_id genullt (SET NULL).
    # Die Konsistenz prueft do_run_migrations danach per foreign_key_check.
    configure_sqlite_engine(connectable.sync_engine, foreign_keys=False)

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
