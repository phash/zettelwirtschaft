import json
import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BeforeValidator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.query_params import empty_to_none, parse_document_types
from app.database import get_db
from app.models.document import DocumentStatus, TaxCategory
from app.models.saved_search import SavedSearch
from app.schemas.search import (
    SavedSearchCreate,
    SavedSearchResponse,
    SearchFacets,
    SearchResponse,
    SearchResultItem,
    SuggestResponse,
)
from app.services.search_service import InvalidSearchQueryError, search_documents, suggest

logger = logging.getLogger(__name__)

router = APIRouter()

# Leere Formularwerte (``?amount_min=``) gelten als nicht gesetzt statt 422.
OptionalFloat = Annotated[float | None, BeforeValidator(empty_to_none)]
OptionalInt = Annotated[int | None, BeforeValidator(empty_to_none)]
OptionalDate = Annotated[date | None, BeforeValidator(empty_to_none)]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str | None = None,
    document_type: str | None = Query(None, description="Kommagetrennte Dokumenttypen"),
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    amount_min: OptionalFloat = None,
    amount_max: OptionalFloat = None,
    issuer: str | None = None,
    tax_relevant: bool | None = None,
    tax_year: OptionalInt = None,
    tax_category: TaxCategory | None = None,
    tags: str | None = None,
    status: DocumentStatus | None = None,
    filing_scope_id: str | None = None,
    sort_by: str = Query("relevance", pattern="^(relevance|date|amount|title|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SearchResponse:
    """Volltextsuche mit Metadatenfiltern und Facetten."""
    try:
        result = await search_documents(
            session=db,
            query=q,
            document_types=[t.value for t in parse_document_types(document_type)],
            date_from=date_from,
            date_to=date_to,
            amount_min=amount_min,
            amount_max=amount_max,
            issuer=issuer,
            tax_relevant=tax_relevant,
            tax_year=tax_year,
            tax_category=tax_category.value if tax_category else None,
            tags=tags,
            status=status.value if status else None,
            filing_scope_id=filing_scope_id,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
        )
    except InvalidSearchQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SearchResponse(
        results=[SearchResultItem(**item) for item in result["results"]],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        facets=SearchFacets(**result["facets"]),
    )


@router.get("/search/suggest", response_model=SuggestResponse)
async def search_suggest(
    q: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SuggestResponse:
    """Autocomplete-Vorschlaege fuer die Suche."""
    suggestions = await suggest(db, q)
    return SuggestResponse(suggestions=suggestions)


# === Saved Searches ===


@router.post("/saved-searches", response_model=SavedSearchResponse, status_code=201)
async def create_saved_search(
    data: SavedSearchCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SavedSearchResponse:
    """Speichert eine Suchanfrage."""
    saved = SavedSearch(
        name=data.name,
        query_params=json.dumps(data.query_params, ensure_ascii=False),
    )
    db.add(saved)
    await db.flush()
    return SavedSearchResponse.model_validate(saved)


@router.get("/saved-searches", response_model=list[SavedSearchResponse])
async def list_saved_searches(
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[SavedSearchResponse]:
    """Listet alle gespeicherten Suchen auf."""
    result = await db.execute(
        select(SavedSearch).order_by(SavedSearch.created_at.desc())
    )
    searches = result.scalars().all()
    return [SavedSearchResponse.model_validate(s) for s in searches]


@router.delete("/saved-searches/{search_id}")
async def delete_saved_search(
    search_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Loescht eine gespeicherte Suche."""
    result = await db.execute(
        select(SavedSearch).where(SavedSearch.id == search_id)
    )
    saved = result.scalar_one_or_none()
    if saved is None:
        raise HTTPException(status_code=404, detail="Gespeicherte Suche nicht gefunden")

    await db.delete(saved)
    return {"message": "Gespeicherte Suche gelöscht"}
