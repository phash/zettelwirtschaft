"""Erweitertes Rueckfrage-System API."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.models.filing_scope import FilingScope
from app.models.review_question import ReviewQuestion
from app.models.correction_mapping import CorrectionMapping

logger = logging.getLogger(__name__)
router = APIRouter(tags=["review"])


@router.get("/review/pending")
async def review_pending(session: AsyncSession = Depends(get_db)):
    """Dokumente mit offenen Rueckfragen."""
    stmt = (
        select(Document)
        .where(Document.review_status == ReviewStatus.NEEDS_REVIEW)
        .where(Document.status != DocumentStatus.DELETED)
        .order_by(Document.created_at.desc())
    )
    result = await session.execute(stmt)
    docs = result.scalars().all()

    items = []
    for doc in docs:
        open_q = [q for q in doc.review_questions if not q.is_answered]
        items.append({
            "id": doc.id,
            "title": doc.title,
            "document_type": doc.document_type,
            "thumbnail_path": doc.thumbnail_path,
            "file_type": doc.file_type,
            "ai_confidence": doc.ai_confidence,
            "created_at": doc.created_at.isoformat(),
            "open_questions": len(open_q),
            "total_questions": len(doc.review_questions),
        })

    return {"documents": items, "total": len(items)}


@router.get("/review/documents/{document_id}")
async def review_document_detail(
    document_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Detaillierte Review-Daten fuer ein Dokument."""
    result = await session.execute(
        select(Document).where(Document.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")

    questions = []
    for q in doc.review_questions:
        questions.append({
            "id": q.id,
            "question": q.question,
            "question_type": getattr(q, "question_type", None),
            "explanation": getattr(q, "explanation", None),
            "field_affected": q.field_affected,
            "suggested_answers": getattr(q, "suggested_answers", None),
            "answer": q.answer,
            "is_answered": q.is_answered,
            "priority": getattr(q, "priority", 0),
        })

    # Sortiere nach Prioritaet
    questions.sort(key=lambda q: q.get("priority", 0), reverse=True)

    return {
        "id": doc.id,
        "title": doc.title,
        "document_type": doc.document_type,
        "file_type": doc.file_type,
        "ai_confidence": doc.ai_confidence,
        "summary": doc.summary,
        "ocr_text": doc.ocr_text[:2000] if doc.ocr_text else "",
        "questions": questions,
        "confident_fields": {
            "title": doc.title,
            "document_type": doc.document_type,
            "document_date": str(doc.document_date) if doc.document_date else None,
            "amount": float(doc.amount) if doc.amount is not None else None,
            "currency": doc.currency,
            "issuer": doc.issuer,
            "filing_scope": doc.filing_scope.name if doc.filing_scope else None,
        },
    }


class AnswerRequest(BaseModel):
    answer: str


@router.post("/review/questions/{question_id}/answer")
async def answer_question(
    question_id: str,
    body: AnswerRequest,
    session: AsyncSession = Depends(get_db),
):
    """Beantwortet eine Rueckfrage und aktualisiert ggf. das Dokument."""
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(400, "Antwort darf nicht leer sein")

    result = await session.execute(
        select(ReviewQuestion).where(ReviewQuestion.id == question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(404, "Frage nicht gefunden")

    # Frage beantworten
    question.answer = answer
    question.is_answered = True
    question.answered_at = datetime.now(timezone.utc)

    # Auto-Update des betroffenen Feldes
    if question.field_affected:
        doc_result = await session.execute(
            select(Document).where(Document.id == question.document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc:
            await _update_field_from_answer(doc, question.field_affected, answer, session)

    # Pruefen ob alle Fragen beantwortet
    all_q_result = await session.execute(
        select(ReviewQuestion).where(ReviewQuestion.document_id == question.document_id)
    )
    all_questions = all_q_result.scalars().all()
    all_answered = all(q.is_answered for q in all_questions)

    return {
        "ok": True,
        "all_answered": all_answered,
    }


@router.post("/review/documents/{document_id}/approve")
async def approve_document(
    document_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Alle Antworten bestaetigen, Dokument aus Review entlassen."""
    result = await session.execute(
        select(Document).where(Document.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")

    doc.review_status = ReviewStatus.REVIEWED

    return {"ok": True, "review_status": ReviewStatus.REVIEWED}


@router.post("/review/documents/{document_id}/skip")
async def skip_document(
    document_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Dokument ueberspringen - KI-Ergebnisse als korrekt akzeptieren."""
    result = await session.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    document.review_status = ReviewStatus.OK
    return {"ok": True}


@router.post("/review/documents/{document_id}/reanalyze")
async def reanalyze_document(
    document_id: str,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Fuehrt die LLM-Analyse erneut durch (z.B. wenn Ollama beim ersten Mal nicht erreichbar war)."""
    result = await session.execute(
        select(Document).where(Document.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")

    if not doc.ocr_text or not doc.ocr_text.strip():
        raise HTTPException(400, "Kein OCR-Text vorhanden, Re-Analyse nicht moeglich")

    # LLM-Analyse mit vorhandenem OCR-Text
    from app.services.analysis_service import _truncate_text, _try_combined_analysis, _try_sequential_analysis, _load_correction_examples

    # Filing Scopes laden
    scope_result = await session.execute(select(FilingScope))
    scopes = scope_result.scalars().all()
    filing_scopes = []
    for s in scopes:
        filing_scopes.append({
            "id": s.id, "name": s.name, "slug": s.slug,
            "keywords": s.parsed_keywords, "is_default": s.is_default,
        })

    truncated_text = _truncate_text(doc.ocr_text)

    # Few-Shot-Examples aus User-Korrekturen laden
    corrections = await _load_correction_examples(session)

    # Kombinierte Analyse versuchen
    analysis = await _try_combined_analysis(truncated_text, settings, filing_scopes, corrections=corrections)
    if not analysis:
        analysis = await _try_sequential_analysis(truncated_text, settings)

    if not analysis:
        raise HTTPException(503, "LLM nicht erreichbar. Bitte spaeter erneut versuchen.")

    # Dokument aktualisieren
    if analysis.document_type:
        try:
            doc.document_type = DocumentType(analysis.document_type)
        except ValueError:
            pass
    if analysis.title:
        doc.title = analysis.title
    if analysis.sender:
        doc.issuer = analysis.sender
    if analysis.document_date:
        # N-02 (Re-Review): document_date kommt als String aus dem LLM.
        # Mapped[date | None] erwartet date — sonst landet ein String in
        # der DATE-Spalte (SQLite-TEXT-affinity schluckt's, aber date-Methoden
        # crashen spaeter).
        from app.services.archive_service import _parse_document_date
        parsed_date = _parse_document_date(analysis.document_date)
        if parsed_date:
            doc.document_date = parsed_date
    if analysis.amount is not None:
        doc.amount = analysis.amount
    if analysis.currency:
        doc.currency = analysis.currency
    if analysis.summary:
        doc.summary = analysis.summary
    if analysis.tax_relevant is not None:
        doc.tax_relevant = analysis.tax_relevant
    doc.ai_confidence = analysis.confidence

    # Alte Review-Fragen loeschen und ggf. neue erstellen
    old_questions = [q for q in doc.review_questions if not q.is_answered]
    for q in old_questions:
        await session.delete(q)

    if analysis.needs_review and analysis.review_questions:
        for q_data in analysis.review_questions:
            if isinstance(q_data, str):
                q = ReviewQuestion(document_id=doc.id, question=q_data)
            else:
                q = ReviewQuestion(
                    document_id=doc.id,
                    question=q_data.get("question", str(q_data)),
                    field_affected=q_data.get("field_affected"),
                    question_type=q_data.get("question_type"),
                    explanation=q_data.get("explanation"),
                    suggested_answers=q_data.get("suggested_answers"),
                    priority=q_data.get("priority", 0),
                )
            session.add(q)
        doc.review_status = ReviewStatus.NEEDS_REVIEW
    else:
        doc.review_status = ReviewStatus.REVIEWED

    logger.info("Re-Analyse fuer Dokument %s erfolgreich: Typ=%s, Konfidenz=%.0f%%",
                doc.id, doc.document_type, analysis.confidence * 100)

    return {
        "ok": True,
        "document_type": str(doc.document_type),
        "title": doc.title,
        "confidence": analysis.confidence,
        "needs_review": analysis.needs_review,
    }


@router.get("/review/stats")
async def review_stats(session: AsyncSession = Depends(get_db)):
    """Review-Statistiken."""
    # Offene Dokumente
    open_docs_result = await session.execute(
        select(func.count()).select_from(Document)
        .where(Document.review_status == ReviewStatus.NEEDS_REVIEW)
        .where(Document.status != DocumentStatus.DELETED)
    )
    open_docs = open_docs_result.scalar() or 0

    # Offene Fragen
    open_q_result = await session.execute(
        select(func.count()).select_from(ReviewQuestion)
        .where(ReviewQuestion.is_answered.is_(False))
    )
    open_questions = open_q_result.scalar() or 0

    # Beantwortete Fragen
    answered_result = await session.execute(
        select(func.count()).select_from(ReviewQuestion)
        .where(ReviewQuestion.is_answered.is_(True))
    )
    answered = answered_result.scalar() or 0

    return {
        "open_documents": open_docs,
        "open_questions": open_questions,
        "answered_questions": answered,
    }


async def _update_field_from_answer(
    doc: Document, field: str, answer: str, session: AsyncSession
) -> None:
    """Aktualisiert ein Dokumentfeld basierend auf der Antwort."""
    field_map = {
        "title": str,
        "issuer": str,
        "recipient": str,
        "reference_number": str,
        "summary": str,
    }

    if field in field_map:
        old_value = getattr(doc, field, None)
        setattr(doc, field, answer)

        # CorrectionMapping erstellen/aktualisieren
        if old_value and old_value != answer:
            await _record_correction(session, field, str(old_value), answer)

    elif field == "amount":
        # M-2 (Re-Review): Decimal statt float — Document.amount ist
        # Mapped[Decimal | None], float-Zuweisung verlaesst sich auf
        # SQLAlchemy-Implicit-Conversion und produziert Rundungsfehler
        # bei Aggregation. Decimal() wirft InvalidOperation bei nicht-numerischen
        # Strings — gleicher No-Op-Fall wie der alte ValueError-Pfad.
        from decimal import Decimal, InvalidOperation
        try:
            doc.amount = Decimal(answer.replace(",", ".").replace(" ", ""))
        except (ValueError, InvalidOperation):
            pass

    elif field == "document_date":
        from datetime import date
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                doc.document_date = datetime.strptime(answer, fmt).date()
                break
            except ValueError:
                continue

    elif field == "document_type":
        from app.models.document import DocumentType
        try:
            doc.document_type = DocumentType(answer)
        except ValueError:
            pass

    elif field == "tax_relevant":
        doc.tax_relevant = answer.lower() in ("ja", "yes", "true", "1")

    elif field == "filing_scope":
        if answer.startswith("NEU: "):
            # Neuen Ablagebereich erstellen
            new_name = answer[5:].strip()
            if new_name:
                from app.models.filing_scope import generate_slug
                new_slug = generate_slug(new_name)
                # Pruefen ob Scope mit diesem Namen/Slug bereits existiert
                existing = await session.execute(
                    select(FilingScope).where(
                        (FilingScope.name == new_name) | (FilingScope.slug == new_slug)
                    )
                )
                scope = existing.scalar_one_or_none()
                if not scope:
                    scope = FilingScope(
                        name=new_name,
                        slug=new_slug,
                        is_default=False,
                    )
                    session.add(scope)
                    await session.flush()
                    logger.info("Neuer Ablagebereich erstellt: '%s' (Slug: %s)", new_name, new_slug)
                old_scope_id = doc.filing_scope_id
                doc.filing_scope_id = scope.id
        else:
            scope_result = await session.execute(
                select(FilingScope).where(FilingScope.name == answer)
            )
            scope = scope_result.scalar_one_or_none()
            if scope:
                old_scope_id = doc.filing_scope_id
                doc.filing_scope_id = scope.id
                if old_scope_id and old_scope_id != scope.id:
                    await _record_correction(session, "filing_scope", old_scope_id, scope.id)


async def _record_correction(
    session: AsyncSession, field: str, original: str, corrected: str
) -> None:
    """Speichert oder aktualisiert ein CorrectionMapping."""
    result = await session.execute(
        select(CorrectionMapping)
        .where(CorrectionMapping.field == field)
        .where(CorrectionMapping.original_value == original)
        .where(CorrectionMapping.corrected_value == corrected)
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        mapping.occurrence_count += 1
        if mapping.occurrence_count >= 3:
            mapping.auto_apply = True
    else:
        session.add(CorrectionMapping(
            field=field,
            original_value=original,
            corrected_value=corrected,
        ))
