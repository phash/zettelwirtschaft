"""Garantie-Tracker API."""

import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.date_utils import add_months
from app.core.normalize import enum_value
from app.database import get_db
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus
from app.models.warranty_info import (
    WARRANTY_AUDIT_PREFIX,
    WarrantyInfo,
    WarrantyStatus,
    select_active_document_warranties,
    warranty_status_condition,
)
from app.schemas.warranty import WarrantyListItem, WarrantyStats, WarrantyUpdate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["warranties"])


def _days_remaining(end_date: date) -> int:
    return max(0, (end_date - date.today()).days)


def _to_item(warranty: WarrantyInfo) -> WarrantyListItem:
    item = WarrantyListItem.model_validate(warranty)
    item.days_remaining = _days_remaining(warranty.warranty_end_date)
    item.document_title = warranty.document.title if warranty.document else ""
    return item


@router.get("/warranties", response_model=list[WarrantyListItem])
async def list_warranties(
    status: WarrantyStatus | None = Query(
        None, description="active|expiring|expired"
    ),
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Liste aller Garantien."""
    # WarrantyInfo.document ist seit H-18 `lazy="raise"` — explizit nur das
    # benoetigte Feld (title) laden, nicht den ganzen Document-Tree mit
    # tags + review_questions.
    stmt = select_active_document_warranties().options(
        selectinload(WarrantyInfo.document).load_only(Document.title)
    )
    if status:
        stmt = stmt.where(warranty_status_condition(status, date.today()))

    stmt = stmt.order_by(WarrantyInfo.warranty_end_date.asc())

    result = await session.execute(stmt)
    return [_to_item(w) for w in result.scalars().all()]


@router.get("/warranties/stats", response_model=WarrantyStats)
async def warranty_stats(session: AsyncSession = Depends(get_db, scope="function")):
    """Garantie-Statistiken (ohne geloeschte Dokumente)."""
    today = date.today()

    def _count(status: str):
        return func.sum(case((warranty_status_condition(status, today), 1), else_=0))

    result = await session.execute(
        select(
            func.count().label("total"),
            _count("active").label("active"),
            _count("expiring").label("expiring"),
            _count("expired").label("expired"),
        )
        .select_from(WarrantyInfo)
        .join(Document, Document.id == WarrantyInfo.document_id)
        .where(Document.status != DocumentStatus.DELETED)
    )
    row = result.one()

    return WarrantyStats(
        total=row.total or 0,
        active=row.active or 0,
        expiring_soon=row.expiring or 0,
        expired=row.expired or 0,
    )


@router.patch("/warranties/{warranty_id}", response_model=WarrantyListItem)
async def update_warranty(
    warranty_id: str,
    body: WarrantyUpdate,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Garantie aktualisieren.

    Aendert sich nur die Laufzeit, wird das Enddatum aus Kaufdatum + Laufzeit
    neu berechnet. Aendert sich das Enddatum, werden die Erinnerungen neu
    scharf geschaltet.
    """
    result = await session.execute(
        select_active_document_warranties()
        .where(WarrantyInfo.id == warranty_id)
        .options(selectinload(WarrantyInfo.document).load_only(Document.title))
    )
    warranty = result.scalar_one_or_none()
    if not warranty:
        raise HTTPException(404, "Garantie nicht gefunden")

    values = body.model_dump(exclude_unset=True)
    if "warranty_duration_months" in values and "warranty_end_date" not in values:
        values["warranty_end_date"] = add_months(
            warranty.purchase_date, values["warranty_duration_months"]
        )

    changes = {}
    for field, value in values.items():
        old_value = getattr(warranty, field)
        if old_value != value:
            changes[f"{WARRANTY_AUDIT_PREFIX}{field}"] = {
                "old": str(enum_value(old_value)), "new": str(enum_value(value)),
            }
            setattr(warranty, field, value)

    if f"{WARRANTY_AUDIT_PREFIX}warranty_end_date" in changes:
        warranty.rearm_reminders()

    if changes:
        session.add(AuditLog(
            document_id=warranty.document_id,
            action=AuditAction.UPDATED,
            details=json.dumps(changes, ensure_ascii=False),
        ))

    await session.flush()
    # H3: KEIN blanket session.refresh(warranty) — WarrantyInfo.document ist
    # lazy="raise"; refresh wuerde die per selectinload geladene Relationship
    # expiren und der Zugriff in _to_item (warranty.document.title) liefe in
    # einen InvalidRequestError/MissingGreenlet. WarrantyInfo hat keine
    # server-seitigen onupdate-Spalten, die nach flush nachgeladen werden muessten.
    return _to_item(warranty)
