"""Datenmigration 017_review_data_fixes: leere Slugs, UUID-Scope-Korrekturen, Steuerjahr."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

_MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "017_review_data_fixes.py"
_SPEC = importlib.util.spec_from_file_location("migration_017_review_data_fixes", _MIGRATION)
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)

PRIVAT = "11111111-1111-4111-8111-111111111111"
PRAXIS = "22222222-2222-4222-8222-222222222222"
GONE = "33333333-3333-4333-8333-333333333333"


def _setup(conn) -> None:
    conn.execute(sa.text("CREATE TABLE filing_scopes (id TEXT PRIMARY KEY, name TEXT, slug TEXT)"))
    conn.execute(sa.text(
        "CREATE TABLE correction_mappings (id TEXT PRIMARY KEY, field TEXT, original_value TEXT, "
        "corrected_value TEXT, occurrence_count INTEGER, auto_apply BOOLEAN)"
    ))
    conn.execute(sa.text(
        "INSERT INTO filing_scopes VALUES (:a, 'Privat', 'privat'), (:b, 'Praxis', ''), ('x', '€', NULL)"
    ), {"a": PRIVAT, "b": PRAXIS})
    conn.execute(sa.text(
        "INSERT INTO correction_mappings VALUES "
        "('m1', 'filing_scope', :privat, :praxis, 2, 0),"
        "('m2', 'filing_scope', 'Privat', 'Praxis', 1, 0),"
        "('m3', 'filing_scope', :gone, :praxis, 4, 1),"
        "('m4', 'issuer', 'Amazon EU', 'Amazon', 3, 1)"
    ), {"privat": PRIVAT, "praxis": PRAXIS, "gone": GONE})


def test_fixes_slugs_and_scope_corrections():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _setup(conn)
        migration._fix_empty_slugs(conn)
        migration._scope_corrections_to_names(conn)

        slugs = dict(conn.execute(sa.text("SELECT name, slug FROM filing_scopes")).all())
        assert slugs["Privat"] == "privat"
        assert slugs["Praxis"].startswith("bereich-")
        assert slugs["€"].startswith("bereich-")

        rows = conn.execute(sa.text(
            "SELECT id, field, original_value, corrected_value, occurrence_count, auto_apply "
            "FROM correction_mappings ORDER BY id"
        )).all()
        # m1 (UUIDs) wurde mit m2 (Namen) zusammengefasst, m3 (Bereich weg) entfernt
        assert [tuple(r) for r in rows] == [
            ("m2", "filing_scope", "Privat", "Praxis", 3, 1),
            ("m4", "issuer", "Amazon EU", "Amazon", 3, 1),
        ]

        # Idempotent
        before = conn.execute(sa.text("SELECT * FROM filing_scopes ORDER BY id")).all()
        migration._fix_empty_slugs(conn)
        migration._scope_corrections_to_names(conn)
        assert conn.execute(sa.text("SELECT * FROM filing_scopes ORDER BY id")).all() == before
        assert len(conn.execute(sa.text("SELECT * FROM correction_mappings")).all()) == 2


def test_backfills_tax_year_for_tax_relevant_documents():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, tax_relevant BOOLEAN, tax_year INTEGER, "
            "document_date DATE, created_at DATETIME)"
        ))
        conn.execute(sa.text(
            "INSERT INTO documents VALUES "
            "('d1', 1, NULL, '2024-03-05', '2025-01-02 10:00:00'),"  # Belegdatum zaehlt
            "('d2', 1, NULL, NULL, '2023-11-30 08:00:00.000000'),"   # sonst Erfassung
            "('d3', 1, 2021, '2024-03-05', '2025-01-02 10:00:00'),"  # explizit: bleibt
            "('d4', 0, NULL, '2024-03-05', '2025-01-02 10:00:00')"   # nicht relevant
        ))
        migration._backfill_tax_year(conn)
        years = dict(conn.execute(sa.text("SELECT id, tax_year FROM documents")).all())
        assert years == {"d1": 2024, "d2": 2023, "d3": 2021, "d4": None}
