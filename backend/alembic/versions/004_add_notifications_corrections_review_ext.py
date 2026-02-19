"""Fuegt Notification, CorrectionMapping Tabellen hinzu und erweitert ReviewQuestion.

Revision ID: 004_notifications_corrections
Revises: 003_add_fts5_saved
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa

revision = "004_notifications_corrections"
down_revision = "003_add_fts5_saved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # CorrectionMapping
    op.create_table(
        "correction_mappings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("field", sa.String(100), nullable=False),
        sa.Column("original_value", sa.Text, nullable=False),
        sa.Column("corrected_value", sa.Text, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("auto_apply", sa.Boolean, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_correction_mappings_field", "correction_mappings", ["field"])

    # ReviewQuestion Erweiterungen
    op.add_column("review_questions", sa.Column("question_type", sa.String(20), nullable=True))
    op.add_column("review_questions", sa.Column("explanation", sa.Text, nullable=True))
    op.add_column("review_questions", sa.Column("suggested_answers", sa.Text, nullable=True))
    op.add_column("review_questions", sa.Column("priority", sa.Integer, nullable=True, server_default="0"))


def downgrade() -> None:
    op.drop_column("review_questions", "priority")
    op.drop_column("review_questions", "suggested_answers")
    op.drop_column("review_questions", "explanation")
    op.drop_column("review_questions", "question_type")
    op.drop_table("correction_mappings")
    op.drop_table("notifications")
