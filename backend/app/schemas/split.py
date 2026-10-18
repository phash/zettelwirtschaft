from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.config import SPLIT_PAGES_HARD_LIMIT, SPLIT_PARTS_HARD_LIMIT
from app.models.processing_job import JobStatus
from app.models.split_batch import SplitBatchStatus, SplitOrigin


class SplitPage(BaseModel):
    """Merkmale einer Seite — bewusst ohne Text."""

    n: int
    w: float
    h: float
    rot: int = 0
    blank: bool = False
    ink: float = 0.0
    source: str = "none"  # ocr | digital | none
    conf: float = 0.0
    # Seitenzaehler "Seite x von y", [x, y|null]
    counter: list[int | None] | None = None
    # Leerseiten: "back" = leere Rueckseite (Zaehler laeuft weiter),
    # "separator" = vermutlich Trennblatt, None = nicht eingeordnet
    blank_kind: Literal["back", "separator"] | None = None


class SplitBoundary(BaseModel):
    """Uebergang nach Seite ``after_page``."""

    after_page: int
    decision: Literal["cut", "uncertain", "none"]
    score: float | None = None
    p: float | None = None
    reasons: list[str] = []
    llm: bool = False  # KI-Pass hat diesen Uebergang bewertet


class SplitPartInfo(BaseModel):
    index: int
    pages: list[int]
    job_id: str | None = None
    job_status: JobStatus | None = None
    document_id: str | None = None
    error_message: str | None = None
    error_code: str | None = None


class SplitBatchResponse(BaseModel):
    id: str
    origin: SplitOrigin
    status: SplitBatchStatus
    version: int
    original_filename: str
    job_source: str
    source_job_id: str | None = None
    source_document_id: str | None = None
    page_count: int
    pages_done: int
    pages: list[SplitPage]
    boundaries: list[SplitBoundary]
    duplex: bool = False
    duplex_conf: float = 0.0
    partial_from: int | None = None
    llm_used: bool = False
    llm_unavailable: bool = False
    excluded: list[int] = []
    parts: list[SplitPartInfo] = []
    error_message: str | None = None
    error_code: str | None = None
    # "Aufteilung rückgängig" (DONE/DONE_WITH_ERRORS): moeglich bzw. warum nicht
    can_undo: bool = False
    undo_blocked_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    finished_at: datetime | None = None


class SplitBatchListItem(BaseModel):
    id: str
    origin: SplitOrigin
    status: SplitBatchStatus
    original_filename: str
    job_source: str
    page_count: int
    pages_done: int
    proposed_parts: int
    uncertain: int = 0
    created_at: datetime


class PaginatedSplitBatches(BaseModel):
    items: list[SplitBatchListItem]
    total: int
    page: int
    page_size: int


class SplitConfirmPart(BaseModel):
    # Obergrenze gegen ueberlange Request-Bodies (validate_parts prueft danach
    # gegen die echte Seitenzahl, hoechstens SPLIT_MAX_PAGES). Settings
    # erzwingt SPLIT_MAX_PAGES <= SPLIT_PAGES_HARD_LIMIT.
    pages: list[int] = Field(min_length=1, max_length=SPLIT_PAGES_HARD_LIMIT)


class SplitConfirmRequest(BaseModel):
    version: int
    parts: list[SplitConfirmPart] = Field(min_length=1, max_length=SPLIT_PARTS_HARD_LIMIT)
    excluded: list[int] = Field(default_factory=list, max_length=SPLIT_PAGES_HARD_LIMIT)


class SplitVersionRequest(BaseModel):
    version: int


class SplitConfirmResponse(BaseModel):
    batch_id: str
    job_ids: list[str]


class SplitRequestResponse(BaseModel):
    batch_id: str
    job_id: str


class SplitSettingsBody(BaseModel):
    """Einstellungen der automatischen Pruefung (System -> Verarbeitung).
    ``use_llm`` fehlt/None -> bleibt unveraendert."""

    auto_enabled: bool
    min_pages: int = Field(ge=2, le=1000)
    use_llm: bool | None = None


class SplitSettingsResponse(BaseModel):
    auto_enabled: bool
    min_pages: int
    use_llm: bool  # KI-Pass fuer unsichere Uebergaenge
    max_pages: int  # SPLIT_MAX_PAGES (Config) — Obergrenze fuer min_pages


class SplitUndoResponse(BaseModel):
    batch_id: str
    status: SplitBatchStatus
    removed_document_ids: list[str]
    restored_document_id: str | None = None


class SplitRetryResponse(BaseModel):
    batch_id: str
    job_ids: list[str]
