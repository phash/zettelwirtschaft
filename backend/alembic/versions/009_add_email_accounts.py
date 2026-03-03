"""E-Mail-Konten und verarbeitete E-Mails.

Revision ID: 009_add_email_accounts
Revises: 008_add_warranty_reminder_flags
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa

revision = "009_add_email_accounts"
down_revision = "008_add_warranty_reminder_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("imap_host", sa.String(500), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("username", sa.String(500), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("folder_inbox", sa.String(500), nullable=False, server_default="'INBOX'"),
        sa.Column(
            "folder_processed",
            sa.String(500),
            nullable=False,
            server_default="'Zettelwirtschaft/Verarbeitet'",
        ),
        sa.Column("schedule_type", sa.String(10), nullable=False, server_default="'MANUAL'"),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "filing_scope_id",
            sa.Integer(),
            sa.ForeignKey("filing_scopes.id", ondelete="SET NULL"),
            nullable=True,
        ),
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

    op.create_table(
        "processed_emails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "email_account_id",
            sa.Integer(),
            sa.ForeignKey("email_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("message_id", sa.String(1000), nullable=False),
        sa.Column("subject", sa.String(1000), nullable=True),
        sa.Column("sender", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("relevance_reason", sa.String(500), nullable=True),
        sa.Column(
            "processing_job_id",
            sa.String(36),
            sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email_account_id", "message_id", name="uq_account_message"),
    )

    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.add_column(sa.Column("email_account_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.drop_column("email_account_id")
    op.drop_table("processed_emails")
    op.drop_table("email_accounts")
