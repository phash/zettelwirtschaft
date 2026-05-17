"""Heartbeat-Spalte fuer Stuck-Job-Recovery (B5).

Revision ID: 012_add_processing_started_at
Revises: 011_add_performance_indexes
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "012_add_processing_started_at"
down_revision = "011_add_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: bei manueller Migration koennte die Spalte schon da sein.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns("processing_jobs")]
    if "processing_started_at" not in cols:
        op.add_column(
            "processing_jobs",
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    # SQLite-Drop-Column via batch_alter_table.
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.drop_column("processing_started_at")
