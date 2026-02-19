import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.saved_search import SavedSearch
from app.schemas.search import (
    SavedSearchCreate,
    SavedSearchResponse,
    SearchFacets,
    SearchResponse,
    SearchResultItem,
    SuggestResponse,
)
from app.services.search_service import search_documents, suggest

logger = logging.getLogger("zettelwirtschaft.api.search")

router = APIRouter()


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    issuer: str | None = None,
    tax_relevant: bool | None = None,
    tax_year: int | None = None,
    tax_category: str | None = None,
    tags: str | None = None,
    status: str | None = None,
    filing_scope_id: str | None = None,
    sort_by: str = "relevance",
    sort_order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Volltextsuche mit Metadatenfiltern und Facetten."""
    result = await search_documents(
        session=db,
        query=q,
        document_type=document_type,
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        issuer=issuer,
        tax_relevant=tax_relevant,
        tax_year=tax_year,
        tax_category=tax_category,
        tags=tags,
        status=status,
        filing_scope_id=filing_scope_id,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )

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
    db: AsyncSession = Depends(get_db),
) -> SuggestResponse:
    """Autocomplete-Vorschlaege fuer die Suche."""
    suggestions = await suggest(db, q)
    return SuggestResponse(suggestions=suggestions)


# === Saved Searches ===


@router.post("/saved-searches", response_model=SavedSearchResponse)
async def create_saved_search(
    data: SavedSearchCreate,
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Loescht eine gespeicherte Suche."""
    result = await db.execute(
        select(SavedSearch).where(SavedSearch.id == search_id)
    )
    saved = result.scalar_one_or_none()
    if saved is None:
        raise HTTPException(status_code=404, detail="Gespeicherte Suche nicht gefunden")

    await db.delete(saved)
    return {"message": "Gespeicherte Suche geloescht"}
