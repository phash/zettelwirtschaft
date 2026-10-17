"""Chat-API: KI-Assistent fuer Dokumenten-Fragen."""

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.chat_message import ChatMessage, MessageRole
from app.models.filing_scope import FilingScope
from app.schemas.chat import (
    ChatHistoryResponse,
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.services.rag_service import RagServiceUnavailable, ask_question

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Stellt eine Frage an den KI-Assistenten.

    Frage und Antwort werden nur bei Erfolg im Verlauf gespeichert — nicht bei
    503 (KI nicht verfuegbar) und nicht, wenn der Client die Anfrage bereits
    abgebrochen hat (Timeout/Abbrechen-Button).
    """
    # Vor dem (teuren) LLM-Aufruf pruefen: ein unbekannter/geloeschter
    # Ablagebereich wuerde sonst erst beim Speichern an der FK scheitern.
    if request.filing_scope_id is not None:
        scope = await session.get(FilingScope, request.filing_scope_id)
        if scope is None:
            raise HTTPException(422, "Ablagebereich nicht gefunden")

    try:
        result = await ask_question(
            question=request.question,
            settings=settings,
            session=session,
            filing_scope_id=request.filing_scope_id,
        )
    except RagServiceUnavailable as e:
        raise HTTPException(503, e.message)
    except Exception:
        logger.exception("RAG-Pipeline fehlgeschlagen")
        raise HTTPException(503, "Der KI-Assistent ist derzeit nicht verfügbar.")

    sources = [ChatSource(**s) for s in result["sources"]]
    response = ChatResponse(
        answer=result["answer"],
        sources=sources,
        chunks_found=result["chunks_found"],
    )

    if await http_request.is_disconnected():
        logger.info("Chat-Anfrage vom Client abgebrochen — Antwort wird nicht gespeichert")
        return response

    # Antwort 1ms nach der Frage stempeln. L2: ChatMessage.id ist eine zufaellige
    # UUID und taugt NICHT als Tiebreaker — der Verlauf sortiert primaer nach
    # created_at, daher der explizite Offset, damit die Frage vor der Antwort steht.
    now = datetime.now(timezone.utc)
    session.add(ChatMessage(
        role=MessageRole.USER,
        content=request.question,
        filing_scope_id=request.filing_scope_id,
        created_at=now,
    ))
    session.add(ChatMessage(
        role=MessageRole.ASSISTANT,
        content=result["answer"],
        sources_json=json.dumps(result["sources"], ensure_ascii=False) if result["sources"] else None,
        filing_scope_id=request.filing_scope_id,
        created_at=now + timedelta(milliseconds=1),
    ))
    # Flush noch im Request: Constraint-Fehler werden so zur Fehlerantwort statt
    # nach dem Senden der Antwort still verworfen zu werden.
    await session.flush()
    return response


@router.get("/chat/history", response_model=ChatHistoryResponse)
async def chat_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Gibt den Chatverlauf zurueck."""
    # Total count
    count_result = await session.execute(
        select(func.count()).select_from(ChatMessage)
    )
    total = count_result.scalar() or 0

    # Nachrichten laden (neueste zuerst, dann umkehren fuer chronologische Anzeige)
    result = await session.execute(
        select(ChatMessage)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        .offset(offset)
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))

    response_messages = []
    for msg in messages:
        sources = []
        if msg.sources_json:
            try:
                sources = [ChatSource(**s) for s in json.loads(msg.sources_json)]
            except (ValueError, TypeError):  # inkl. JSONDecodeError / ValidationError
                pass

        response_messages.append(
            ChatMessageResponse(
                id=msg.id,
                role=msg.role.value,
                content=msg.content,
                sources=sources,
                filing_scope_id=msg.filing_scope_id,
                created_at=msg.created_at,
            )
        )

    return ChatHistoryResponse(messages=response_messages, total=total)


@router.delete("/chat/history")
async def clear_chat_history(session: AsyncSession = Depends(get_db, scope="function")):
    """Loescht den gesamten Chatverlauf."""
    await session.execute(delete(ChatMessage))
    return {"message": "Chatverlauf gelöscht"}
