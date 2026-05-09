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
    # Idempotent — der Migrationsstand kann je nach DB-Historie leicht abweichen,
    # die Indizes existieren ggf. bereits aus init_db oder einer manuellen Migration.
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_is_read ON notifications (is_read)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_processing_jobs_created_at ON processing_jobs (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_review_questions_is_answered ON review_questions (is_answered)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_processing_jobs_status ON processing_jobs (status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_processing_jobs_status")
    op.execute("DROP INDEX IF EXISTS ix_review_questions_is_answered")
    op.execute("DROP INDEX IF EXISTS ix_processing_jobs_created_at")
    op.execute("DROP INDEX IF EXISTS ix_notifications_is_read")
