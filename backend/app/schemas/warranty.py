from datetime import date

from pydantic import BaseModel


class WarrantyListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    document_id: str
    product_name: str
    product_category: str | None = None
    purchase_date: date
    warranty_end_date: date
    warranty_type: str
    warranty_duration_months: int
    retailer: str | None = None
    is_expired: bool
    reminder_sent: bool
    notes: str | None = None
    document_title: str = ""
    document_thumbnail: str | None = None
    days_remaining: int = 0


class WarrantyUpdate(BaseModel):
    warranty_end_date: date | None = None
    warranty_duration_months: int | None = None
    warranty_type: str | None = None
    product_category: str | None = None
    notes: str | None = None


class WarrantyStats(BaseModel):
    total: int
    active: int
    expiring_soon: int  # < 90 Tage
    expired: int
