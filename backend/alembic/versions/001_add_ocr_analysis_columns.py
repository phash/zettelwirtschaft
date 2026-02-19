"""Fuegt OCR- und Analyse-Spalten zu processing_jobs hinzu.

Revision ID: 001_add_ocr_analysis
Revises:
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa

revision = "001_add_ocr_analysis"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("processing_jobs", sa.Column("ocr_text", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("ocr_confidence", sa.Float(), nullable=True))
    op.add_column("processing_jobs", sa.Column("analysis_result", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("processing_jobs", "analysis_result")
    op.drop_column("processing_jobs", "ocr_confidence")
    op.drop_column("processing_jobs", "ocr_text")
