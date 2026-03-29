"""Indizes fuer haeufig gefilterte Spalten.

Revision ID: 011_add_performance_indexes
Revises: 010_fix_email_filing_scope_fk_type
Create Date: 2026-03-29
"""

from alembic import op

revision = "011_add_performance_indexes"
down_revision = "010_fix_email_filing_scope_fk_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    op.create_index("ix_processing_jobs_created_at", "processing_jobs", ["created_at"])
    op.create_index("ix_review_questions_is_answered", "review_questions", ["is_answered"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_status")
    op.drop_index("ix_review_questions_is_answered")
    op.drop_index("ix_processing_jobs_created_at")
    op.drop_index("ix_notifications_is_read")
