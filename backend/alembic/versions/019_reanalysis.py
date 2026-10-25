"""Neu-Analyse mit Aenderungsvorschlag (Issue #39).

- ``documents.analysis_model``: Modell-Tag der zuletzt uebernommenen/geprueften
  Analyse (nullable; Altbestand bleibt NULL).
- ``reanalysis_proposals``: ein Vorschlag je Dokument (FK ON DELETE CASCADE, unique).
- ``reanalysis_runs`` + Partial Unique Index ``ux_reanalysis_runs_active``
  (hoechstens ein aktiver Lauf).
- ``reanalysis_run_items`` (FK auf den Lauf ON DELETE CASCADE; ``document_id``
  bewusst ohne FK).

Neues Enum ``NotificationType.REANALYSIS_DONE`` braucht keine DDL (VARCHAR ohne CHECK).
Idempotent: der Fresh-DB-Pfad (create_all) legt alles schon an. Eine nullable
Spalte ohne FK wird per ALTER TABLE ADD COLUMN ergaenzt (kein Tabellen-Rebuild,
daher kein Risiko fuer abhaengige Zeilen/Indizes).

Revision ID: 019_reanalysis
Revises: 018_document_split
Create Date: 2026-10-24
"""

from alembic import op
import sqlalchemy as sa

revision = "019_reanalysis"
down_revision = "018_document_split"
branch_labels = None
depends_on = None


_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_reanalysis_runs_active "
    "ON reanalysis_runs ((1)) WHERE status IN ('RUNNING', 'PAUSED', 'WAITING_AI')",
    "CREATE INDEX IF NOT EXISTS ix_reanalysis_run_items_run_status_pos "
    "ON reanalysis_run_items (run_id, status, position)",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "analysis_model" not in {c["name"] for c in insp.get_columns("documents")}:
        op.add_column("documents", sa.Column("analysis_model", sa.String(200), nullable=True))

    if not insp.has_table("reanalysis_proposals"):
        op.create_table(
            "reanalysis_proposals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "document_id",
                sa.String(36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("with_ocr", sa.Boolean(), nullable=False),
            sa.Column("analysis", sa.JSON(), nullable=False),
            sa.Column("ocr_text", sa.Text(), nullable=True),
            sa.Column("ocr_confidence", sa.Float(), nullable=True),
            sa.Column("ocr_note", sa.String(40), nullable=True),
            sa.Column("run_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if not insp.has_table("reanalysis_runs"):
        op.create_table(
            "reanalysis_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("with_ocr", sa.Boolean(), nullable=False),
            sa.Column("total", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
        )

    if not insp.has_table("reanalysis_run_items"):
        op.create_table(
            "reanalysis_run_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey("reanalysis_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("document_id", sa.String(36), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
        )

    for statement in _INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reanalysis_run_items_run_status_pos")
    op.execute("DROP INDEX IF EXISTS ux_reanalysis_runs_active")
    op.drop_table("reanalysis_run_items")
    op.drop_table("reanalysis_runs")
    op.drop_table("reanalysis_proposals")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("analysis_model")
