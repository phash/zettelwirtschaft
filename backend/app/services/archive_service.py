import asyncio
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.date_utils import add_months, parse_date_lenient
from app.core.normalize import DEFAULT_CURRENCY, normalize_currency, normalize_tax_category
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, DocumentType, ReviewStatus, Tag
from app.models.review_question import ReviewQuestion
from app.models.warranty_info import WarrantyInfo, WarrantyType
from app.services.analysis_service import AnalysisResult
from app.services.ocr_service import OcrResult
from app.services.search_service import reindex_document_fts
from app.services.tax_year import ensure_tax_year

if TYPE_CHECKING:
    from app.services.network_folder_service import FolderConfig

logger = logging.getLogger("zettelwirtschaft.archive")

_DEFAULT_PRODUCT_NAME = "Unbekanntes Produkt"
_DEFAULT_WARRANTY_MONTHS = 24  # gesetzliche Gewaehrleistung
_MAX_NAME_LEN = 500  # WarrantyInfo.product_name / retailer


class DuplicateDocumentError(ValueError):
    """Die Datei (gleicher SHA-256) ist bereits als aktives Dokument archiviert.

    Geloeschte Dokumente (Soft-Delete) zaehlen nicht: eine erneut hochgeladene
    Datei wird dann als neues Dokument angelegt.
    """

    def __init__(self, existing_document_id: str, existing_title: str | None):
        self.existing_document_id = existing_document_id
        self.existing_title = existing_title
        title = existing_title or "ohne Titel"
        super().__init__(f"Diese Datei ist bereits archiviert („{title}“).")


@dataclass(frozen=True)
class SplitProvenance:
    """Herkunft eines Teildokuments aus einem aufgeteilten Stapel."""

    batch_id: str
    part_index: int
    part_count: int
    source_pages: str  # z.B. "3-11,13-44"
    source_filename: str
    # Manuelle Tags des Originaldokuments (nur "Dokument aufteilen")
    inherited_tags: tuple[str, ...] = ()

    def audit_text(self) -> str:
        pages = self.source_pages.replace("-", "–")
        return f"Teil {self.part_index}/{self.part_count}, Seiten {pages} aus {self.source_filename}"


class ArchiveDataError(Exception):
    """Deterministischer Datenfehler beim Speichern (z.B. Constraint-Verletzung
    durch unerwartete Analysedaten). Ein erneuter Versuch fuehrt zum selben
    Ergebnis — der Worker markiert den Job deshalb sofort als FAILED."""


def coerce_decimal_amount(value) -> Decimal | None:
    """M1: float/str -> Decimal(2 Nachkommastellen) am Schreib-Rand von
    Document.amount (Numeric(12,2)).

    AnalysisResult.amount bleibt float (es wird als JSON in
    ProcessingJob.analysis_result serialisiert). Erst beim Persistieren in die
    Decimal-Spalte wird sauber quantisiert — sonst landet ein IEEE-754-float im
    In-Memory-Objekt und untergraebt die Decimal-Pipeline (H-ARCH-2)."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


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
    """Erstellt den Archiv-Pfad: archive/{scope_slug}/{jahr}/{monat}/{document_type}/filename.

    Reine Pfadberechnung — das Verzeichnis legt erst _copy_file an, damit bei
    einem Fehler vor dem Kopieren keine leeren Ordner entstehen.
    """
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
    return base / year / month / document_type / stored_filename


def _copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(dest))


def remove_created_files(paths: list[Path]) -> None:
    """Entfernt die von archive_document angelegten Archiv-Kopien, wenn die
    Transaktion danach scheitert — sonst bleiben verwaiste Dateien ohne
    DB-Eintrag zurueck."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Verwaiste Archivdatei konnte nicht entfernt werden: %s", path, exc_info=True)


def _parse_document_type(type_str: str | None) -> DocumentType:
    """Parst den Dokumenttyp, Fallback auf SONSTIGES."""
    if not type_str:
        return DocumentType.SONSTIGES
    try:
        return DocumentType(type_str)
    except ValueError:
        return DocumentType.SONSTIGES


