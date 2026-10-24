"""Erweitertes Rückfrage-System API."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.normalize import enum_value
from app.database import get_db, get_session_factory
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, ReviewStatus
from app.models.review_question import ReviewQuestion
from app.models.split_batch import SplitBatch, SplitBatchStatus
from app.schemas.background_task import TaskResponse
from app.schemas.reanalysis import ReanalyzeRequest
from app.services.file_relocation import RelocationError, relocate_document_file
from app.services.reanalysis_service import (
    NO_OCR_TEXT_MESSAGE,
    TASK_KIND as REANALYZE_TASK_KIND,
    load_document_for_review,
    run_reanalysis,
)
from app.services import vectorize_service
from app.services.review_answers import AnswerError, apply_answer
from app.services.search_service import FTS_DOCUMENT_FIELDS, reindex_document_fts
from app.services.task_service import TaskContext, start_task

logger = logging.getLogger(__name__)
router = APIRouter(tags=["review"])

DOCUMENT_NOT_FOUND = "Dokument nicht gefunden"
# AVAIL-06: Obergrenze aktiver (wartender + laufender) Re-Analysen ueber alle Dokumente.
MAX_ACTIVE_REANALYSES = 3


async def _get_document_or_404(session: AsyncSession, document_id: str) -> Document:
    doc = await load_document_for_review(session, document_id)
    if doc is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return doc


def _review_audit(document_id: str, action: str, **details) -> AuditLog:
    return AuditLog(
        document_id=document_id,
        action=AuditAction.REVIEWED,
        details=json.dumps({"action": action, **details}, ensure_ascii=False),
    )


@router.get("/review/pending")
async def review_pending(session: AsyncSession = Depends(get_db, scope="function")):
    """Dokumente mit offenen Rückfragen."""
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
            "document_type": enum_value(doc.document_type),
            "has_thumbnail": bool(doc.thumbnail_path),
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
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Detaillierte Review-Daten für ein Dokument."""
    doc = await _get_document_or_404(session, document_id)

    questions = [
        {
            "id": q.id,
            "question": q.question,
            "question_type": q.question_type,
            "explanation": q.explanation,
            "field_affected": q.field_affected,
            "suggested_answers": q.suggested_answers,
            "answer": q.answer,
            "is_answered": q.is_answered,
            "priority": q.priority,
        }
        for q in doc.review_questions
    ]
    # Sortiere nach Prioritaet
    questions.sort(key=lambda q: q["priority"] or 0, reverse=True)

    return {
        "id": doc.id,
        "title": doc.title,
        "document_type": enum_value(doc.document_type),
        "file_type": doc.file_type,
        "ai_confidence": doc.ai_confidence,
        "summary": doc.summary,
        "ocr_text": doc.ocr_text[:2000] if doc.ocr_text else "",
        "questions": questions,
        "confident_fields": {
            "title": doc.title,
            "document_type": enum_value(doc.document_type),
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
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
):
    """Beantwortet eine Rückfrage und übernimmt die Antwort ins Dokument.

    Die Antwort wird zuerst auf das betroffene Feld angewendet; geht das nicht
    (z.B. kein gültiger Betrag), antwortet der Endpoint mit 422 und die Frage
    bleibt offen. Geänderte Felder landen als UPDATED im AuditLog — eine
    spätere Re-Analyse lässt sie dadurch unangetastet.
    """
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(400, "Antwort darf nicht leer sein")

    result = await session.execute(
        select(ReviewQuestion).where(ReviewQuestion.id == question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(404, "Frage nicht gefunden")
    doc = await _get_document_or_404(session, question.document_id)

    try:
        outcome = await apply_answer(
            session, doc, question.field_affected, answer, question.suggested_answers
        )
    except AnswerError as exc:
        raise HTTPException(422, str(exc))

    question.answer = answer
    question.is_answered = True
    question.answered_at = datetime.now(timezone.utc)

    changes = dict(outcome.changes)
    if outcome.new_scope is not None:
        changes["filing_scope_id"] = {"old": str(doc.filing_scope_id), "new": outcome.new_scope.id}
    if changes:
        session.add(AuditLog(
            document_id=doc.id,
            action=AuditAction.UPDATED,
            details=json.dumps(changes, ensure_ascii=False),
        ))
    if FTS_DOCUMENT_FIELDS & changes.keys():
        await reindex_document_fts(session, doc.id)

    # Pruefen ob alle Fragen beantwortet (die aktuelle ist es jetzt)
    all_answered = all(q.is_answered for q in doc.review_questions)

    if outcome.new_scope is not None:
        # Datei wandert in den Ordner des neuen Bereichs (copy -> commit -> delete)
        try:
            await relocate_document_file(session, doc, outcome.new_scope, settings.ARCHIVE_DIR)
        except RelocationError as exc:
            raise HTTPException(500, str(exc))
    else:
        await session.commit()

    # Vektoren nachziehen (post-commit, best effort — die Helfer loggen selbst)
    # Ein reiner Ablagebereichs-Wechsel braucht kein Re-Embedding (nur Chroma-Metadaten).
    if vectorize_service.METADATA_CHUNK_FIELDS & changes.keys():
        await vectorize_service.revectorize_document_safe(doc.id, settings, session_factory)
    elif outcome.new_scope is not None:
        await vectorize_service.update_scope_metadata([doc.id], outcome.new_scope.id, settings)

    return {
        "ok": True,
        "all_answered": all_answered,
    }


@router.post("/review/documents/{document_id}/approve")
async def approve_document(
    document_id: str,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """'Freigeben': Antworten bestätigen, Dokument aus dem Review entlassen.

    Bewusst auch mit offenen Rückfragen erlaubt (explizite Annahme durch den
    Benutzer); die Anzahl offener Fragen steht im AuditLog.
    """
    doc = await _get_document_or_404(session, document_id)
    open_questions = sum(1 for q in doc.review_questions if not q.is_answered)
    doc.review_status = ReviewStatus.REVIEWED
    session.add(_review_audit(doc.id, "approve", open_questions=open_questions))
    return {"ok": True, "review_status": ReviewStatus.REVIEWED.value}


@router.post("/review/documents/{document_id}/accept")
async def accept_document(
    document_id: str,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """'So übernehmen': das KI-Ergebnis unverändert als geprüft akzeptieren.

    Ersetzt das frühere 'Überspringen', das Dokumente still als OK markierte.
    'Später' ändert den Status nicht und lebt nur im Frontend.
    """
    doc = await _get_document_or_404(session, document_id)
    open_questions = sum(1 for q in doc.review_questions if not q.is_answered)
    doc.review_status = ReviewStatus.REVIEWED
    session.add(_review_audit(doc.id, "accept", open_questions=open_questions))
    return {"ok": True, "review_status": ReviewStatus.REVIEWED.value}


@router.post(
    "/review/documents/{document_id}/reanalyze",
    status_code=202,
    response_model=TaskResponse,
)
async def reanalyze_document(
    document_id: str,
    body: ReanalyzeRequest | None = None,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> TaskResponse:
    """Startet die KI-Analyse erneut als Hintergrund-Vorgang (HTTP 202).

    Optionaler Body ``{"with_ocr": true}``: der Text wird aus der Datei neu
    erkannt (dann ist auch ein Dokument ohne gespeicherten Text zulässig).

    Status/Ergebnis über ``GET /api/tasks/{id}``. Nutzt die Haupt-Pipeline und
    legt nur einen Änderungsvorschlag an — das Dokument ändert sich erst beim
    Übernehmen (siehe reanalysis_service).
    """
    doc = await _get_document_or_404(session, document_id)
    with_ocr = body.with_ocr if body is not None else False
    if not with_ocr and not (doc.ocr_text or "").strip():
        raise HTTPException(400, NO_OCR_TEXT_MESSAGE)

    async def work(ctx: TaskContext):
        return await run_reanalysis(ctx, document_id, settings, with_ocr=with_ocr)

    task = await start_task(
        session_factory, REANALYZE_TASK_KIND, work, key=document_id,
        conflict_message="Für dieses Dokument läuft bereits eine Re-Analyse.",
        # AVAIL-06: Re-Analysen teilen sich den Analyse-Slot mit dem
        # Queue-Worker; mehr als ein paar wartende Vorgaenge sind kein
        # legitimer Bedienfall (die UI analysiert ein Dokument nach dem anderen).
        max_active=MAX_ACTIVE_REANALYSES,
        limit_message="Es laufen bereits mehrere Re-Analysen. Bitte warten, bis sie fertig sind.",
    )
    return TaskResponse.model_validate(task)


@router.get("/review/stats")
async def review_stats(session: AsyncSession = Depends(get_db, scope="function")):
    """Review-Statistiken (ohne gelöschte Dokumente)."""
    open_docs_result = await session.execute(
        select(func.count()).select_from(Document)
        .where(Document.review_status == ReviewStatus.NEEDS_REVIEW)
        .where(Document.status != DocumentStatus.DELETED)
    )
    open_docs = open_docs_result.scalar() or 0

    # Offene Fragen nur von Dokumenten, die noch im Review sind — Fragen
    # freigegebener/akzeptierter Dokumente sind erledigt.
    open_q_result = await session.execute(
        select(func.count()).select_from(ReviewQuestion)
        .join(Document, Document.id == ReviewQuestion.document_id)
        .where(ReviewQuestion.is_answered.is_(False))
        .where(Document.review_status == ReviewStatus.NEEDS_REVIEW)
        .where(Document.status != DocumentStatus.DELETED)
    )
    open_questions = open_q_result.scalar() or 0

    answered_result = await session.execute(
        select(func.count()).select_from(ReviewQuestion)
        .join(Document, Document.id == ReviewQuestion.document_id)
        .where(ReviewQuestion.is_answered.is_(True))
        .where(Document.status != DocumentStatus.DELETED)
    )
    answered = answered_result.scalar() or 0

    # Stapel, die im Aufteilungs-Editor auf den Benutzer warten (Spezifikation 12)
    split_batches = (await session.execute(
        select(func.count(SplitBatch.id)).where(SplitBatch.status == SplitBatchStatus.PROPOSED)
    )).scalar() or 0

    return {
        "open_documents": open_docs,
        "open_questions": open_questions,
        "answered_questions": answered,
        "split_batches": split_batches,
    }
