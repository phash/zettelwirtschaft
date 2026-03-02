"""Fuegt separate Reminder-Flags fuer 90/30/0-Tage Garantie-Erinnerungen hinzu.

Revision ID: 008_add_warranty_reminder_flags
Revises: 007_add_system_settings
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa

revision = "008_add_warranty_reminder_flags"
down_revision = "007_add_system_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("warranty_info", sa.Column("reminder_90d_sent", sa.Boolean, nullable=False, server_default="0"))
    op.add_column("warranty_info", sa.Column("reminder_30d_sent", sa.Boolean, nullable=False, server_default="0"))
    op.add_column("warranty_info", sa.Column("reminder_0d_sent", sa.Boolean, nullable=False, server_default="0"))

    # Bestehende Garantien mit reminder_sent=True: Setze alle 3 Flags auf True
    # (damit keine doppelten Erinnerungen erstellt werden)
    op.execute("UPDATE warranty_info SET reminder_90d_sent = 1, reminder_30d_sent = 1, reminder_0d_sent = 1 WHERE reminder_sent = 1")


def downgrade() -> None:
    op.drop_column("warranty_info", "reminder_0d_sent")
    op.drop_column("warranty_info", "reminder_30d_sent")
    op.drop_column("warranty_info", "reminder_90d_sent")
