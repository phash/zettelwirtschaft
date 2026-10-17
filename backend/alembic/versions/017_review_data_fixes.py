"""Datenkorrekturen aus dem Review 1.6.0 (keine Schemaaenderung).

1. Ablagebereiche mit leerem Slug bekommen einen generierten Slug
   ``bereich-<hex>``. Ein leerer Slug zeigte als Scope-Ordner auf das
   Archiv-Wurzelverzeichnis (Loeschen des Bereichs loeschte frueher das ganze
   Archiv). Bestehende Dateien bleiben, wo sie sind (file_path ist absolut).
2. Ablagebereich-Korrekturen (correction_mappings, field='filing_scope')
   speicherten Scope-UUIDs, die als Few-Shot-Beispiele im LLM-Prompt nichts
   bedeuten. Sie werden auf Bereichs-Namen umgestellt (gleiche Paare werden
   zusammengefasst), nicht aufloesbare Eintraege (Bereich geloescht) entfernt.
3. Steuerrelevante Dokumente ohne Steuerjahr bekommen eines (Jahr des
   Belegdatums, sonst des Erfassungsdatums — gleiche Regel wie
   app.services.tax_year). Alle Steuer-Abfragen filtern auf tax_year; ohne
   Jahr tauchte ein manuell als steuerrelevant markierter Beleg nie im
   Steuerpaket auf.

Idempotent: ein erneuter Lauf findet nichts mehr zu tun.

Revision ID: 017_review_data_fixes
Revises: 016_schema_consistency
Create Date: 2026-10-17
"""

import re
import uuid
from datetime import date

from app.services.tax_year import derive_tax_year

from alembic import op
import sqlalchemy as sa

revision = "017_review_data_fixes"
down_revision = "016_schema_consistency"
branch_labels = None
depends_on = None

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_AUTO_APPLY_THRESHOLD = 3


def _fix_empty_slugs(bind) -> None:
    rows = bind.execute(
        sa.text("SELECT id FROM filing_scopes WHERE slug IS NULL OR TRIM(slug) = ''")
    ).all()
    for (scope_id,) in rows:
        bind.execute(
            sa.text("UPDATE filing_scopes SET slug = :slug WHERE id = :id"),
            {"slug": f"bereich-{uuid.uuid4().hex[:8]}", "id": scope_id},
        )


def _scope_corrections_to_names(bind) -> None:
    names = {row[0]: row[1] for row in bind.execute(sa.text("SELECT id, name FROM filing_scopes")).all()}
    rows = bind.execute(sa.text(
        "SELECT id, original_value, corrected_value, occurrence_count "
        "FROM correction_mappings WHERE field = 'filing_scope'"
    )).all()

    def resolve(value: str) -> str | None:
        if _UUID_RE.match(value or ""):
            return names.get(value)
        return value

    for row_id, original, corrected, count in rows:
        if not (_UUID_RE.match(original or "") or _UUID_RE.match(corrected or "")):
            continue
        new_original, new_corrected = resolve(original), resolve(corrected)
        if not new_original or not new_corrected or new_original == new_corrected:
            bind.execute(sa.text("DELETE FROM correction_mappings WHERE id = :id"), {"id": row_id})
            continue
        existing = bind.execute(
            sa.text(
                "SELECT id, occurrence_count FROM correction_mappings "
                "WHERE field = 'filing_scope' AND original_value = :o AND corrected_value = :c AND id != :id"
            ),
            {"o": new_original, "c": new_corrected, "id": row_id},
        ).first()
        if existing:
            total = existing[1] + count
            bind.execute(
                sa.text(
                    "UPDATE correction_mappings SET occurrence_count = :n, auto_apply = :auto WHERE id = :id"
                ),
                {"n": total, "auto": total >= _AUTO_APPLY_THRESHOLD, "id": existing[0]},
            )
            bind.execute(sa.text("DELETE FROM correction_mappings WHERE id = :id"), {"id": row_id})
        else:
            bind.execute(
                sa.text(
                    "UPDATE correction_mappings SET original_value = :o, corrected_value = :c WHERE id = :id"
                ),
                {"o": new_original, "c": new_corrected, "id": row_id},
            )


def _year_of(value) -> int | None:
    """Jahr aus einem SQLite-Datums-/Zeitstempelwert ('YYYY-MM-DD...')."""
    text = str(value or "")
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _backfill_tax_year(bind) -> None:
    rows = bind.execute(sa.text(
        "SELECT id, document_date, created_at FROM documents "
        "WHERE tax_relevant = 1 AND tax_year IS NULL"
    )).all()
    for doc_id, document_date, created_at in rows:
        doc_year, created_year = _year_of(document_date), _year_of(created_at)
        year = derive_tax_year(
            date(doc_year, 1, 1) if doc_year else None,
            date(created_year, 1, 1) if created_year else None,
        )
        bind.execute(
            sa.text("UPDATE documents SET tax_year = :year WHERE id = :id"),
            {"year": year, "id": doc_id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("filing_scopes"):
        _fix_empty_slugs(bind)
        if insp.has_table("correction_mappings"):
            _scope_corrections_to_names(bind)
    if insp.has_table("documents"):
        _backfill_tax_year(bind)


def downgrade() -> None:
    # Reine Datenkorrektur — die alten (kaputten) Werte werden nicht wiederhergestellt.
    pass
