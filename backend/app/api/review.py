"""Erweitertes Rueckfrage-System API."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.document import Document, DocumentStatus, ReviewStatus
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


@router.post("/review/questions/{question_id}/answer")
async def answer_question(
    question_id: str,
    body: dict,
    session: AsyncSession = Depends(get_db),
):
    """Beantwortet eine Rueckfrage und aktualisiert ggf. das Dokument."""
    answer = body.get("answer", "").strip()
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
    question.answered_at = datetime.utcnow()

    # Auto-Update des betroffenen Feldes
    if question.field_affected:
        doc_result = await session.execute(
            select(Document).where(Document.id == question.document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc:
            await _update_field_from_answer(doc, question.field_affected, answer, session)

    await session.commit()

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
    await session.commit()

    return {"ok": True, "review_status": ReviewStatus.REVIEWED}


@router.post("/review/documents/{document_id}/skip")
async def skip_document(
    document_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Dokument uebersprungen (bleibt NEEDS_REVIEW)."""
    return {"ok": True}


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
        try:
            doc.amount = float(answer.replace(",", ".").replace(" ", ""))
        except ValueError:
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
