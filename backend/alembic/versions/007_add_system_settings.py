"""Fuegt system_settings Tabelle fuer UI-konfigurierbare Einstellungen hinzu.

Revision ID: 007_add_system_settings
Revises: 006_add_chat_messages
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa

revision = "007_add_system_settings"
down_revision = "006_add_chat_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.String, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
