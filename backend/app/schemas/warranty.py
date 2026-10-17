from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.models.warranty_info import WarrantyType


class WarrantyListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    document_id: str
    product_name: str
    product_category: str | None = None
    purchase_date: date
    warranty_end_date: date
    warranty_type: WarrantyType
    warranty_duration_months: int
    retailer: str | None = None
    is_expired: bool
    reminder_sent: bool
    notes: str | None = None
    document_title: str = ""
    days_remaining: int = 0


# Pflichtspalten (NOT NULL) — ein explizites null wuerde sonst erst beim
# Flush als IntegrityError (500) auffallen.
_NON_NULLABLE_FIELDS = ("warranty_end_date", "warranty_duration_months", "warranty_type")


class WarrantyUpdate(BaseModel):
    warranty_end_date: date | None = None
    warranty_duration_months: int | None = Field(None, ge=1, le=600)
    warranty_type: WarrantyType | None = None
    product_category: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _reject_explicit_null(self):
        for name in _NON_NULLABLE_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"'{name}' darf nicht leer sein")
        return self


class WarrantyStats(BaseModel):
    total: int
    active: int  # Enddatum >= heute
    expiring_soon: int  # heute <= Enddatum <= heute + 90 Tage
    expired: int  # Enddatum < heute