def _parse_tax_category(category_str: str | None) -> str | None:
    """Steuerkategorie aus dem LLM-Wert (erster gueltiger Teil bei 'A | B').
    Delegiert an app.core.normalize (Single Source fuer alle Pfade)."""
    return normalize_tax_category(category_str)


async def _get_or_create_tags(
    tag_names: list[str],
    session: AsyncSession,
    auto_generated: bool = True,
) -> list[Tag]:
    """Holt oder erstellt Tags anhand der Namen (normalisiert, ohne Duplikate).

    Das LLM liefert gelegentlich Varianten wie ["Strom", "strom "] — ohne
    Deduplizierung entstanden doppelte DocumentTag-Primaerschluessel und die
    Archivierung brach ab.
    """
    tags: list[Tag] = []
    seen: set[str] = set()
    for raw_name in tag_names:
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)

        result = await session.execute(select(Tag).where(Tag.name == name))
        tag = result.scalar_one_or_none()
        if not tag:
            tag = Tag(name=name, is_auto_generated=auto_generated)
            session.add(tag)
            await session.flush()
        tags.append(tag)
    return tags


# Ab dieser LLM-Konfidenz gilt eine Scope-Zuordnung als sicher (CLAUDE.md:
# Keyword-Match > LLM (>= 0.7) > Default).
SCOPE_CONFIDENCE_THRESHOLD = 0.7


class ScopeMatch(NamedTuple):
    scope_id: str | None
    scope_slug: str | None
    new_scope_suggestion: str | None
    # "forced" (E-Mail-Konto), "keyword", "llm", "default" oder None (keine Scopes)
    source: str | None


@lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern:
    """Kompiliert ein Scope-Keyword zu einem Suchmuster (gecacht).

    Kurze Keywords (<= 3 Zeichen) und Abkuerzungen in Grossbuchstaben matchen
    nur als ganzes Wort — sonst traf 'AG' in 'Vertrag'. Laengere Keywords
    bleiben Teilstring-Treffer, damit 'Praxis' auch 'Zahnarztpraxis' findet.
    """
    escaped = re.escape(keyword)
    if len(keyword) <= 3 or (keyword.isupper() and len(keyword) > 1):
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _count_keyword_hits(keywords: list, text: str) -> int:
    hits = 0
    for kw in keywords or []:
        if not isinstance(kw, str):
            continue
        kw = kw.strip()
        # Leere/Whitespace-Keywords (Altbestand) wuerden sonst jedes Dokument treffen.
        if not kw:
            continue
        if _keyword_pattern(kw).search(text):
            hits += 1
    return hits


def _match_filing_scope(
    analysis: AnalysisResult,
    filing_scopes: list[dict],
    ocr_text: str | None = None,
    forced_scope_id: str | None = None,
) -> ScopeMatch:
    """Bestimmt den Filing Scope.

    Prioritaet:
    0. Erzwungener Scope (Ablagebereich des E-Mail-Kontos), solange er noch existiert
    1. Keyword-Match im OCR-Text (zuverlaessiger als LLM)
    2. LLM-Zuweisung (wenn Konfidenz >= 0.7 und Scope existiert)
    3. LLM-Vorschlag fuer neuen Scope (wenn Name unbekannt)
    4. Default-Scope als Fallback
    """
    # 0. Vom E-Mail-Konto vorgegebener Ablagebereich
    if forced_scope_id:
        for scope in filing_scopes:
            if scope["id"] == forced_scope_id:
                return ScopeMatch(scope["id"], scope["slug"], None, "forced")
        logger.info(
            "Ablagebereich %s des E-Mail-Kontos existiert nicht mehr — normale Zuordnung",
            forced_scope_id,
        )

    # 1. Keyword-Match im OCR-Text - hat Vorrang
    if ocr_text:
        best_scope = None
        best_hits = 0
        for scope in filing_scopes:
            hits = _count_keyword_hits(scope.get("keywords", []), ocr_text)
            if hits > best_hits:
                best_hits = hits
                best_scope = scope
        if best_scope and best_hits > 0:
            logger.info(
                "Filing Scope '%s' via Keyword-Match (%d Treffer)",
                best_scope["name"], best_hits,
            )
            return ScopeMatch(best_scope["id"], best_scope["slug"], None, "keyword")

    # 2. LLM-Zuweisung (bekannter Scope)
    if analysis.filing_scope and analysis.filing_scope_confidence >= SCOPE_CONFIDENCE_THRESHOLD:
        for scope in filing_scopes:
            if scope["name"].lower() == analysis.filing_scope.lower():
                return ScopeMatch(scope["id"], scope["slug"], None, "llm")

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
            return ScopeMatch(scope["id"], scope["slug"], new_scope_suggestion, "default")

    # 5. Erster Scope als Fallback
    if filing_scopes:
        return ScopeMatch(filing_scopes[0]["id"], filing_scopes[0]["slug"], new_scope_suggestion, "default")

    return ScopeMatch(None, None, new_scope_suggestion, None)


