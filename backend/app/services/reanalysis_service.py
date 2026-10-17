"""Re-Analyse eines archivierten Dokuments (Hintergrund-Vorgang).

Nutzt die Haupt-Pipeline ``analyze_document`` (inkl. OCR-Sanitizer, Verifier,
Fallbacks, Few-Shot) mit dem gespeicherten OCR-Text — keine eigene
Mini-Pipeline mehr. Danach werden die KI-Felder uebernommen:

- Vom Benutzer bearbeitete Felder bleiben unangetastet. "Bearbeitet" heisst:
  ein AuditLog-Eintrag UPDATED nennt das Feld (PATCH /documents, beantwortete
  Rueckfragen, PATCH /warranties fuer die Garantie).
- Alle anderen KI-Felder werden vollstaendig ueberschrieben — auch mit
  "leer", wenn die KI nichts mehr erkennt.
- Tags: manuell hinzugefuegte bleiben, manuell entfernte kommen nicht wieder,
  automatische werden durch die neuen ersetzt.
- Ablagebereich: neu bestimmt (Keyword > LLM > Default), ausser der Benutzer
  hat ihn gesetzt; die Datei wandert rollback-sicher mit.
- Offene Rueckfragen zu ueberschriebenen Feldern bzw. Freitext-Fragen der
  vorherigen Analyse werden ersetzt; andere offene Fragen bleiben.

Faellt die KI aus, bleibt das Dokument unveraendert und der Vorgang endet
mit einer verstaendlichen Fehlermeldung.
"""

import json
import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.date_utils import parse_date_lenient
from app.core.normalize import DEFAULT_CURRENCY, enum_value, normalize_currency
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, ReviewStatus
from app.models.warranty_info import WARRANTY_AUDIT_PREFIX
from app.services import vectorize_service
from app.services.analysis_service import AnalysisResult, analyze_document
from app.services.archive_service import (
    _build_review_question,
    _build_warranty_info,
    _get_or_create_tags,
    _match_filing_scope,
    _parse_document_type,
    _parse_tax_category,
    build_scope_question,
    coerce_decimal_amount,
)
from app.services.correction_service import apply_auto_corrections
from app.services.file_relocation import (
    RelocationError,
    copy_files,
    discard_copies,
    known_scope_slugs,
    plan_move,
    remove_sources,
)
from app.services.filing_scope_service import scopes_for_analysis
from app.services.ollama_client import OllamaError, OllamaErrorKind
from app.services.search_service import reindex_document_fts
from app.services.task_service import TaskContext, TaskError
from app.services.tax_year import ensure_tax_year

logger = logging.getLogger(__name__)

TASK_KIND = "reanalyze"

SCOPE_FIELD = "filing_scope_id"

# KI-Felder, die die Re-Analyse ueberschreibt (Dokumentfeld-Namen).
REANALYZED_FIELDS = (
    "document_type", "title", "issuer", "document_date", "amount", "currency",
    "summary", "tax_relevant", "tax_category", "tax_year", "recipient",
    "reference_number",
)

DOCUMENT_GONE_MESSAGE = "Das Dokument existiert nicht mehr oder wurde gelöscht."
NO_OCR_TEXT_MESSAGE = "Kein OCR-Text vorhanden, Re-Analyse nicht möglich."


async def load_document_for_review(session: AsyncSession, document_id: str) -> Document | None:
    """Dokument laden — ``None`` fuer unbekannte UND geloeschte Dokumente."""
    result = await session.execute(
        select(Document)
        .where(Document.id == document_id)
        .where(Document.status != DocumentStatus.DELETED)
    )
    return result.scalar_one_or_none()


