from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field

from app.models.background_task import ACTIVE_TASK_STATUSES, TaskStatus


class TaskResponse(BaseModel):
    """Status eines Hintergrund-Vorgangs (Antwort von 202-Endpoints + Polling)."""

    model_config = {"from_attributes": True}

    id: str
    kind: str
    key: str | None = None
    status: TaskStatus
    progress_percent: int | None = None
    progress_text: str | None = None
    result: Any | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @computed_field
    @property
    def done(self) -> bool:
        return self.status not in ACTIVE_TASK_STATUSES
