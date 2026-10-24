import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import UTCDateTime
from app.database import Base


class DocumentType(str, enum.Enum):
    RECHNUNG = "RECHNUNG"
    QUITTUNG = "QUITTUNG"
    KAUFVERTRAG = "KAUFVERTRAG"
    GARANTIESCHEIN = "GARANTIESCHEIN"
    VERSICHERUNGSPOLICE = "VERSICHERUNGSPOLICE"
    KONTOAUSZUG = "KONTOAUSZUG"
    LOHNABRECHNUNG = "LOHNABRECHNUNG"
    STEUERBESCHEID = "STEUERBESCHEID"
    MIETVERTRAG = "MIETVERTRAG"
    HANDWERKER_RECHNUNG = "HANDWERKER_RECHNUNG"
    ARZTRECHNUNG = "ARZTRECHNUNG"
    REZEPT = "REZEPT"
    AMTLICHES_SCHREIBEN = "AMTLICHES_SCHREIBEN"
    BEDIENUNGSANLEITUNG = "BEDIENUNGSANLEITUNG"
    SONSTIGES = "SONSTIGES"


class DocumentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class ReviewStatus(str, enum.Enum):
    OK = "OK"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REVIEWED = "REVIEWED"


class TaxCategory(str, enum.Enum):
    WERBUNGSKOSTEN = "Werbungskosten"
    SONDERAUSGABEN = "Sonderausgaben"
    AUSSERGEWOEHNLICHE_BELASTUNGEN = "Aussergewoehnliche_Belastungen"
    HANDWERKERLEISTUNGEN = "Handwerkerleistungen"
    HAUSHALTSNAHE_DIENSTLEISTUNGEN = "Haushaltsnahe_Dienstleistungen"
    VORSORGEAUFWENDUNGEN = "Vorsorgeaufwendungen"
    KEINE = "Keine"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Datei-Hash nur unter aktiven Dokumenten eindeutig: ein geloeschtes
        # Dokument (Soft-Delete) blockiert den erneuten Import derselben Datei
        # nicht. Der Partial Index dient zugleich dem Duplikat-Lookup.
        Index(
            "ux_documents_file_hash_active",
            "file_hash",
            unique=True,
            sqlite_where=text("status != 'DELETED'"),
        ),
        # Ein Teil eines aufgeteilten Stapels kann nicht doppelt aktiv
        # archiviert werden (Spezifikation 12).
        Index(
            "ux_documents_split_part_active",
            "split_batch_id",
            "split_part_index",
            unique=True,
            sqlite_where=text("status != 'DELETED' AND split_batch_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # KI-extrahierte Metadaten
    document_type: Mapped[str] = mapped_column(
        Enum(DocumentType, native_enum=False),
        nullable=False,
        default=DocumentType.SONSTIGES,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="Unbekanntes Dokument")
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # H-ARCH-2: Decimal statt float — vermeidet IEEE-754-Rundungsfehler bei
    # Aggregationen (Tax-Year-Summary, Sonderausgaben). DB-Storage ist
    # Numeric(12,2), SQLAlchemy mapped es jetzt nach Decimal statt float.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    issuer: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    recipient: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ocr_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Steuer
    tax_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    tax_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tax_category: Mapped[str | None] = mapped_column(
        Enum(TaxCategory, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )

    # Ablagebereich
    filing_scope_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("filing_scopes.id"), nullable=True, index=True
    )

    # Herkunft aus einem aufgeteilten Stapel (Spezifikation 12)
    split_batch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("split_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    split_part_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Seiten im Original, z.B. "3-11,13-44"
    source_pages: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Modell-Tag der zuletzt uebernommenen bzw. geprueften KI-Analyse
    analysis_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        Enum(DocumentStatus, native_enum=False),
        nullable=False,
        default=DocumentStatus.ACTIVE,
        index=True,
    )
    review_status: Mapped[str] = mapped_column(
        Enum(ReviewStatus, native_enum=False),
        nullable=False,
        default=ReviewStatus.OK,
        index=True,
    )
    ai_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Relationships
    tags: Mapped[list["Tag"]] = relationship(
        secondary="document_tags", back_populates="documents", lazy="selectin"
    )
    warranty_info: Mapped["WarrantyInfo | None"] = relationship(
        back_populates="document", uselist=False, lazy="selectin"
    )
    review_questions: Mapped[list["ReviewQuestion"]] = relationship(
        back_populates="document", lazy="selectin"
    )
    filing_scope: Mapped["FilingScope | None"] = relationship(lazy="selectin")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_auto_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    # Wird nie geladen (Tag-Zuweisung laeuft ueber DocumentTag). lazy="raise"
    # statt "noload" (in SQLAlchemy 2.1 deprecated): Zugriff wirft, statt still
    # eine leere Liste zu liefern.
    documents: Mapped[list["Document"]] = relationship(
        secondary="document_tags", back_populates="tags", lazy="raise"
    )


class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


# Forward references for relationships
from app.models.warranty_info import WarrantyInfo  # noqa: E402
from app.models.review_question import ReviewQuestion  # noqa: E402
from app.models.filing_scope import FilingScope  # noqa: E402
