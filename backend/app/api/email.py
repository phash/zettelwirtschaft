"""API-Router fuer E-Mail-Konten-Verwaltung."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db, get_session_factory
from app.models.email_account import EmailAccount, ScheduleType
from app.models.filing_scope import FilingScope
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.schemas.background_task import TaskResponse
from app.schemas.email import (
    CRON_REQUIRED_MESSAGE,
    EmailAccountCreate,
    EmailAccountResponse,
    EmailAccountUpdate,
    EmailFetchResult,
    EmailSettingsInfo,
    EmailStatsResponse,
    EmailTestResult,
    ProcessedEmailResponse,
)
from app.services.crypto_service import encrypt_password
from app.services.email_fetch_service import (
    ENCRYPTION_KEY_MISSING_MESSAGE,
    account_fetch_lock,
    fetch_emails_for_account,
    test_imap_connection,
)
from app.services.email_scheduler_service import IDLE_INTERVAL_SECONDS
from app.services.task_service import TaskContext, TaskError, start_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email", tags=["email"])


def _require_encryption_key(settings: Settings) -> str:
    key = settings.EMAIL_ENCRYPTION_KEY
    if not key:
        raise HTTPException(503, ENCRYPTION_KEY_MISSING_MESSAGE)
    return key


async def _get_account_or_404(db: AsyncSession, account_id: int) -> EmailAccount:
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    return account


async def _ensure_scope_exists(db: AsyncSession, filing_scope_id: str | None) -> None:
    if filing_scope_id is not None and await db.get(FilingScope, filing_scope_id) is None:
        raise HTTPException(422, "Ablagebereich nicht gefunden")


@router.get("/settings", response_model=EmailSettingsInfo)
async def get_email_settings(settings: Settings = Depends(get_settings)):
    """Feste Abruf-Regeln (Zeitzone fuer CRON, nur ungelesene Mails, Limit)."""
    return EmailSettingsInfo(
        timezone=str(settings.local_tz),
        unseen_only=True,
        max_per_run=settings.EMAIL_MAX_PER_RUN,
        idle_interval_minutes=IDLE_INTERVAL_SECONDS // 60,
    )


@router.get("/accounts", response_model=list[EmailAccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db, scope="function")):
    result = await db.execute(select(EmailAccount).order_by(EmailAccount.name))
    return result.scalars().all()


@router.post("/accounts", response_model=EmailAccountResponse, status_code=201)
async def create_account(
    data: EmailAccountCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    key = _require_encryption_key(settings)
    await _ensure_scope_exists(db, data.filing_scope_id)

    account = EmailAccount(
        name=data.name,
        imap_host=data.imap_host,
        imap_port=data.imap_port,
        use_ssl=data.use_ssl,
        username=data.username,
        encrypted_password=encrypt_password(data.password, key),
        folder_inbox=data.folder_inbox,
        folder_processed=data.folder_processed,
        schedule_type=data.schedule_type,
        cron_expression=data.cron_expression,
        filing_scope_id=data.filing_scope_id,
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    return account


@router.put("/accounts/{account_id}", response_model=EmailAccountResponse)
async def update_account(
    account_id: int,
    data: EmailAccountUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    account = await _get_account_or_404(db, account_id)
    changes = data.model_dump(exclude_unset=True)

    # CRON braucht einen Ausdruck — gegen den zusammengefuehrten Zustand pruefen.
    schedule_type = changes.get("schedule_type", account.schedule_type)
    cron_expression = changes.get("cron_expression", account.cron_expression)
    if schedule_type == ScheduleType.CRON and not cron_expression:
        raise HTTPException(422, CRON_REQUIRED_MESSAGE)
    if "filing_scope_id" in changes:
        await _ensure_scope_exists(db, changes["filing_scope_id"])

    for field, value in changes.items():
        if field == "password":
            account.encrypted_password = encrypt_password(value, _require_encryption_key(settings))
        else:
            setattr(account, field, value)

    await db.flush()
    await db.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db, scope="function")):
    account = await _get_account_or_404(db, account_id)
    await db.delete(account)
    await db.flush()


@router.post("/accounts/{account_id}/test", response_model=EmailTestResult)
async def test_connection(
    account_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    account = await _get_account_or_404(db, account_id)
    return await test_imap_connection(account, settings)


@router.post("/accounts/{account_id}/fetch", status_code=202, response_model=TaskResponse)
async def manual_fetch(
    account_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> TaskResponse:
    """Startet den manuellen Abruf als Hintergrund-Vorgang (HTTP 202).

    IMAP-Download plus ein LLM-Aufruf pro Mail dauern laenger als ein Request
    offen bleiben darf. Ergebnis (``EmailFetchResult``) per ``GET /api/tasks/{id}``.
    """
    await _get_account_or_404(db, account_id)
    _require_encryption_key(settings)

    async def work(ctx: TaskContext) -> dict:
        lock = account_fetch_lock(account_id)
        if lock.locked():
            await ctx.report_progress(0, "Warte auf laufenden automatischen Abruf …")
        async with lock:
            stats = await fetch_emails_for_account(
                account_id, ctx.session_factory, settings, progress=ctx.report_progress
            )
        if stats["error"]:
            raise TaskError(stats["error"])
        return EmailFetchResult(**stats).model_dump()

    task = await start_task(
        session_factory, "email_fetch", work, key=str(account_id),
        conflict_message="Für dieses Konto läuft bereits ein Abruf.",
    )
    return TaskResponse.model_validate(task)


@router.get("/accounts/{account_id}/history", response_model=list[ProcessedEmailResponse])
async def get_history(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    await _get_account_or_404(db, account_id)

    result = await db.execute(
        select(ProcessedEmail)
        .where(ProcessedEmail.email_account_id == account_id)
        .order_by(ProcessedEmail.created_at.desc(), ProcessedEmail.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/stats", response_model=list[EmailStatsResponse])
async def get_stats(db: AsyncSession = Depends(get_db, scope="function")):
    # Single query: all accounts with aggregated email stats
    stmt = (
        select(
            EmailAccount.id,
            EmailAccount.name,
            EmailAccount.last_checked_at,
            func.count(ProcessedEmail.id).label("total"),
            func.sum(case((ProcessedEmail.status == EmailStatus.RELEVANT, 1), else_=0)).label("relevant"),
            func.sum(case((ProcessedEmail.status == EmailStatus.IRRELEVANT, 1), else_=0)).label("irrelevant"),
            func.sum(case((ProcessedEmail.status == EmailStatus.FAILED, 1), else_=0)).label("failed"),
        )
        .outerjoin(ProcessedEmail, ProcessedEmail.email_account_id == EmailAccount.id)
        .group_by(EmailAccount.id)
        .order_by(EmailAccount.name)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        EmailStatsResponse(
            account_id=row.id,
            account_name=row.name,
            total_processed=row.total or 0,
            relevant=row.relevant or 0,
            irrelevant=row.irrelevant or 0,
            failed=row.failed or 0,
            last_checked_at=row.last_checked_at,
        )
        for row in rows
    ]
