"""Fuegt FilingScope-Tabelle hinzu und filing_scope_id auf documents.

Revision ID: 005_add_filing_scopes
Revises: 004_notifications_corrections
Create Date: 2026-02-19
"""

import uuid

from alembic import op
import sqlalchemy as sa

revision = "005_add_filing_scopes"
down_revision = "004_notifications_corrections"
branch_labels = None
depends_on = None

# Default scope IDs
PRIVAT_ID = str(uuid.uuid4())
PRAXIS_ID = str(uuid.uuid4())


def upgrade() -> None:
    # FilingScopes Tabelle
    op.create_table(
        "filing_scopes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column("slug", sa.String(200), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("keywords", sa.Text, nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, default=False),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_filing_scopes_slug", "filing_scopes", ["slug"])

    # Default-Eintraege
    op.execute(
        sa.text(
            "INSERT INTO filing_scopes (id, name, slug, description, keywords, is_default, color) "
            "VALUES (:id, :name, :slug, :desc, :keywords, :is_default, :color)"
        ).bindparams(
            id=PRIVAT_ID,
            name="Privat",
            slug="privat",
            desc="Privater Haushalt",
            keywords="[]",
            is_default=True,
            color="#3B82F6",
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO filing_scopes (id, name, slug, description, keywords, is_default, color) "
            "VALUES (:id, :name, :slug, :desc, :keywords, :is_default, :color)"
        ).bindparams(
            id=PRAXIS_ID,
            name="Praxis Dr. Klotz-Roedig",
            slug="praxis-dr-klotz-roedig",
            desc="Arztpraxis Dr. Klotz-Roedig",
            keywords='["KBV", "Kassenaerztliche", "Praxis", "Dr. Klotz", "BFS health finance", "KRONES BKK", "Arztpraxis"]',
            is_default=False,
            color="#10B981",
        )
    )

    # filing_scope_id Spalte auf documents
    op.add_column("documents", sa.Column("filing_scope_id", sa.String(36), nullable=True))
    op.create_index("ix_documents_filing_scope_id", "documents", ["filing_scope_id"])
    op.create_foreign_key(
        "fk_documents_filing_scope_id",
        "documents",
        "filing_scopes",
        ["filing_scope_id"],
        ["id"],
    )

    # Bestehende Dokumente auf Default-Scope setzen
    op.execute(
        sa.text("UPDATE documents SET filing_scope_id = :scope_id").bindparams(
            scope_id=PRIVAT_ID,
        )
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_filing_scope_id", "documents", type_="foreignkey")
    op.drop_index("ix_documents_filing_scope_id", table_name="documents")
    op.drop_column("documents", "filing_scope_id")
    op.drop_index("ix_filing_scopes_slug", table_name="filing_scopes")
    op.drop_table("filing_scopes")
