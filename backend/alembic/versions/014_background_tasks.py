"""Background-Tasks fuer langlaufende Vorgaenge (Re-Analyse, E-Mail-Abruf, Backup).

Tabelle background_tasks + Partial Unique Index (hoechstens ein aktiver
Vorgang pro kind+key).

Revision ID: 014_background_tasks
Revises: 013_fk_indexes_consistency
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa

revision = "014_background_tasks"
down_revision = "013_fk_indexes_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: Fresh-DB-Pfad (create_all) kann die Tabelle schon angelegt haben.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("background_tasks"):
        op.create_table(
            "background_tasks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("kind", sa.String(50), nullable=False),
            sa.Column("key", sa.String(200), nullable=True),
            sa.Column(
                "status",
                sa.Enum(
                    "PENDING", "RUNNING", "SUCCEEDED", "FAILED",
                    name="taskstatus", native_enum=False,
                ),
                nullable=False,
            ),
            sa.Column("progress_percent", sa.Integer(), nullable=True),
            sa.Column("progress_text", sa.String(500), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_background_tasks_kind ON background_tasks (kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_background_tasks_status ON background_tasks (status)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_background_tasks_active_kind_key "
        "ON background_tasks (kind, key) "
        "WHERE status IN ('PENDING', 'RUNNING') AND key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_background_tasks_active_kind_key")
    op.execute("DROP INDEX IF EXISTS ix_background_tasks_status")
    op.execute("DROP INDEX IF EXISTS ix_background_tasks_kind")
    op.drop_table("background_tasks")
