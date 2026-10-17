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

# Default scope ID
PRIVAT_ID = str(uuid.uuid4())


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    # Idempotent: SQLite-DDL ist fuer Alembic nicht transaktional — scheitert
    # ein spaeterer Schritt, bleibt filing_scopes stehen und ein erneuter Lauf
    # muss trotzdem durchkommen.
    bind = op.get_bind()

    # FilingScopes Tabelle
    if not _has_table(bind, "filing_scopes"):
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
    op.execute("CREATE INDEX IF NOT EXISTS ix_filing_scopes_slug ON filing_scopes (slug)")

    # Default-Eintrag (nur wenn noch keiner existiert)
    scope_id = bind.execute(sa.text("SELECT id FROM filing_scopes WHERE is_default = 1 LIMIT 1")).scalar()
    if scope_id is None:
        scope_id = PRIVAT_ID
        op.execute(
            sa.text(
                "INSERT INTO filing_scopes (id, name, slug, description, keywords, is_default, color) "
                "VALUES (:id, :name, :slug, :desc, :keywords, :is_default, :color)"
            ).bindparams(
                id=scope_id,
                name="Privat",
                slug="privat",
                desc="Privater Haushalt",
                keywords="[]",
                is_default=True,
                color="#3B82F6",
            )
        )

    # filing_scope_id Spalte + FK auf documents. SQLite kann Constraints nicht
    # per ALTER TABLE anlegen -> batch_alter_table baut die Tabelle neu
    # (op.create_foreign_key ausserhalb von batch wirft NotImplementedError).
    if not _has_column(bind, "documents", "filing_scope_id"):
        with op.batch_alter_table("documents") as batch_op:
            batch_op.add_column(sa.Column("filing_scope_id", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                "fk_documents_filing_scope_id",
                "filing_scopes",
                ["filing_scope_id"],
                ["id"],
            )
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_filing_scope_id ON documents (filing_scope_id)")

    # Bestehende Dokumente auf Default-Scope setzen
    op.execute(
        sa.text("UPDATE documents SET filing_scope_id = :scope_id WHERE filing_scope_id IS NULL").bindparams(
            scope_id=scope_id,
        )
    )


def downgrade() -> None:
    op.drop_index("ix_documents_filing_scope_id", table_name="documents")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_filing_scope_id", type_="foreignkey")
        batch_op.drop_column("filing_scope_id")
    op.drop_index("ix_filing_scopes_slug", table_name="filing_scopes")
    op.drop_table("filing_scopes")
