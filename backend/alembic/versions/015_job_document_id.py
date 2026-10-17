"""ProcessingJob -> Document-Verknuepfung + maschinenlesbarer Fehlercode.

- processing_jobs.document_id: FK auf documents(id) ON DELETE SET NULL + Index.
  Upload-/Dashboard-Ansicht verlinken damit auf das erzeugte Dokument (die
  Job-ID ist nicht die Dokument-ID — der "Anzeigen"-Link lief bisher ins 404).
- processing_jobs.error_code: z.B. "DUPLICATE" fuer bereits archivierte Dateien.

Der FK wird als benannter Tabellen-Constraint per Rebuild angelegt (statt
ADD COLUMN ... REFERENCES): nur so reflektiert SQLAlchemy das ON DELETE SET NULL
und spaetere batch_alter_table-Rebuilds uebernehmen es. Rebuild ohne
FK-Pruefung (alembic/env.py schaltet sie fuer alle Migrationen ab).

Revision ID: 015_job_document_id
Revises: 014_background_tasks
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa

revision = "015_job_document_id"
down_revision = "014_background_tasks"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "processing_jobs")

    # Idempotent: Fresh-DB-Pfad (create_all) kann die Spalten schon angelegt haben.
    if "document_id" not in columns:
        with op.batch_alter_table("processing_jobs", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("document_id", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                "fk_processing_jobs_document_id",
                "documents",
                ["document_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "error_code" not in columns:
        op.add_column("processing_jobs", sa.Column("error_code", sa.String(30), nullable=True))

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processing_jobs_document_id "
        "ON processing_jobs (document_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_processing_jobs_document_id")
    # Rebuild von processing_jobs (FK-Spalte) — alembic/env.py faehrt Migrationen
    # ohne FK-Pruefung, sonst loeste das DROP TABLE die ON-DELETE-Aktionen von
    # processed_emails aus.
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.drop_column("error_code")
        batch_op.drop_column("document_id")
