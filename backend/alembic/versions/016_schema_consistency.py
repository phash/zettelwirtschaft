"""Schema-Konsistenz zwischen Frisch-Installation (create_all) und Migrationskette.

- documents.file_hash: UNIQUE nur noch unter aktiven Dokumenten (Partial
  Unique Index ``ux_documents_file_hash_active``, WHERE status != 'DELETED').
  Ein geloeschtes Dokument blockiert den erneuten Import derselben Datei nicht
  mehr. Kette: Spalten-UNIQUE aus 002 -> Tabellen-Rebuild; create_all-DBs:
  Unique-Index ``ix_documents_file_hash`` -> DROP INDEX.
- Indizes aus 006/011 (notifications.is_read, processing_jobs.created_at,
  review_questions.is_answered, chat_messages.created_at): fehlten bei
  Frisch-Installationen (Models deklarierten sie nicht).
- processing_jobs.email_account_id: FK auf email_accounts (ON DELETE SET NULL)
  fehlte in migrierten DBs (009 legte nur die Spalte an). Verwaiste Werte
  werden vorher auf NULL gesetzt.
- review_questions.priority: in migrierten DBs nullable (004) -> NULL auf 0,
  dann NOT NULL wie im Model.

Tabellen-Rebuilds laufen mit abgeschalteter FK-Pruefung (alembic/env.py) —
alle Schritte sind idempotent.

Revision ID: 016_schema_consistency
Revises: 015_job_document_id
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa

from app.core.sqlite_migration import (
    has_foreign_key,
    is_nullable,
    unique_constraint_columns,
)

revision = "016_schema_consistency"
down_revision = "015_job_document_id"
branch_labels = None
depends_on = None

_UQ_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    bind = op.get_bind()

    # Indizes, die create_all-DBs fehlten (Models jetzt mit index=True).
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_is_read ON notifications (is_read)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_processing_jobs_created_at ON processing_jobs (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_review_questions_is_answered ON review_questions (is_answered)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_created_at ON chat_messages (created_at)")

    # create_all-DBs: unique=True + index=True erzeugte einen Unique-Index.
    op.execute("DROP INDEX IF EXISTS ix_documents_file_hash")

    needs_documents_rebuild = ["file_hash"] in unique_constraint_columns(bind, "documents")
    needs_jobs_fk = not has_foreign_key(bind, "processing_jobs", "email_account_id", "email_accounts")
    needs_priority_not_null = is_nullable(bind, "review_questions", "priority")

    if needs_jobs_fk:
        op.execute(
            "UPDATE processing_jobs SET email_account_id = NULL "
            "WHERE email_account_id IS NOT NULL "
            "AND email_account_id NOT IN (SELECT id FROM email_accounts)"
        )
    if needs_priority_not_null:
        op.execute("UPDATE review_questions SET priority = 0 WHERE priority IS NULL")

    # Tabellen-Rebuilds: alembic/env.py faehrt alle Migrationen mit
    # PRAGMA foreign_keys=OFF (sonst loeste DROP TABLE documents die ON-DELETE-
    # Aktionen der Kind-Tabellen aus) und prueft danach foreign_key_check.
    if needs_documents_rebuild:
        with op.batch_alter_table(
            "documents", recreate="always", naming_convention=_UQ_NAMING,
        ) as batch_op:
            batch_op.drop_constraint("uq_documents_file_hash", type_="unique")
    if needs_jobs_fk:
        with op.batch_alter_table("processing_jobs", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_processing_jobs_email_account_id",
                "email_accounts",
                ["email_account_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if needs_priority_not_null:
        with op.batch_alter_table("review_questions", recreate="always") as batch_op:
            batch_op.alter_column(
                "priority",
                existing_type=sa.Integer(),
                nullable=False,
                existing_server_default="0",
            )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_file_hash_active "
        "ON documents (file_hash) WHERE status != 'DELETED'"
    )


def downgrade() -> None:
    # Nur der fachlich relevante Teil: Hash wieder global eindeutig. Scheitert,
    # wenn inzwischen eine geloeschte und eine aktive Kopie derselben Datei
    # existieren — das ist gewollt (keine stillen Datenverluste).
    op.execute("DROP INDEX IF EXISTS ux_documents_file_hash_active")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_file_hash ON documents (file_hash)")
