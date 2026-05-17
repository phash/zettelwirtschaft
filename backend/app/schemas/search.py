import json
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator


class SearchResultItem(BaseModel):
    id: str
    title: str
    document_type: str
    document_date: date | None = None
    amount: Decimal | None = None

    @field_serializer("amount")
    def _ser_amount(self, v):
        return float(v) if v is not None else None
    currency: str = "EUR"
    issuer: str | None = None
    thumbnail_path: str | None = None
    tags: list[str] = []
    tax_relevant: bool = False
    highlight: str = ""
    relevance_score: float = 0.0
    ai_confidence: float = 0.0
    created_at: datetime


class SearchFacets(BaseModel):
    document_types: dict[str, int] = {}
    years: dict[str, int] = {}
    top_issuers: dict[str, int] = {}
    filing_scopes: dict[str, int] = {}


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int
    page: int
    page_size: int
    facets: SearchFacets


class SuggestResponse(BaseModel):
    suggestions: list[str]


class SavedSearchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    query_params: dict


class SavedSearchResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    name: str
    query_params: dict | str
    created_at: datetime

    @field_validator("query_params", mode="before")
    @classmethod
    def parse_query_params(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return v
        return v
