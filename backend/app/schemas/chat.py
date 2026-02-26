"""Pydantic-Schemas fuer Chat Request/Response."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    filing_scope_id: str | None = None


class ChatSource(BaseModel):
    document_id: str
    title: str
    document_type: str | None = None
    document_date: str | None = None
    issuer: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource] = []
    chunks_found: int = 0


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    sources: list[ChatSource] = []
    filing_scope_id: str | None = None
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageResponse]
    total: int
