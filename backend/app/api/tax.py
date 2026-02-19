"""Steuerpaket-Export API."""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.schemas.tax import TaxExportRequest, TaxExportValidation, TaxYearSummary
from app.services.tax_export_service import (
    create_tax_export_zip,
    get_tax_summary,
    validate_export,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tax"])


@router.get("/tax/summary/{year}", response_model=TaxYearSummary)
async def tax_summary(
    year: int,
    filing_scope_id: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    """Steuer-Zusammenfassung fuer ein Jahr."""
    if year < 2000 or year > date.today().year + 1:
        raise HTTPException(400, "Ungueltiges Jahr")
    return await get_tax_summary(session, year, filing_scope_id=filing_scope_id)


@router.get("/tax/validate/{year}", response_model=TaxExportValidation)
async def tax_validate(
    year: int,
    filing_scope_id: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    """Prueft ob Export bereit ist."""
    if year < 2000 or year > date.today().year + 1:
        raise HTTPException(400, "Ungueltiges Jahr")
    return await validate_export(session, year, filing_scope_id=filing_scope_id)


@router.post("/tax/export")
async def tax_export(
    body: TaxExportRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Erstellt und liefert Steuerpaket als ZIP."""
    if body.year < 2000 or body.year > date.today().year + 1:
        raise HTTPException(400, "Ungueltiges Jahr")

    validation = await validate_export(session, body.year, filing_scope_id=body.filing_scope_id)
    if not validation["ready"]:
        raise HTTPException(400, "Keine steuerrelevanten Dokumente fuer dieses Jahr")

    try:
        zip_bytes = await create_tax_export_zip(
            session, body.year, settings,
            include_pdf=body.include_overview_pdf,
            include_csv=body.include_csv,
            filing_scope_id=body.filing_scope_id,
        )
    except Exception:
        logger.exception("Fehler beim Erstellen des Steuerpakets")
        raise HTTPException(500, "Export fehlgeschlagen")

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="Steuerpaket_{body.year}.zip"',
        },
    )


@router.get("/tax/years")
async def tax_years(session: AsyncSession = Depends(get_db)):
    """Gibt Jahre zurueck, fuer die steuerrelevante Dokumente existieren."""
    from sqlalchemy import select, distinct
    from app.models.document import Document, DocumentStatus

    stmt = (
        select(distinct(Document.tax_year))
        .where(Document.tax_relevant.is_(True))
        .where(Document.status != DocumentStatus.DELETED)
        .where(Document.tax_year.isnot(None))
        .order_by(Document.tax_year.desc())
    )
    result = await session.execute(stmt)
    years = [row[0] for row in result.all()]
    return {"years": years}
