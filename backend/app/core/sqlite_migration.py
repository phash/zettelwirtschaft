"""Hilfen fuer Alembic-Migrationen, die SQLite-Tabellen neu aufbauen muessen.

SQLite kann Constraints (UNIQUE, FOREIGN KEY, NOT NULL) nicht per ALTER TABLE
aendern; ``batch_alter_table`` baut die Tabelle deshalb neu auf (CREATE tmp,
INSERT SELECT, DROP, RENAME). Die FK-Pruefung ist dabei bereits aus:
``alembic/env.py`` faehrt ALLE Migrationen mit ``PRAGMA foreign_keys=OFF``
(sonst loeste ``DROP TABLE documents`` die ``ON DELETE CASCADE``/``SET NULL``-
Aktionen der Kind-Tabellen aus) und prueft danach per
``PRAGMA foreign_key_check``, dass keine NEUEN Verletzungen entstanden sind —
das offizielle SQLite-Verfahren ("12 steps",
https://sqlite.org/lang_altertable.html). Die Helfer hier dienen nur dazu,
Rebuilds idempotent zu machen (nur umbauen, wenn das Schema es noch braucht).
"""

import sqlalchemy as sa


def unique_constraint_columns(bind, table: str) -> list[list[str]]:
    """Spaltenlisten aller UNIQUE-Constraints (nicht CREATE UNIQUE INDEX) einer Tabelle."""
    result = []
    for row in bind.exec_driver_sql(f"PRAGMA index_list({table})").fetchall():
        # row: (seq, name, unique, origin, partial); origin 'u' = UNIQUE-Constraint
        name, origin = row[1], row[3]
        if origin != "u":
            continue
        cols = [r[2] for r in bind.exec_driver_sql(f"PRAGMA index_info('{name}')").fetchall()]
        result.append(cols)
    return result


def has_foreign_key(bind, table: str, column: str, referred_table: str) -> bool:
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if fk.get("constrained_columns") == [column] and fk.get("referred_table") == referred_table:
            return True
    return False


def is_nullable(bind, table: str, column: str) -> bool:
    for col in sa.inspect(bind).get_columns(table):
        if col["name"] == column:
            return bool(col["nullable"])
    raise KeyError(f"{table}.{column} nicht gefunden")
