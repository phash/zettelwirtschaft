"""API-Router fuer E-Mail-Konten-Verwaltung."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.email_account import EmailAccount, ScheduleType
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.schemas.email import (
    EmailAccountCreate,
    EmailAccountResponse,
    EmailAccountUpdate,
    EmailFetchResult,
    EmailStatsResponse,
    EmailTestResult,
    ProcessedEmailResponse,
)
from app.services.crypto_service import decrypt_password, encrypt_password
from app.services.email_fetch_service import fetch_emails_for_account, test_imap_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email", tags=["email"])


@router.get("/accounts", response_model=list[EmailAccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).order_by(EmailAccount.name))
    return result.scalars().all()


@router.post("/accounts", response_model=EmailAccountResponse, status_code=201)
async def create_account(
    data: EmailAccountCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    key = settings.EMAIL_ENCRYPTION_KEY
    if not key:
        raise HTTPException(
            503,
            "EMAIL_ENCRYPTION_KEY ist nicht konfiguriert. "
            "Bitte in .env setzen, bevor E-Mail-Konten angelegt werden.",
        )

    account = EmailAccount(
        name=data.name,
        imap_host=data.imap_host,
        imap_port=data.imap_port,
        use_ssl=data.use_ssl,
        username=data.username,
        encrypted_password=encrypt_password(data.password, key),
        folder_inbox=data.folder_inbox,
        folder_processed=data.folder_processed,
        schedule_type=ScheduleType(data.schedule_type),
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
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "password" and value:
            key = settings.EMAIL_ENCRYPTION_KEY
            if not key:
                raise HTTPException(503, "EMAIL_ENCRYPTION_KEY ist nicht konfiguriert.")
            account.encrypted_password = encrypt_password(value, key)
        elif field == "schedule_type" and value:
            account.schedule_type = ScheduleType(value)
        elif field != "password":
            setattr(account, field, value)

    await db.flush()
    await db.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    await db.delete(account)
    await db.flush()


@router.post("/accounts/{account_id}/test", response_model=EmailTestResult)
async def test_connection(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    return await test_imap_connection(account, settings)


@router.post("/accounts/{account_id}/fetch", response_model=EmailFetchResult)
async def manual_fetch(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    stats = await fetch_emails_for_account(account, db, settings)
    # M-21: ohne expliziten Commit bleiben ProcessedEmail-Eintraege + last_checked_at
    # nicht persistent (get_db macht keinen Auto-Commit).
    await db.commit()
    return stats


@router.get("/accounts/{account_id}/history", response_model=list[ProcessedEmailResponse])
async def get_history(
    account_id: int,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    acc_result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    if not acc_result.scalar_one_or_none():
        raise HTTPException(404, "Konto nicht gefunden")

    result = await db.execute(
        select(ProcessedEmail)
        .where(ProcessedEmail.email_account_id == account_id)
        .order_by(ProcessedEmail.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/stats", response_model=list[EmailStatsResponse])
async def get_stats(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import case

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
