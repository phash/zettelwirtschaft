"""Chat-API: KI-Assistent fuer Dokumenten-Fragen."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.chat_message import ChatMessage, MessageRole
from app.schemas.chat import (
    ChatHistoryResponse,
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.services.rag_service import ask_question

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Stellt eine Frage an den KI-Assistenten."""
    try:
        result = await ask_question(
            question=request.question,
            settings=settings,
            session=session,
            filing_scope_id=request.filing_scope_id,
        )
    except Exception:
        logger.exception("RAG-Pipeline fehlgeschlagen")
        raise HTTPException(503, "KI-Assistent derzeit nicht verfuegbar")

    # Nachricht des Benutzers speichern
    now = datetime.now(timezone.utc)
    user_msg = ChatMessage(
        role=MessageRole.USER,
        content=request.question,
        filing_scope_id=request.filing_scope_id,
        created_at=now,
    )
    session.add(user_msg)

    # Antwort speichern (1 Sekunde spaeter fuer korrekte Sortierung)
    from datetime import timedelta
    assistant_msg = ChatMessage(
        role=MessageRole.ASSISTANT,
        content=result["answer"],
        sources_json=json.dumps(result["sources"], ensure_ascii=False) if result["sources"] else None,
        filing_scope_id=request.filing_scope_id,
        created_at=now + timedelta(milliseconds=1),
    )
    session.add(assistant_msg)

    sources = [ChatSource(**s) for s in result["sources"]]
    return ChatResponse(
        answer=result["answer"],
        sources=sources,
        chunks_found=result["chunks_found"],
    )


@router.get("/chat/history", response_model=ChatHistoryResponse)
async def chat_history(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
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
            except (json.JSONDecodeError, TypeError):
                pass

        response_messages.append(
            ChatMessageResponse(
                id=msg.id,
                role=msg.role.value if hasattr(msg.role, "value") else msg.role,
                content=msg.content,
                sources=sources,
                filing_scope_id=msg.filing_scope_id,
                created_at=msg.created_at,
            )
        )

    return ChatHistoryResponse(messages=response_messages, total=total)


@router.delete("/chat/history")
async def clear_chat_history(session: AsyncSession = Depends(get_db)):
    """Loescht den gesamten Chatverlauf."""
    from sqlalchemy import delete
    await session.execute(delete(ChatMessage))
    return {"message": "Chatverlauf geloescht"}
