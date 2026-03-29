"""Fix EmailAccount.filing_scope_id Spaltentyp: Integer -> String(36).

Revision ID: 010_fix_email_filing_scope_fk_type
Revises: 009_add_email_accounts
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "010_fix_email_filing_scope_fk_type"
down_revision = "009_add_email_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite unterstuetzt kein ALTER COLUMN, daher Tabelle neu erstellen
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.alter_column(
            "filing_scope_id",
            existing_type=sa.Integer(),
            type_=sa.String(36),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.alter_column(
            "filing_scope_id",
            existing_type=sa.String(36),
            type_=sa.Integer(),
            existing_nullable=True,
        )
