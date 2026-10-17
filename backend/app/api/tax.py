"""Steuerpaket-Export API."""

import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.document import Document, DocumentStatus
from app.schemas.tax import TaxExportRequest, TaxExportValidation, TaxYearSummary
from app.services.tax_export_service import (
    EmptyTaxExportError,
    create_tax_export_zip,
    get_tax_summary,
    validate_export,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tax"])


def _validate_year(year: int) -> None:
    if year < 2000 or year > date.today().year + 1:
        raise HTTPException(400, "Ungültiges Jahr")


@router.get("/tax/summary/{year}", response_model=TaxYearSummary)
async def tax_summary(
    year: int,
    filing_scope_id: str | None = None,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Steuer-Zusammenfassung für ein Jahr."""
    _validate_year(year)
    return await get_tax_summary(session, year, filing_scope_id=filing_scope_id)


@router.get("/tax/validate/{year}", response_model=TaxExportValidation)
async def tax_validate(
    year: int,
    filing_scope_id: str | None = None,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Prüft, ob der Export bereit ist."""
    _validate_year(year)
    return await validate_export(session, year, filing_scope_id=filing_scope_id)


@router.post("/tax/export")
async def tax_export(
    body: TaxExportRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Erstellt das Steuerpaket als ZIP und liefert es aus.

    Die ZIP-Datei entsteht als temporäre Datei (nicht im Speicher) und wird
    nach dem Ausliefern per Background-Task gelöscht.
    """
    _validate_year(body.year)

    try:
        zip_path = await create_tax_export_zip(
            session, body.year,
            include_pdf=body.include_overview_pdf,
            include_csv=body.include_csv,
            filing_scope_id=body.filing_scope_id,
        )
    except EmptyTaxExportError:
        raise HTTPException(400, "Keine steuerrelevanten Dokumente für dieses Jahr")
    except Exception:
        logger.exception("Fehler beim Erstellen des Steuerpakets")
        raise HTTPException(500, "Export fehlgeschlagen")

    background_tasks.add_task(zip_path.unlink, missing_ok=True)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"Steuerpaket_{body.year}.zip",
        background=background_tasks,
    )


@router.get("/tax/years")
async def tax_years(session: AsyncSession = Depends(get_db, scope="function")):
    """Gibt Jahre zurück, für die steuerrelevante Dokumente existieren."""
    stmt = (
        select(Document.tax_year)
        .distinct()
        .where(Document.tax_relevant.is_(True))
        .where(Document.status != DocumentStatus.DELETED)
        .where(Document.tax_year.isnot(None))
        .order_by(Document.tax_year.desc())
    )
    result = await session.execute(stmt)
    years = [row[0] for row in result.all()]
    return {"years": years}
