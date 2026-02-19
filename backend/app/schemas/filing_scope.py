from datetime import datetime

from pydantic import BaseModel, Field


class FilingScopeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    keywords: list[str] = []
    is_default: bool = False
    color: str | None = Field(None, max_length=7)


class FilingScopeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    keywords: list[str] | None = None
    is_default: bool | None = None
    color: str | None = Field(None, max_length=7)


class FilingScopeResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    name: str
    slug: str
    description: str | None = None
    keywords: list[str] = []
    is_default: bool
    color: str | None = None
    created_at: datetime
