"""Pydantic-Schemas fuer E-Mail-Konten und verarbeitete E-Mails."""

from datetime import datetime

from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.email_account import ScheduleType
from app.models.processed_email import EmailStatus

CRON_REQUIRED_MESSAGE = "Für einen Zeitplan (CRON) ist ein CRON-Ausdruck erforderlich."


def normalize_cron_expression(value: str | None) -> str | None:
    """Leer -> None; sonst genau 5 Felder (Minute Stunde Tag Monat Wochentag)."""
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        return None
    if len(value.split(" ")) != 5 or not croniter.is_valid(value):
        raise ValueError(
            "Ungültiger CRON-Ausdruck — erwartet werden 5 Felder "
            "(Minute Stunde Tag Monat Wochentag), z.B. „0 8 * * *“."
        )
    return value


class EmailAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    imap_host: str = Field(..., min_length=1, max_length=500)
    imap_port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    username: str = Field(..., min_length=1, max_length=500)
    password: str = Field(..., min_length=1)
    folder_inbox: str = Field(default="INBOX", min_length=1, max_length=500)
    folder_processed: str = Field(default="Zettelwirtschaft/Verarbeitet", min_length=1, max_length=500)
    schedule_type: ScheduleType = ScheduleType.MANUAL
    cron_expression: str | None = Field(default=None, max_length=100)
    filing_scope_id: str | None = None

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, value: str | None) -> str | None:
        return normalize_cron_expression(value)

    @model_validator(mode="after")
    def _cron_required(self) -> "EmailAccountCreate":
        if self.schedule_type == ScheduleType.CRON and not self.cron_expression:
            raise ValueError(CRON_REQUIRED_MESSAGE)
        return self


# Spalten, die in der DB NOT NULL sind — ein explizites null im PUT waere ein
# IntegrityError (500). Fehlende Felder bleiben unveraendert.
_NOT_NULL_UPDATE_FIELDS = (
    "name", "imap_host", "imap_port", "use_ssl", "username", "password",
    "folder_inbox", "folder_processed", "schedule_type", "is_active",
)


class EmailAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    imap_host: str | None = Field(default=None, min_length=1, max_length=500)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    username: str | None = Field(default=None, min_length=1, max_length=500)
    password: str | None = Field(default=None, min_length=1)
    folder_inbox: str | None = Field(default=None, min_length=1, max_length=500)
    folder_processed: str | None = Field(default=None, min_length=1, max_length=500)
    schedule_type: ScheduleType | None = None
    cron_expression: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    filing_scope_id: str | None = None

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, value: str | None) -> str | None:
        return normalize_cron_expression(value)

    @model_validator(mode="after")
    def _reject_explicit_null(self) -> "EmailAccountUpdate":
        for field in _NOT_NULL_UPDATE_FIELDS:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Feld „{field}“ darf nicht leer sein.")
        return self


class EmailAccountResponse(BaseModel):
    id: int
    name: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    folder_inbox: str
    folder_processed: str
    schedule_type: ScheduleType
    cron_expression: str | None
    is_active: bool
    last_checked_at: datetime | None
    last_error: str | None
    filing_scope_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailTestResult(BaseModel):
    success: bool
    error: str | None = None
    # Erfolgreiche Verbindung, aber Hinweis (z.B. Zielordner fehlt noch).
    warning: str | None = None


class EmailFetchResult(BaseModel):
    total: int
    relevant: int
    irrelevant: int
    skipped: int
    failed: int
    # Ungelesene E-Mails ueber EMAIL_MAX_PER_RUN hinaus — folgen beim naechsten Abruf.
    remaining: int = 0
    error: str | None = None


class EmailSettingsInfo(BaseModel):
    """Feste Abruf-Regeln fuer die Hinweise in der E-Mail-Einstellungs-UI."""

    timezone: str
    unseen_only: bool
    max_per_run: int
    idle_interval_minutes: int


class ProcessedEmailResponse(BaseModel):
    id: int
    message_id: str
    subject: str | None
    sender: str | None
    received_at: datetime | None
    status: EmailStatus
    relevance_reason: str | None
    processing_job_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EmailStatsResponse(BaseModel):
    account_id: int
    account_name: str
    total_processed: int
    relevant: int
    irrelevant: int
    failed: int
    last_checked_at: datetime | None