async def find_active_duplicate(file_hash: str, session: AsyncSession) -> Document | None:
    """Aktives (nicht geloeschtes) Dokument mit diesem Datei-Hash."""
    result = await session.execute(
        select(Document).where(
            Document.file_hash == file_hash,
            Document.status != DocumentStatus.DELETED,
        )
    )
    return result.scalars().first()


async def file_sha256(file_path: Path) -> str:
    """SHA-256 einer Datei (blockierendes Lesen in einem Thread)."""
    return await asyncio.to_thread(_compute_file_hash, file_path)


async def _find_active_split_part(split_part: SplitProvenance, session: AsyncSession) -> Document | None:
    result = await session.execute(
        select(Document).where(
            Document.split_batch_id == split_part.batch_id,
            Document.split_part_index == split_part.part_index,
            Document.status != DocumentStatus.DELETED,
        )
    )
    return result.scalars().first()


def _truncate(value, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_len] if text else None


def _parse_months(value) -> int | None:
    try:
        months = int(value)
    except (TypeError, ValueError):
        return None
    return months if 0 < months <= 600 else None


def _parse_warranty_type(value) -> WarrantyType:
    if isinstance(value, str):
        try:
            return WarrantyType(value.strip().upper())
        except ValueError:
            pass
    return WarrantyType.LEGAL


def _build_warranty_info(warranty, document_id: str, document_date: date | None) -> WarrantyInfo | None:
    """Erstellt WarrantyInfo aus den LLM-Garantiedaten.

    - Kaufdatum faellt auf das Dokumentdatum zurueck.
    - Fehlt das Enddatum (oder liegt es nicht nach dem Kaufdatum), wird es aus
      Kaufdatum + Laufzeit berechnet (Default: 24 Monate Gewaehrleistung).
    - LLM-null-Werte fuer Produkt/Haendler/Laufzeit fuehren nicht mehr zu
      NOT-NULL-Fehlern.
    """
    if not isinstance(warranty, dict) or not warranty.get("has_warranty"):
        return None

    purchase_date = parse_date_lenient(warranty.get("purchase_date")) or document_date
    if purchase_date is None:
        logger.info("Garantie ohne Kauf-/Dokumentdatum fuer %s — wird nicht angelegt", document_id)
        return None

    months = _parse_months(warranty.get("warranty_duration_months"))
    end_date = parse_date_lenient(warranty.get("warranty_end_date"))
    if end_date is None or end_date <= purchase_date:
        end_date = add_months(purchase_date, months or _DEFAULT_WARRANTY_MONTHS)

    return WarrantyInfo(
        document_id=document_id,
        product_name=_truncate(warranty.get("product_name"), _MAX_NAME_LEN) or _DEFAULT_PRODUCT_NAME,
        purchase_date=purchase_date,
        warranty_end_date=end_date,
        warranty_type=_parse_warranty_type(warranty.get("warranty_type")),
        warranty_duration_months=months or _DEFAULT_WARRANTY_MONTHS,
        retailer=_truncate(warranty.get("store_name"), _MAX_NAME_LEN),
    )


