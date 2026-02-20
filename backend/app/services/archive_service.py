import hashlib
import json
import logging
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, DocumentType, ReviewStatus, Tag, TaxCategory
from app.models.review_question import ReviewQuestion
from app.models.warranty_info import WarrantyInfo, WarrantyType
from app.services.analysis_service import AnalysisResult
from app.services.ocr_service import OcrResult
from app.services.search_service import index_document

logger = logging.getLogger("zettelwirtschaft.archive")


def _compute_file_hash(file_path: Path) -> str:
    """Berechnet SHA-256 Hash einer Datei."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _build_archive_path(
    archive_dir: str,
    document_type: str,
    document_date: date | None,
    stored_filename: str,
    scope_slug: str | None = None,
) -> Path:
    """Erstellt den Archiv-Pfad: archive/{scope_slug}/{jahr}/{monat}/{document_type}/filename."""
    if document_date:
        year = str(document_date.year)
        month = f"{document_date.month:02d}"
    else:
        now = datetime.now(timezone.utc)
        year = str(now.year)
        month = f"{now.month:02d}"

    base = Path(archive_dir)
    if scope_slug:
        base = base / scope_slug
    archive_path = base / year / month / document_type
    archive_path.mkdir(parents=True, exist_ok=True)
    return archive_path / stored_filename


def _parse_document_date(date_str: str | None) -> date | None:
    """Parst ein Datumsstring im Format YYYY-MM-DD."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _parse_document_type(type_str: str | None) -> DocumentType:
    """Parst den Dokumenttyp, Fallback auf SONSTIGES."""
    if not type_str:
        return DocumentType.SONSTIGES
    try:
        return DocumentType(type_str)
    except ValueError:
        return DocumentType.SONSTIGES


def _parse_tax_category(category_str: str | None) -> str | None:
    """Parst die Steuerkategorie. Bei Compound-Werten (z.B. 'A | B') wird der erste genommen."""
    if not category_str:
        return None
    # LLM liefert manchmal compound-Werte wie "Werbungskosten | Aussergewoehnliche_Belastungen"
    parts = [p.strip() for p in category_str.split("|")]
    for part in parts:
        try:
            return TaxCategory(part).value
        except ValueError:
            continue
    return None


async def _get_or_create_tags(
    tag_names: list[str],
    session: AsyncSession,
    auto_generated: bool = True,
) -> list[Tag]:
    """Holt oder erstellt Tags anhand der Namen."""
    tags = []
    for name in tag_names:
        name = name.strip().lower()
        if not name:
            continue

        result = await session.execute(select(Tag).where(Tag.name == name))
        tag = result.scalar_one_or_none()
        if not tag:
            tag = Tag(name=name, is_auto_generated=auto_generated)
            session.add(tag)
            await session.flush()
        tags.append(tag)
    return tags


