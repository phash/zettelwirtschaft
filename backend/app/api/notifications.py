"""Benachrichtigungs-API."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.notification import Notification
from app.schemas.notification import NotificationCount, NotificationResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    """Liste aller Benachrichtigungen."""
    stmt = select(Notification).order_by(Notification.created_at.desc())
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/notifications/count", response_model=NotificationCount)
async def notification_count(session: AsyncSession = Depends(get_db)):
    """Anzahl ungelesener Benachrichtigungen."""
    stmt = select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
    result = await session.execute(stmt)
    return NotificationCount(unread=result.scalar() or 0)


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Benachrichtigung als gelesen markieren."""
    result = await session.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Benachrichtigung nicht gefunden")
    n.is_read = True
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_read(session: AsyncSession = Depends(get_db)):
    """Alle Benachrichtigungen als gelesen markieren."""
    await session.execute(
        update(Notification).where(Notification.is_read.is_(False)).values(is_read=True)
    )
    return {"ok": True}