async def user_edited_fields(session: AsyncSession, document_id: str) -> set[str]:
    """Feldnamen, die der Benutzer je bearbeitet hat (AuditLog UPDATED)."""
    result = await session.execute(
        select(AuditLog.details)
        .where(AuditLog.document_id == document_id)
        .where(AuditLog.action == AuditAction.UPDATED)
    )
    fields: set[str] = set()
    for (details,) in result.all():
        try:
            data = json.loads(details or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            fields.update(data.keys())
    return fields


async def _user_tag_decisions(session: AsyncSession, document_id: str) -> tuple[set[str], set[str]]:
    """(manuell hinzugefuegt, manuell entfernt) — jeweils nach letztem Ereignis."""
    result = await session.execute(
        select(AuditLog.action, AuditLog.details)
        .where(AuditLog.document_id == document_id)
        .where(AuditLog.action.in_([AuditAction.TAG_ADDED, AuditAction.TAG_REMOVED]))
        .order_by(AuditLog.id)
    )
    last: dict[str, str] = {}
    for action, details in result.all():
        try:
            name = str(json.loads(details or "{}").get("tag") or "").strip().lower()
        except (TypeError, ValueError, AttributeError):
            continue
        if name:
            last[name] = action
    added = {n for n, a in last.items() if a == AuditAction.TAG_ADDED}
    removed = {n for n, a in last.items() if a == AuditAction.TAG_REMOVED}
    return added, removed


def _llm_failure_message(analysis: AnalysisResult | None, settings: Settings) -> str:
    kind = OllamaErrorKind.INVALID_RESPONSE
    if analysis is not None and analysis.llm_error:
        try:
            kind = OllamaErrorKind(analysis.llm_error)
        except ValueError:
            pass
    reason = OllamaError(kind, model=settings.OLLAMA_MODEL).user_message
    return f"Die Re-Analyse ist fehlgeschlagen: {reason} Das Dokument wurde nicht verändert."


def _analysis_values(analysis: AnalysisResult, original_filename: str) -> dict:
    """KI-Ergebnis als Dokumentfeld-Werte (gleiche Helfer wie archive_document)."""
    return {
        "document_type": _parse_document_type(analysis.document_type),
        "title": analysis.title or original_filename,
        "issuer": analysis.sender,
        "document_date": parse_date_lenient(analysis.document_date),
        "amount": coerce_decimal_amount(analysis.amount),
        "currency": normalize_currency(analysis.currency) or DEFAULT_CURRENCY,
        "summary": analysis.summary,
        "tax_relevant": bool(analysis.tax_relevant),
        "tax_category": _parse_tax_category(analysis.tax_category),
        "tax_year": analysis.tax_year,
        "recipient": analysis.recipient,
        "reference_number": analysis.reference_number,
    }


async def _sync_tags(session: AsyncSession, doc: Document, new_names: list[str]) -> None:
    manual_added, manual_removed = await _user_tag_decisions(session, doc.id)
    current = {t.name: t for t in doc.tags}
    wanted = {
        t.name: t
        for t in await _get_or_create_tags(new_names or [], session)
        if t.name not in manual_removed
    }
    keep = {name for name in current if name in manual_added}

    drop_ids = [tag.id for name, tag in current.items() if name not in keep and name not in wanted]
    if drop_ids:
        await session.execute(
            delete(DocumentTag)
            .where(DocumentTag.document_id == doc.id)
            .where(DocumentTag.tag_id.in_(drop_ids))
        )
    for name, tag in wanted.items():
        if name not in current:
            session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))


async def _sync_warranty(session: AsyncSession, doc: Document, analysis: AnalysisResult) -> None:
    new = _build_warranty_info(analysis.warranty_info, doc.id, doc.document_date)
    existing = doc.warranty_info
    if existing is None:
        if new is not None:
            session.add(new)
        return
    if new is None:
        await session.delete(existing)
        return
    end_changed = existing.warranty_end_date != new.warranty_end_date
    for field in (
        "product_name", "purchase_date", "warranty_end_date", "warranty_type",
        "warranty_duration_months", "retailer",
    ):
        setattr(existing, field, getattr(new, field))
    if end_changed:
        existing.rearm_reminders()