def _match_filing_scope(
    analysis: AnalysisResult,
    filing_scopes: list[dict],
    ocr_text: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Bestimmt den Filing Scope. Returns (scope_id, scope_slug, new_scope_suggestion).

    Prioritaet:
    1. Keyword-Match im OCR-Text (zuverlaessiger als LLM)
    2. LLM-Zuweisung (wenn Konfidenz >= 0.7 und Scope existiert)
    3. LLM-Vorschlag fuer neuen Scope (wenn Name unbekannt)
    4. Default-Scope als Fallback
    """
    # 1. Keyword-Match im OCR-Text - hat Vorrang
    if ocr_text:
        text_lower = ocr_text.lower()
        best_scope = None
        best_hits = 0
        for scope in filing_scopes:
            keywords = scope.get("keywords", [])
            if not keywords:
                continue
            hits = sum(1 for kw in keywords if kw.lower() in text_lower)
            if hits > best_hits:
                best_hits = hits
                best_scope = scope
        if best_scope and best_hits > 0:
            logger.info(
                "Filing Scope '%s' via Keyword-Match (%d Treffer)",
                best_scope["name"], best_hits,
            )
            return best_scope["id"], best_scope["slug"], None

    # 2. LLM-Zuweisung (bekannter Scope)
    if analysis.filing_scope and analysis.filing_scope_confidence >= 0.7:
        for scope in filing_scopes:
            if scope["name"].lower() == analysis.filing_scope.lower():
                return scope["id"], scope["slug"], None

    # 3. LLM-Vorschlag fuer neuen Scope (Name unbekannt)
    new_scope_suggestion = None
    if analysis.filing_scope and analysis.filing_scope_confidence > 0:
        known_names = [s["name"].lower() for s in filing_scopes]
        if analysis.filing_scope.lower() not in known_names:
            new_scope_suggestion = analysis.filing_scope
            logger.info(
                "LLM schlaegt neuen Ablagebereich vor: '%s' (Konfidenz: %.0f%%)",
                new_scope_suggestion, analysis.filing_scope_confidence * 100,
            )

    # 4. Fallback auf Default-Scope
    for scope in filing_scopes:
        if scope.get("is_default"):
            return scope["id"], scope["slug"], new_scope_suggestion

    # 5. Erster Scope als Fallback
    if filing_scopes:
        return filing_scopes[0]["id"], filing_scopes[0]["slug"], new_scope_suggestion

    return None, None, new_scope_suggestion


async def check_duplicate(file_path: Path, session: AsyncSession) -> Document | None:
    """Prueft ob eine Datei bereits archiviert wurde (via SHA-256)."""
    file_hash = _compute_file_hash(file_path)
    result = await session.execute(
        select(Document).where(Document.file_hash == file_hash)
    )
    return result.scalar_one_or_none()


async def archive_document(
    file_path: Path,
    original_filename: str,
    stored_filename: str,
    file_type: str,
    file_size_bytes: int,
    ocr_result: OcrResult | None,
    analysis_result: AnalysisResult | None,
    settings: Settings,
    session: AsyncSession,
    thumbnail_path: str | None = None,
    filing_scopes: list[dict] | None = None,
) -> Document:
    """Archiviert ein Dokument: Datei verschieben, DB-Eintrag erstellen.

    Returns:
        Das erstellte Document-Objekt.

    Raises:
        ValueError: Bei Duplikat (gleicher SHA-256 Hash).
    """
    # Hash berechnen + Duplikatcheck
    file_hash = _compute_file_hash(file_path)
    existing = await session.execute(
        select(Document).where(Document.file_hash == file_hash)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Duplikat erkannt: Datei mit Hash {file_hash[:12]}... existiert bereits")

    # Analyse-Daten extrahieren
    analysis = analysis_result or AnalysisResult()
    doc_type = _parse_document_type(analysis.document_type)
    doc_date = _parse_document_date(analysis.document_date)
    tax_category = _parse_tax_category(analysis.tax_category)

    # Filing Scope bestimmen
    scope_id = None
    scope_slug = None
    new_scope_suggestion = None
    ocr_text = ocr_result.full_text if ocr_result else None
    if filing_scopes:
        scope_id, scope_slug, new_scope_suggestion = _match_filing_scope(analysis, filing_scopes, ocr_text=ocr_text)

    # Archiv-Pfad bestimmen und Datei verschieben
    archive_path = _build_archive_path(
        settings.ARCHIVE_DIR, doc_type.value, doc_date, stored_filename,
        scope_slug=scope_slug,
    )
    shutil.move(str(file_path), str(archive_path))
    logger.info("Datei archiviert: %s -> %s", file_path.name, archive_path)

    # Review-Status bestimmen
    review_status = ReviewStatus.OK
    if analysis.needs_review:
        review_status = ReviewStatus.NEEDS_REVIEW

    # Document erstellen
    document = Document(
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_path=str(archive_path),
        thumbnail_path=thumbnail_path,
        file_type=file_type,
        file_size_bytes=file_size_bytes,
        file_hash=file_hash,
        document_type=doc_type,
        title=analysis.title or original_filename,
        document_date=doc_date,
        amount=analysis.amount,
        currency=analysis.currency or "EUR",
        issuer=analysis.sender,
        recipient=analysis.recipient,
        reference_number=analysis.reference_number,
        summary=analysis.summary,
        ocr_text=ocr_result.full_text if ocr_result else "",
        ocr_confidence=ocr_result.average_confidence if ocr_result else 0.0,
        tax_relevant=analysis.tax_relevant,
        tax_year=analysis.tax_year,
        tax_category=tax_category,
        filing_scope_id=scope_id,
        status=DocumentStatus.ACTIVE,
        review_status=review_status,
        ai_confidence=analysis.confidence,
        scanned_at=datetime.now(timezone.utc),
    )
    session.add(document)
    await session.flush()

    # Tags erstellen (via junction table to avoid lazy-load issues)
    if analysis.tags:
        tags = await _get_or_create_tags(analysis.tags, session)
        for tag in tags:
            session.add(DocumentTag(document_id=document.id, tag_id=tag.id))

    # Garantie-Info erstellen
    warranty = analysis.warranty_info
    if warranty and isinstance(warranty, dict) and warranty.get("has_warranty"):
        purchase_date = _parse_document_date(warranty.get("purchase_date"))
        warranty_end = _parse_document_date(warranty.get("warranty_end_date"))
        if purchase_date and warranty_end:
            warranty_info = WarrantyInfo(
                document_id=document.id,
                product_name=warranty.get("product_name", "Unbekanntes Produkt"),
                purchase_date=purchase_date,
                warranty_end_date=warranty_end,
                warranty_type=WarrantyType.LEGAL,
                warranty_duration_months=warranty.get("warranty_duration_months", 24),
                retailer=warranty.get("store_name"),
            )
            session.add(warranty_info)

    # Review-Fragen erstellen
    if analysis.review_questions:
        for question_text in analysis.review_questions:
            question = ReviewQuestion(
                document_id=document.id,
                question=question_text,
            )
            session.add(question)

    # Review-Frage bei unsicherer Scope-Zuordnung oder neuem Scope-Vorschlag
    if filing_scopes and (new_scope_suggestion or analysis.filing_scope_confidence < 0.7):
        scope_names = [s["name"] for s in filing_scopes]
        if new_scope_suggestion:
            # Neuen Vorschlag als erste Option mit "NEU: " Prefix
            suggested = [f"NEU: {new_scope_suggestion}"] + scope_names
            scope_question = ReviewQuestion(
                document_id=document.id,
                question=f"Die KI schlaegt einen neuen Ablagebereich '{new_scope_suggestion}' vor. Soll dieser erstellt werden?",
                question_type="classification",
                field_affected="filing_scope",
                suggested_answers="|".join(suggested),
                explanation=f"Das Dokument passt zu keinem bestehenden Ablagebereich. Vorschlag: '{new_scope_suggestion}'",
                priority=8,
            )
        else:
            scope_question = ReviewQuestion(
                document_id=document.id,
                question="Zu welchem Ablagebereich gehoert dieses Dokument?",
                question_type="classification",
                field_affected="filing_scope",
                suggested_answers="|".join(scope_names),
                priority=5,
            )
        session.add(scope_question)
        if not analysis.needs_review:
            document.review_status = ReviewStatus.NEEDS_REVIEW

    # AuditLog
    audit = AuditLog(
        document_id=document.id,
        action=AuditAction.CREATED,
        details=json.dumps(
            {"document_type": doc_type.value, "ai_confidence": analysis.confidence},
            ensure_ascii=False,
        ),
    )
    session.add(audit)

    await session.flush()

    # FTS-Index aktualisieren
    try:
        tag_names = " ".join(analysis.tags) if analysis.tags else ""
        await index_document(
            session=session,
            doc_id=document.id,
            title=document.title,
            ocr_text=document.ocr_text,
            issuer=document.issuer,
            summary=document.summary,
            tags=tag_names,
        )
    except Exception:
        logger.warning("FTS-Indexierung fehlgeschlagen fuer %s", document.id, exc_info=True)

    logger.info(
        "Dokument archiviert: %s (Typ: %s, Konfidenz: %.0f%%)",
        document.id,
        doc_type.value,
        analysis.confidence * 100,
    )
    return document
