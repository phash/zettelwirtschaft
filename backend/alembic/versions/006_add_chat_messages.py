"""Fuegt chat_messages Tabelle hinzu.

Revision ID: 006_add_chat_messages
Revises: 005_add_filing_scopes
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "006_add_chat_messages"
down_revision = "005_add_filing_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("sources_json", sa.Text, nullable=True),
        sa.Column("filing_scope_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_table("chat_messages")
