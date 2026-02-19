"""Fuegt Document, Tag, DocumentTag, WarrantyInfo, ReviewQuestion, AuditLog Tabellen hinzu.

Revision ID: 002_add_document_models
Revises: 001_add_ocr_analysis
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa

revision = "002_add_document_models"
down_revision = "001_add_ocr_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Documents
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("stored_filename", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("thumbnail_path", sa.String(1000), nullable=True),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("file_size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("document_type", sa.String(50), nullable=False, server_default="SONSTIGES"),
        sa.Column("title", sa.String(500), nullable=False, server_default="Unbekanntes Dokument"),
        sa.Column("document_date", sa.Date, nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("issuer", sa.String(500), nullable=True),
        sa.Column("recipient", sa.String(500), nullable=True),
        sa.Column("reference_number", sa.String(200), nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("ocr_text", sa.Text, nullable=False, server_default=""),
        sa.Column("ocr_confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("tax_relevant", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("tax_year", sa.Integer, nullable=True),
        sa.Column("tax_category", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="OK"),
        sa.Column("ai_confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_document_date", "documents", ["document_date"])
    op.create_index("ix_documents_tax_relevant", "documents", ["tax_relevant"])
    op.create_index("ix_documents_tax_year", "documents", ["tax_year"])
    op.create_index("ix_documents_issuer", "documents", ["issuer"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_review_status", "documents", ["review_status"])

    # Tags
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("is_auto_generated", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # DocumentTag
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )

    # WarrantyInfo
    op.create_table(
        "warranty_info",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("product_name", sa.String(500), nullable=False),
        sa.Column("product_category", sa.String(200), nullable=True),
        sa.Column("purchase_date", sa.Date, nullable=False),
        sa.Column("warranty_end_date", sa.Date, nullable=False),
        sa.Column("warranty_type", sa.String(20), nullable=False, server_default="LEGAL"),
        sa.Column("warranty_duration_months", sa.Integer, nullable=False, server_default="24"),
        sa.Column("retailer", sa.String(500), nullable=True),
        sa.Column("reminder_sent", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_warranty_info_warranty_end_date", "warranty_info", ["warranty_end_date"])

    # ReviewQuestion
    op.create_table(
        "review_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("field_affected", sa.String(100), nullable=True),
        sa.Column("is_answered", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_questions_document_id", "review_questions", ["document_id"])

    # AuditLog
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_document_id", "audit_log", ["document_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("review_questions")
    op.drop_table("warranty_info")
    op.drop_table("document_tags")
    op.drop_table("tags")
    op.drop_table("documents")