def _build_review_question(document_id: str, entry) -> ReviewQuestion | None:
    """ReviewQuestion aus einem Analyse-Eintrag: Freitext (str) oder strukturiert
    (dict mit question/question_type/field_affected/suggested_answers/
    explanation/priority). Strukturierte Fragen kann das Review-System
    automatisch auf das betroffene Feld anwenden."""
    if isinstance(entry, str):
        text = entry.strip()
        return ReviewQuestion(document_id=document_id, question=text) if text else None
    if not isinstance(entry, dict):
        return None

    text = str(entry.get("question") or "").strip()
    if not text:
        return None
    suggested = entry.get("suggested_answers")
    if isinstance(suggested, (list, tuple)):
        suggested = "|".join(str(s) for s in suggested if s is not None and str(s).strip())
    try:
        priority = int(entry.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    return ReviewQuestion(
        document_id=document_id,
        question=text,
        question_type=_truncate(entry.get("question_type"), 20),
        field_affected=_truncate(entry.get("field_affected"), 100),
        suggested_answers=suggested or None,
        explanation=entry.get("explanation") or None,
        priority=priority,
    )


def build_scope_question(
    document_id: str,
    scope: ScopeMatch,
    analysis: AnalysisResult,
    filing_scopes: list[dict] | None,
) -> ReviewQuestion | None:
    """Rueckfrage zum Ablagebereich — gemeinsam fuer Archivierung und Re-Analyse.

    Gefragt wird bei einem neuen Bereichs-Vorschlag oder bei unsicherer
    LLM-Zuordnung. M-05 (Re-Review): nur wenn das LLM tatsaechlich einen Bereich
    vorgeschlagen hat (``filing_scope=None`` heisst Konfidenz 0.0, sonst bekaeme
    jedes Default-Dokument eine Frage). Hat ein Keyword-Match oder das
    E-Mail-Konto den Bereich entschieden, ist die LLM-Konfidenz irrelevant.

    Returns:
        Die (noch nicht hinzugefuegte) Frage oder ``None``.
    """
    if not filing_scopes:
        return None
    scope_decided = scope.source in ("keyword", "forced")
    lowconf = bool(
        not scope_decided
        and analysis.filing_scope
        and analysis.filing_scope_confidence < SCOPE_CONFIDENCE_THRESHOLD
    )
    suggestion = scope.new_scope_suggestion
    if not (suggestion or lowconf):
        return None
    scope_names = [s["name"] for s in filing_scopes]
    if suggestion:
        # Neuen Vorschlag als erste Option mit "NEU: " Prefix
        return ReviewQuestion(
            document_id=document_id,
            question=f"Die KI schlägt einen neuen Ablagebereich '{suggestion}' vor. Soll dieser erstellt werden?",
            question_type="classification",
            field_affected="filing_scope",
            suggested_answers="|".join([f"NEU: {suggestion}"] + scope_names),
            explanation=f"Das Dokument passt zu keinem bestehenden Ablagebereich. Vorschlag: '{suggestion}'",
            priority=8,
        )
    return ReviewQuestion(
        document_id=document_id,
        question="Zu welchem Ablagebereich gehört dieses Dokument?",
        question_type="classification",
        field_affected="filing_scope",
        suggested_answers="|".join(scope_names),
        priority=5,
    )


@dataclass(frozen=True)
class ExportRequest:
    """Export-Kopie eines archivierten Dokuments — ausgefuehrt NACH dem Commit."""

    archive_path: Path
    document_type: str
    document_date: date | None
    stored_filename: str
    scope_slug: str | None
    config: "FolderConfig"


async def export_copy(request: ExportRequest, settings: Settings) -> Path | None:
    """Kopiert ein archiviertes Dokument in den Export-Ordner (best effort).

    R2: laeuft nach dem Commit — Verbindungsaufbau zu einer (evtl.
    ausgeschalteten) NAS und die Kopie halten so keine SQLite-Schreibsperre.
    Netzwerk-Export: erst die eigene Verbindung (``prepare_export``), dann
    die Kopie samt Aufraeumen unter einer Lease (NET-SEC-03) — ein
    Einstellungswechsel trennt erst nach dem Ende der Kopie, und ohne
    Verbindung wird der UNC-Pfad nie angefasst. Lokaler Export: keine
    Verknuepfung auf eine Freigabe (NET-SEC-07). Fehler werden geloggt und im
    Status gemeldet; das Dokument bleibt archiviert.
    """
    from app.core import network_share
    from app.services import network_folder_service as nfs

    config = request.config
    if not config.path:
        return None
    leased = nfs.uses_own_connection(config)
    if config.network and not await asyncio.to_thread(nfs.prepare_export, config, settings):
        return None

    def _copy() -> Path:
        lease = network_share.connections.open_lease(nfs.EXPORT) if leased else network_share.NO_LEASE
        with lease:
            base = network_share.connections.access_path(nfs.EXPORT, config.path) if leased else config.path
            if not config.network:
                link = network_share.find_remote_link(base)
                if link:
                    raise OSError(0, network_share.remote_link_message(link))
            dest = _build_archive_path(
                base, request.document_type, request.document_date, request.stored_filename,
                scope_slug=request.scope_slug,
            )
            try:
                _copy_file(request.archive_path, dest)
            except BaseException:
                remove_created_files([dest])
                raise
            return dest

    try:
        dest = await asyncio.to_thread(_copy)
    except Exception as exc:
        logger.warning("Export-Kopie fehlgeschlagen fuer %s", request.stored_filename, exc_info=True)
        if config.network:
            reason = str(exc) if isinstance(exc, network_share.ShareError) else (
                f"Netzwerkordner: Kopie fehlgeschlagen ({type(exc).__name__})."
            )
            nfs.status.update(nfs.EXPORT, state="getrennt", reason=reason)
        return None
    logger.info("Export-Kopie erstellt: %s", dest)
    return dest


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
    forced_scope_id: str | None = None,
    created_files: list[Path] | None = None,
    split_part: SplitProvenance | None = None,
    export_requests: list[ExportRequest] | None = None,
) -> Document:
    """Archiviert ein Dokument: DB-Eintrag erstellen, Datei ins Archiv kopieren.

    Die Datei wird erst kopiert, nachdem alle DB-Eintraege erfolgreich geflusht
    sind. Die angelegten Kopien werden an ``created_files`` angehaengt, damit
    der Aufrufer sie entfernen kann, falls sein Commit scheitert. Eine
    Export-Kopie wird nur geplant (``export_requests``) — der Aufrufer fuehrt
    sie NACH seinem Commit mit ``export_copy`` aus (R2).

    Args:
        forced_scope_id: Ablagebereich, der Keyword-/LLM-Zuordnung uebersteuert
            (Ablagebereich des E-Mail-Kontos bzw. des aufgeteilten Originals).
        split_part: Herkunft eines Teils aus einem aufgeteilten Stapel —
            setzt split_batch_id/split_part_index/source_pages, uebernimmt die
            manuellen Tags des Originals und schreibt AuditAction.SPLIT_CREATED.

    Returns:
        Das erstellte Document-Objekt.

    Raises:
        DuplicateDocumentError: Aktives Dokument mit gleichem SHA-256 existiert.
        ArchiveDataError: Daten liessen sich nicht speichern (nicht wiederholbar).
    """
    # Hash berechnen + Duplikatcheck (geloeschte Dokumente zaehlen nicht)
    file_hash = await file_sha256(file_path)
    existing = await find_active_duplicate(file_hash, session)
    if existing is not None:
        raise DuplicateDocumentError(existing.id, existing.title)

    # Analyse-Daten extrahieren
    analysis = analysis_result or AnalysisResult()
    doc_type = _parse_document_type(analysis.document_type)
    doc_date = parse_date_lenient(analysis.document_date)
    unparsed_date = (
        analysis.document_date.strip()
        if doc_date is None and isinstance(analysis.document_date, str) and analysis.document_date.strip()
        else None
    )
    if unparsed_date:
        logger.warning("Dokumentdatum nicht lesbar: %r", unparsed_date)
    tax_category = _parse_tax_category(analysis.tax_category)

    # Filing Scope bestimmen
    ocr_text = ocr_result.full_text if ocr_result else None
    scope = ScopeMatch(None, None, None, None)
    if filing_scopes:
        scope = _match_filing_scope(
            analysis, filing_scopes, ocr_text=ocr_text, forced_scope_id=forced_scope_id,
        )

    # Archiv-Pfad bestimmen (Datei wird erst nach dem DB-Flush kopiert)
    archive_path = _build_archive_path(
        settings.ARCHIVE_DIR, doc_type.value, doc_date, stored_filename,
        scope_slug=scope.scope_slug,
    )

    # Export-Verzeichnis vorab ermitteln (benoetigt DB-Session)
    from app.services import network_folder_service

    try:
        export_config = await network_folder_service.load_folder_config(
            session, network_folder_service.EXPORT, settings,
        )
    except Exception:
        export_config = network_folder_service.FolderConfig(settings.EXPORT_DIR, "", "")
    export_dir = export_config.path

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
        amount=coerce_decimal_amount(analysis.amount),
        currency=normalize_currency(analysis.currency) or DEFAULT_CURRENCY,
        issuer=analysis.sender,
        recipient=analysis.recipient,
        reference_number=analysis.reference_number,
        summary=analysis.summary,
        ocr_text=ocr_result.full_text if ocr_result else "",
        ocr_confidence=ocr_result.average_confidence if ocr_result else 0.0,
        tax_relevant=analysis.tax_relevant,
        tax_year=analysis.tax_year,
        tax_category=tax_category,
        filing_scope_id=scope.scope_id,
        # Modell nur merken, wenn die KI wirklich geantwortet hat
        analysis_model=(
            settings.OLLAMA_MODEL
            if analysis_result is not None and analysis.llm_error is None
            else None
        ),
        status=DocumentStatus.ACTIVE,
        review_status=review_status,
        ai_confidence=analysis.confidence,
        scanned_at=datetime.now(timezone.utc),
        split_batch_id=split_part.batch_id if split_part else None,
        split_part_index=split_part.part_index if split_part else None,
        source_pages=split_part.source_pages if split_part else None,
    )
    # Steuerrelevant ohne Jahr (LLM liefert keins) -> Jahr ableiten, sonst taucht
    # der Beleg in keiner Steuer-Abfrage auf (die alle auf tax_year filtern).
    ensure_tax_year(document)
    # Insert im Savepoint: eine Constraint-Verletzung (paralleles Duplikat) rollt
    # nur diesen Insert zurueck — die vom Aufrufer gehaltenen Objekte (Job) bleiben
    # gueltig, statt durch ein Session-Rollback zu expiren.
    try:
        async with session.begin_nested():
            session.add(document)
            await session.flush()
    except IntegrityError as exc:
        existing = await find_active_duplicate(file_hash, session)
        if existing is None and split_part is not None:
            # ux_documents_split_part_active: dieser Teil ist schon archiviert
            existing = await _find_active_split_part(split_part, session)
        if existing is not None:
            raise DuplicateDocumentError(existing.id, existing.title) from exc
        raise ArchiveDataError(f"Dokument konnte nicht gespeichert werden: {exc.orig}") from exc

    # Tags erstellen (via junction table to avoid lazy-load issues). Manuelle
    # Tags des aufgeteilten Originals kommen dazu (gleicher Name -> ein Tag).
    tag_names = list(analysis.tags or [])
    if split_part is not None:
        tag_names += list(split_part.inherited_tags)
    if tag_names:
        tags = await _get_or_create_tags(tag_names, session)
        for tag in tags:
            session.add(DocumentTag(document_id=document.id, tag_id=tag.id))

    # Garantie-Info erstellen
    warranty_info = _build_warranty_info(analysis.warranty_info, document.id, doc_date)
    if warranty_info is not None:
        session.add(warranty_info)

    # Review-Fragen erstellen (Freitext oder strukturiert)
    for entry in analysis.review_questions or []:
        question = _build_review_question(document.id, entry)
        if question is not None:
            session.add(question)

    # Nicht lesbares Datum: nachfragen statt still unter dem aktuellen Monat ablegen
    if unparsed_date:
        session.add(ReviewQuestion(
            document_id=document.id,
            question="Welches Datum hat dieses Dokument?",
            question_type="extraction",
            field_affected="document_date",
            explanation=f"Die KI hat das Datum „{unparsed_date}“ erkannt, es konnte aber nicht gelesen werden.",
            priority=7,
        ))
        document.review_status = ReviewStatus.NEEDS_REVIEW

    # Review-Frage bei unsicherer Scope-Zuordnung oder neuem Scope-Vorschlag.
    scope_question = build_scope_question(document.id, scope, analysis, filing_scopes)
    if scope_question is not None:
        session.add(scope_question)
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
    if split_part is not None:
        session.add(AuditLog(
            document_id=document.id,
            action=AuditAction.SPLIT_CREATED,
            details=json.dumps(
                {
                    "text": split_part.audit_text(),
                    "split_batch_id": split_part.batch_id,
                    "part": split_part.part_index,
                    "parts": split_part.part_count,
                    "source_pages": split_part.source_pages,
                },
                ensure_ascii=False,
            ),
        ))

    try:
        await session.flush()
    except (IntegrityError, DataError) as exc:
        raise ArchiveDataError(f"Dokument konnte nicht gespeichert werden: {exc.orig}") from exc

    # Dateien erst jetzt kopieren — alle DB-Eintraege sind geflusht, ein
    # Datenfehler hinterlaesst also keine verwaiste Kopie mehr.
    # T17: copy2 in to_thread, damit grosse PDFs (50 MB) den Event-Loop nicht
    # blockieren.
    try:
        await asyncio.to_thread(_copy_file, file_path, archive_path)
    except Exception:
        remove_created_files([archive_path])
        raise
    if created_files is not None:
        created_files.append(archive_path)
    logger.info("Datei archiviert: %s -> %s", file_path.name, archive_path)

    # Export-Kopie in den konfigurierten Zielordner (optional): erst NACH dem
    # Commit des Aufrufers (R2) — Netzwerkverbindung und Kopie duerfen die
    # SQLite-Schreibsperre dieser Transaktion nicht halten.
    if export_dir and export_requests is not None:
        export_requests.append(ExportRequest(
            archive_path=archive_path, document_type=doc_type.value, document_date=doc_date,
            stored_filename=stored_filename, scope_slug=scope.scope_slug, config=export_config,
        ))

    # FTS-Index aktualisieren (in Savepoint, damit Fehler die Session nicht korrumpieren).
    # Werte inkl. Tags kommen aus der DB (reindex_document_fts) — identisch zu
    # spaeteren Re-Indexierungen bei Bearbeitung/Tag-Aenderung.
    try:
        async with session.begin_nested():
            await reindex_document_fts(session, document.id)
    except Exception:
        logger.warning("FTS-Indexierung fehlgeschlagen fuer %s", document.id, exc_info=True)

    # B3-Fix: Vektorisierung laeuft nicht in der DB-Transaction. Der Caller
    # (queue_worker_service) ruft vectorize_document() NACH dem Commit auf —
    # sonst hielte ein langsamer ChromaDB-Call die SQLite-Schreibsperre der
    # offenen Transaktion, und ein Vektorisierungs-Fehler korrumpierte die
    # archive-Transaction.

    logger.info(
        "Dokument archiviert: %s (Typ: %s, Konfidenz: %.0f%%)",
        document.id,
        doc_type.value,
        analysis.confidence * 100,
    )
    return document
