from collections.abc import AsyncGenerator

from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
)


# Wie lange eine Verbindung auf eine fremde Schreibsperre wartet, bevor SQLite
# "database is locked" meldet. Kurze, ueberlappende Schreib-Transaktionen
# (Worker-Heartbeat, UI-Speichern, Hintergrund-Vorgaenge) warten so aufeinander,
# statt sofort mit HTTP 500 abzubrechen. Bewusst kein WAL-Modus: die DB wird per
# sqlite3-Backup-API bzw. Datei-Kopie gesichert/wiederhergestellt (Restore-Skript
# raeumt -wal/-shm nur auf, spielt sie aber nicht ein).
SQLITE_BUSY_TIMEOUT_MS = 20_000


def configure_sqlite_engine(sync_engine: Engine, *, foreign_keys: bool = True) -> None:
    """Setzt die Verbindungs-PRAGMAs fuer jede neue SQLite-Verbindung.

    Gemeinsamer Helfer fuer App-Engine, Test-Engine und Alembic (``env.py``):

    - ``busy_timeout`` (siehe ``SQLITE_BUSY_TIMEOUT_MS``).
    - ``foreign_keys`` — K-2 (Re-Review): SQLite ignoriert ondelete=SET NULL/
      CASCADE per Default, bis "PRAGMA foreign_keys=ON" pro Verbindung gesetzt
      wird. Ohne das waeren die FK-Constraints (chat_messages, documents.
      filing_scope_id, document_tags, email_accounts etc.) zur Laufzeit
      wirkungslos. Alembic schaltet sie bewusst AUS (``foreign_keys=False``):
      batch_alter_table baut Tabellen per DROP + RENAME um, und ein DROP der
      Eltern-Tabelle wuerde bei aktiven FKs die ON-DELETE-Aktionen der Kinder
      ausloesen (Migration 010 hat so alle processed_emails geloescht).
    """
    if sync_engine.dialect.name != "sqlite":
        return

    @event.listens_for(sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        finally:
            cursor.close()


configure_sqlite_engine(engine.sync_engine)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dependency fuer Code, der eigene Sessions ausserhalb des Requests braucht
    (Hintergrund-Vorgaenge via task_service). In Tests ueberschreibbar."""
    return async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Request-Session mit Commit am Ende.

    IMMER als ``Depends(get_db, scope="function")`` einbinden: mit dem Default-
    Scope "request" fuehrt FastAPI (>= 0.12x) den Teil nach ``yield`` erst NACH
    dem Senden der Response aus — ein fehlschlagender Commit (locked, FK)
    wuerde dann still zurueckgerollt, waehrend der Client bereits 200 bekam.
    tests/test_db_dependency_scope.py prueft das fuer alle Routen.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# init_db() entfernt — Schema laeuft seit Phase-6/B-01 ausschliesslich ueber
# Alembic-Migrationen (siehe backend/migrate.py + backend/alembic/).
