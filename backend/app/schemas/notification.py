from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    type: str
    title: str
    message: str
    document_id: str | None = None
    is_read: bool
    created_at: datetime


class NotificationCount(BaseModel):
    unread: int
