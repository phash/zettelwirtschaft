"""Gemeinsame SQLAlchemy-Spaltentypen.

UTCDateTime: SQLite speichert ``DateTime(timezone=True)`` ohne Offset und
liefert beim Lesen *naive* datetimes zurueck. Pydantic serialisiert die dann
ohne Offset ("2026-10-17T10:00:00"), der Browser interpretiert das als Lokalzeit
und zeigt Zeiten 1-2 h verschoben an. Dieser Typ normalisiert beim Schreiben auf
UTC und haengt beim Lesen wieder ``timezone.utc`` an — API-Responses tragen
damit immer einen Offset ("...Z"). Kein Schema-Change noetig (impl bleibt
DateTime), daher fuer bestehende Spalten ohne Migration einsetzbar.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # Naive Werte gelten im ganzen Projekt als UTC (datetime.now(timezone.utc)
            # bzw. SQLite CURRENT_TIMESTAMP).
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