async def run_reanalysis(ctx: TaskContext, document_id: str, settings: Settings) -> dict:
    """``work``-Funktion fuer den Hintergrund-Vorgang ``reanalyze``."""
    await ctx.report_progress(5, "Dokument wird geladen …")
    async with ctx.session_factory() as session:
        doc = await load_document_for_review(session, document_id)
        if doc is None:
            raise TaskError(DOCUMENT_GONE_MESSAGE)
        if not (doc.ocr_text or "").strip():
            raise TaskError(NO_OCR_TEXT_MESSAGE)
        file_path, file_type, ocr_text = doc.file_path, doc.file_type, doc.ocr_text
        filing_scopes = await scopes_for_analysis(session)

    await ctx.report_progress(15, "Die KI analysiert das Dokument …")
    # Eigene Session nur zum Lesen der Few-Shot-Korrekturen — keine
    # Schreib-Transaktion ueber den LLM-Aufruf.
    async with ctx.session_factory() as session:
        _, analysis = await analyze_document(
            Path(file_path), file_type, settings, filing_scopes,
            session=session, pre_ocr_text=ocr_text,
        )
    if analysis is None or analysis.llm_error:
        raise TaskError(_llm_failure_message(analysis, settings))

    await ctx.report_progress(85, "Ergebnisse werden übernommen …")
    async with ctx.session_factory() as session:
        doc = await load_document_for_review(session, document_id)
        if doc is None:
            raise TaskError(DOCUMENT_GONE_MESSAGE)

        corrected = await apply_auto_corrections(session, analysis)
        protected = await user_edited_fields(session, doc.id)
        values = _analysis_values(analysis, doc.original_filename)
        overwritten: list[str] = []
        for field in REANALYZED_FIELDS:
            if field in protected:
                continue
            overwritten.append(field)
            setattr(doc, field, values[field])
        doc.ai_confidence = analysis.confidence
        ensure_tax_year(doc)

        await _sync_tags(session, doc, analysis.tags)
        if not any(key.startswith(WARRANTY_AUDIT_PREFIX) for key in protected):
            await _sync_warranty(session, doc, analysis)

        # Ablagebereich + Datei
        moves = []
        scope = None
        if filing_scopes and SCOPE_FIELD not in protected:
            scope = _match_filing_scope(analysis, filing_scopes, ocr_text=ocr_text)
            if scope.scope_id and scope.scope_id != doc.filing_scope_id:
                move = plan_move(doc, scope.scope_slug, Path(settings.ARCHIVE_DIR), await known_scope_slugs(session))
                moves = [move] if move else []
                doc.filing_scope_id = scope.scope_id
                if move:
                    doc.file_path = str(move.target)
                overwritten.append(SCOPE_FIELD)

        # Rueckfragen: veraltete offene Fragen der vorherigen Analyse ersetzen
        replaced_fields = set(overwritten)
        if SCOPE_FIELD in overwritten:
            replaced_fields.add("filing_scope")  # field_affected der Scope-Fragen
        open_questions = [q for q in doc.review_questions if not q.is_answered]
        kept_questions = []
        for q in open_questions:
            if q.field_affected is None or q.field_affected in replaced_fields:
                await session.delete(q)
            else:
                kept_questions.append(q)
        kept_texts = {q.question for q in kept_questions}
        new_questions = []
        for entry in analysis.review_questions or []:
            question = _build_review_question(doc.id, entry)
            if question is not None and question.question not in kept_texts:
                new_questions.append(question)
        if scope is not None and not any(q.field_affected == "filing_scope" for q in kept_questions):
            scope_question = build_scope_question(doc.id, scope, analysis, filing_scopes)
            if scope_question is not None:
                new_questions.append(scope_question)
        for question in new_questions:
            session.add(question)

        if analysis.needs_review or kept_questions or new_questions:
            doc.review_status = ReviewStatus.NEEDS_REVIEW
        elif doc.review_status != ReviewStatus.REVIEWED:
            doc.review_status = ReviewStatus.OK

        session.add(AuditLog(
            document_id=doc.id,
            action=AuditAction.REANALYZED,
            details=json.dumps(
                {
                    "overwritten": overwritten,
                    "protected": sorted(protected),
                    "auto_corrected": corrected,
                    "ai_confidence": analysis.confidence,
                },
                ensure_ascii=False,
            ),
        ))

        await reindex_document_fts(session, doc.id)
        try:
            await copy_files(moves)
        except RelocationError as exc:
            raise TaskError(str(exc)) from exc
        try:
            await session.commit()
        except Exception:
            await discard_copies(moves)
            raise
        await remove_sources(moves, Path(settings.ARCHIVE_DIR))

        result = {
            "message": "Dokument wurde neu analysiert.",
            "document_type": enum_value(doc.document_type),
            "title": doc.title,
            "confidence": analysis.confidence,
            "needs_review": doc.review_status == ReviewStatus.NEEDS_REVIEW,
            "review_status": enum_value(doc.review_status),
            "protected_fields": sorted(f for f in protected if f in REANALYZED_FIELDS or f == SCOPE_FIELD),
            "open_questions": len(kept_questions) + len(new_questions),
        }

    logger.info(
        "Re-Analyse fuer Dokument %s: Typ=%s, Konfidenz=%.0f%%, geschuetzt=%s",
        document_id, result["document_type"], analysis.confidence * 100, result["protected_fields"],
    )
    # Post-commit, best effort (Fehler loggt der Helfer selbst)
    await vectorize_service.revectorize_document_safe(document_id, settings, ctx.session_factory)
    return result
