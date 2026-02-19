"""Garantie-Tracker API."""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.document import Document
from app.models.warranty_info import WarrantyInfo
from app.schemas.warranty import WarrantyListItem, WarrantyStats, WarrantyUpdate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["warranties"])


def _days_remaining(end_date: date) -> int:
    return max(0, (end_date - date.today()).days)


@router.get("/warranties", response_model=list[WarrantyListItem])
async def list_warranties(
    status: str | None = Query(None, description="active|expiring|expired"),
    sort_by: str = Query("warranty_end_date", description="Sort field"),
    session: AsyncSession = Depends(get_db),
):
    """Liste aller Garantien."""
    stmt = select(WarrantyInfo).join(Document)
    today = date.today()

    if status == "active":
        stmt = stmt.where(WarrantyInfo.warranty_end_date > today)
    elif status == "expiring":
        stmt = stmt.where(WarrantyInfo.warranty_end_date > today)
        stmt = stmt.where(WarrantyInfo.warranty_end_date <= today + timedelta(days=90))
    elif status == "expired":
        stmt = stmt.where(WarrantyInfo.warranty_end_date <= today)

    stmt = stmt.order_by(WarrantyInfo.warranty_end_date.asc())

    result = await session.execute(stmt)
    warranties = result.scalars().all()

    items = []
    for w in warranties:
        item = WarrantyListItem.model_validate(w)
        item.days_remaining = _days_remaining(w.warranty_end_date)
        item.document_title = w.document.title if w.document else ""
        item.document_thumbnail = w.document.thumbnail_path if w.document else None
        items.append(item)

    return items


@router.get("/warranties/stats", response_model=WarrantyStats)
async def warranty_stats(session: AsyncSession = Depends(get_db)):
    """Garantie-Statistiken."""
    result = await session.execute(select(WarrantyInfo))
    all_warranties = result.scalars().all()

    today = date.today()
    threshold = today + timedelta(days=90)

    active = sum(1 for w in all_warranties if w.warranty_end_date > today)
    expiring = sum(1 for w in all_warranties if today < w.warranty_end_date <= threshold)
    expired = sum(1 for w in all_warranties if w.warranty_end_date <= today)

    return WarrantyStats(
        total=len(all_warranties),
        active=active,
        expiring_soon=expiring,
        expired=expired,
    )


@router.patch("/warranties/{warranty_id}", response_model=WarrantyListItem)
async def update_warranty(
    warranty_id: str,
    body: WarrantyUpdate,
    session: AsyncSession = Depends(get_db),
):
    """Garantie aktualisieren."""
    result = await session.execute(
        select(WarrantyInfo).where(WarrantyInfo.id == warranty_id)
    )
    warranty = result.scalar_one_or_none()
    if not warranty:
        raise HTTPException(404, "Garantie nicht gefunden")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(warranty, field, value)

    await session.commit()
    await session.refresh(warranty)

    item = WarrantyListItem.model_validate(warranty)
    item.days_remaining = _days_remaining(warranty.warranty_end_date)
    item.document_title = warranty.document.title if warranty.document else ""
    item.document_thumbnail = warranty.document.thumbnail_path if warranty.document else None
    return item
