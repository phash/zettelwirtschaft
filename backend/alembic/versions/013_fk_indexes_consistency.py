"""FK + Indexes Konsistenz (H-ARCH-1,3,4).

- chat_messages.filing_scope_id: FK auf filing_scopes(id) ON DELETE SET NULL + Index.
- email_accounts.filing_scope_id: Index (FK existiert seit 010).
- processing_jobs.email_account_id: Index.
- processed_emails.processing_job_id: Index.

Revision ID: 013_fk_indexes_consistency
Revises: 012_add_processing_started_at
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "013_fk_indexes_consistency"
down_revision = "012_add_processing_started_at"
branch_labels = None
depends_on = None


def _has_index(bind, table: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    return any(idx["name"] == index_name for idx in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()

    # H-ARCH-1: chat_messages.filing_scope_id bekommt einen FK + Index.
    # SQLite kann FK nicht via ALTER TABLE ADD CONSTRAINT — batch_alter_table
    # rebaut die Tabelle.
    insp = sa.inspect(bind)
    fk_names = [fk["name"] for fk in insp.get_foreign_keys("chat_messages") if fk.get("constrained_columns") == ["filing_scope_id"]]
    if not fk_names:
        with op.batch_alter_table("chat_messages") as batch_op:
            batch_op.create_foreign_key(
                "fk_chat_messages_filing_scope_id",
                "filing_scopes",
                ["filing_scope_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if not _has_index(bind, "chat_messages", "ix_chat_messages_filing_scope_id"):
        op.create_index(
            "ix_chat_messages_filing_scope_id",
            "chat_messages",
            ["filing_scope_id"],
        )

    # H-ARCH-3: email_accounts.filing_scope_id Index.
    if not _has_index(bind, "email_accounts", "ix_email_accounts_filing_scope_id"):
        op.create_index(
            "ix_email_accounts_filing_scope_id",
            "email_accounts",
            ["filing_scope_id"],
        )

    # H-ARCH-4: processing_jobs.email_account_id Index.
    if not _has_index(bind, "processing_jobs", "ix_processing_jobs_email_account_id"):
        op.create_index(
            "ix_processing_jobs_email_account_id",
            "processing_jobs",
            ["email_account_id"],
        )

    # H-ARCH-4: processed_emails.processing_job_id Index.
    if not _has_index(bind, "processed_emails", "ix_processed_emails_processing_job_id"):
        op.create_index(
            "ix_processed_emails_processing_job_id",
            "processed_emails",
            ["processing_job_id"],
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_processed_emails_processing_job_id")
    op.execute("DROP INDEX IF EXISTS ix_processing_jobs_email_account_id")
    op.execute("DROP INDEX IF EXISTS ix_email_accounts_filing_scope_id")
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_filing_scope_id")
    # FK-Downgrade fuer chat_messages: batch_alter_table rebuilt die Tabelle.
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.drop_constraint("fk_chat_messages_filing_scope_id", type_="foreignkey")
