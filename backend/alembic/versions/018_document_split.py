"""Mehrdokument-Scans aufteilen (Spezifikation 12).

- Neue Tabelle ``split_batches`` (Stapel mit Seitenmerkmalen, Vorschlag und
  bestaetigter Aufteilung). Bewusst OHNE FK auf processing_jobs/documents —
  nur diese beiden zeigen per ``split_batch_id`` auf den Stapel (kein Zyklus).
- ``processing_jobs``: split_batch_id (FK ON DELETE SET NULL), split_role,
  split_part_index, page_from, page_to, split_mode (NOT NULL, Default 'AUTO'),
  page_ocr + partieller Unique-Index ``ux_processing_jobs_split_part``.
- ``documents``: split_batch_id (FK ON DELETE SET NULL), split_part_index,
  source_pages + partieller Unique-Index ``ux_documents_split_part_active``.

Neue Enum-Werte (JobStatus AWAITING_SPLIT/SPLIT, JobSource SPLIT_REQUEST,
AuditAction SPLIT*, NotificationType SPLIT_NEEDED) brauchen keine DDL: die
Spalten sind VARCHAR ohne CHECK (native_enum=False), SQLite erzwingt die
VARCHAR-Laenge nicht.

Die FKs entstehen wie in 015 als benannte Tabellen-Constraints per Rebuild
(nur so reflektiert SQLAlchemy ON DELETE SET NULL); alembic/env.py faehrt alle
Migrationen ohne FK-Pruefung. Idempotent: der Fresh-DB-Pfad (create_all) legt
alles schon an.

Revision ID: 018_document_split
Revises: 017_review_data_fixes
Create Date: 2026-10-18
"""

from alembic import op
import sqlalchemy as sa

revision = "018_document_split"
down_revision = "017_review_data_fixes"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _create_split_batches() -> None:
    op.create_table(
        "split_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("origin", sa.String(6), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_job_id", sa.String(36), nullable=True),
        sa.Column("source_document_id", sa.String(36), nullable=True),
        sa.Column("email_account_id", sa.Integer(), nullable=True),
        sa.Column("job_source", sa.String(20), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages", sa.Text(), nullable=True),
        sa.Column("proposal", sa.Text(), nullable=True),
        sa.Column("final_parts", sa.Text(), nullable=True),
        sa.Column("detector_version", sa.String(20), nullable=True),
        sa.Column("llm_used", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("detection_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_split_batches_status ON split_batches (status)",
    "CREATE INDEX IF NOT EXISTS ix_split_batches_status_created ON split_batches (status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_split_batches_source_job_id ON split_batches (source_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_split_batches_source_document_id ON split_batches (source_document_id)",
    "CREATE INDEX IF NOT EXISTS ix_split_batches_file_hash ON split_batches (file_hash)",
    "CREATE INDEX IF NOT EXISTS ix_processing_jobs_split_batch_id ON processing_jobs (split_batch_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_processing_jobs_split_part "
    "ON processing_jobs (split_batch_id, split_part_index) WHERE split_role = 'PART'",
    "CREATE INDEX IF NOT EXISTS ix_documents_split_batch_id ON documents (split_batch_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_split_part_active "
    "ON documents (split_batch_id, split_part_index) "
    "WHERE status != 'DELETED' AND split_batch_id IS NOT NULL",
    # Der documents-Rebuild uebernimmt partielle Indizes nicht zuverlaessig
    # (Reflexion ohne WHERE) — Partial Unique Index aus 016 sicherstellen.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_file_hash_active "
    "ON documents (file_hash) WHERE status != 'DELETED'",
)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("split_batches"):
        _create_split_batches()

    job_columns = _columns(bind, "processing_jobs")
    if "split_batch_id" not in job_columns:
        with op.batch_alter_table("processing_jobs", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("split_batch_id", sa.String(36), nullable=True))
            batch_op.add_column(sa.Column("split_role", sa.String(10), nullable=True))
            batch_op.add_column(sa.Column("split_part_index", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("page_from", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("page_to", sa.Integer(), nullable=True))
            batch_op.add_column(
                sa.Column("split_mode", sa.String(10), nullable=False, server_default="AUTO")
            )
            batch_op.add_column(sa.Column("page_ocr", sa.Text(), nullable=True))
            batch_op.create_foreign_key(
                "fk_processing_jobs_split_batch_id",
                "split_batches",
                ["split_batch_id"],
                ["id"],
                ondelete="SET NULL",
            )

    doc_columns = _columns(bind, "documents")
    if "split_batch_id" not in doc_columns:
        op.execute("DROP INDEX IF EXISTS ux_documents_file_hash_active")
        with op.batch_alter_table("documents", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("split_batch_id", sa.String(36), nullable=True))
            batch_op.add_column(sa.Column("split_part_index", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("source_pages", sa.String(40), nullable=True))
            batch_op.create_foreign_key(
                "fk_documents_split_batch_id",
                "split_batches",
                ["split_batch_id"],
                ["id"],
                ondelete="SET NULL",
            )

    for statement in _INDEXES:
        op.execute(statement)


def downgrade() -> None:
    for name in (
        "ux_documents_split_part_active",
        "ix_documents_split_batch_id",
        "ux_processing_jobs_split_part",
        "ix_processing_jobs_split_batch_id",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")

    op.execute("DROP INDEX IF EXISTS ux_documents_file_hash_active")
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.drop_column("source_pages")
        batch_op.drop_column("split_part_index")
        batch_op.drop_column("split_batch_id")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_file_hash_active "
        "ON documents (file_hash) WHERE status != 'DELETED'"
    )

    with op.batch_alter_table("processing_jobs", recreate="always") as batch_op:
        for column in (
            "page_ocr", "split_mode", "page_to", "page_from",
            "split_part_index", "split_role", "split_batch_id",
        ):
            batch_op.drop_column(column)

    op.drop_table("split_batches")
