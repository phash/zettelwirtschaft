"""add fts5 and saved_searches

Revision ID: 003_add_fts5_saved
Revises: 002_add_documents
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa


revision = "003_add_fts5_saved"
down_revision = "002_add_document_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SavedSearch table
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("query_params", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # FTS5 Virtual Table (standalone, not content-synced)
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            doc_id UNINDEXED,
            title,
            ocr_text,
            issuer,
            summary,
            tags,
            tokenize='unicode61 remove_diacritics 2'
        )
    """)

    # Populate index from existing data
    op.execute("""
        INSERT INTO documents_fts(doc_id, title, ocr_text, issuer, summary, tags)
        SELECT
            d.id,
            COALESCE(d.title, ''),
            COALESCE(d.ocr_text, ''),
            COALESCE(d.issuer, ''),
            COALESCE(d.summary, ''),
            COALESCE(
                (SELECT GROUP_CONCAT(t.name, ' ')
                 FROM document_tags dt JOIN tags t ON dt.tag_id = t.id
                 WHERE dt.document_id = d.id),
                ''
            )
        FROM documents d
        WHERE d.status != 'DELETED'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents_fts")
    op.drop_table("saved_searches")
