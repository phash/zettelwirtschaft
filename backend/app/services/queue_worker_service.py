import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.filing_scope import FilingScope
from app.models.processing_job import JobStatus, ProcessingJob
from app.services.analysis_service import analyze_document
from app.services.archive_service import archive_document
from app.services.thumbnail_service import generate_thumbnail

logger = logging.getLogger("zettelwirtschaft.queue_worker")

# Globales Pause-Flag (in-memory, resets on restart)
_queue_paused: bool = False


def pause_queue() -> None:
    global _queue_paused
    _queue_paused = True
    logger.info("Queue-Worker pausiert")


def resume_queue() -> None:
    global _queue_paused
    _queue_paused = False
    logger.info("Queue-Worker fortgesetzt")


def is_queue_paused() -> bool:
    return _queue_paused


async def _process_job(
    job: ProcessingJob,
    settings: Settings,
    session: AsyncSession,
) -> None:
    """Verarbeitet einen einzelnen Job: Thumbnail + OCR + KI-Analyse + Archivierung."""
    file_path = Path(job.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {file_path}")

    # Filing Scopes laden
    scope_result = await session.execute(select(FilingScope))
    scope_records = scope_result.scalars().all()
    filing_scopes = []
    for s in scope_records:
        keywords = []
        if s.keywords:
            try:
                keywords = json.loads(s.keywords)
            except (json.JSONDecodeError, TypeError):
                keywords = []
        filing_scopes.append({
            "id": s.id, "name": s.name, "slug": s.slug,
            "keywords": keywords, "is_default": s.is_default,
        })

    # Thumbnail generieren
    thumbnail_path = await generate_thumbnail(file_path, job.file_type, job.id, settings)

    # OCR + KI-Analyse
    ocr_result, analysis_result = await analyze_document(
        file_path, job.file_type, settings, filing_scopes=filing_scopes,
    )

    # OCR-Ergebnisse im Job speichern
    if ocr_result:
        job.ocr_text = ocr_result.full_text
        job.ocr_confidence = ocr_result.average_confidence

    # Analyse-Ergebnisse im Job speichern
    if analysis_result:
        job.analysis_result = json.dumps(analysis_result.to_dict(), ensure_ascii=False)

    # Archivierung: Dokument in Archiv verschieben + DB-Eintrag erstellen
    try:
        thumbnail_str = None
        if thumbnail_path:
            thumbnail_str = str(thumbnail_path)

        document = await archive_document(
            file_path=file_path,
            original_filename=job.original_filename,
            stored_filename=job.stored_filename,
            file_type=job.file_type,
            file_size_bytes=job.file_size_bytes,
            ocr_result=ocr_result,
            analysis_result=analysis_result,
            settings=settings,
            session=session,
            thumbnail_path=thumbnail_str,
            filing_scopes=filing_scopes,
        )

        if analysis_result and analysis_result.needs_review:
            job.status = JobStatus.NEEDS_REVIEW
            logger.info(
                "Job %s benoetigt Review (Konfidenz: %.1f%%)",
                job.id,
                analysis_result.confidence * 100,
            )

        logger.info(
            "Job %s verarbeitet -> Dokument %s archiviert",
            job.id,
            document.id,
        )

    except ValueError as e:
        # Duplikat erkannt
        job.status = JobStatus.NEEDS_REVIEW
        job.error_message = str(e)
        logger.warning("Job %s: %s", job.id, e)

    await session.commit()


async def run_queue_worker(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Endlos-Loop: Pollt nach PENDING-Jobs und verarbeitet sie."""
    logger.info("Queue-Worker gestartet (Poll-Intervall: %ds)", settings.QUEUE_POLL_INTERVAL)

    while True:
        try:
            if _queue_paused:
                await asyncio.sleep(settings.QUEUE_POLL_INTERVAL)
                continue

            async with session_factory() as session:
                # 1. Naechsten PENDING-Job finden (nur ID)
                result = await session.execute(
                    select(ProcessingJob.id)
                    .where(ProcessingJob.status == JobStatus.PENDING)
                    .order_by(ProcessingJob.created_at.asc())
                    .limit(1)
                )
                job_id = result.scalar_one_or_none()

                if job_id is None:
                    await asyncio.sleep(settings.QUEUE_POLL_INTERVAL)
                    continue

                # 2. Atomic claim: nur erfolgreich wenn noch PENDING
                claim_result = await session.execute(
                    update(ProcessingJob)
                    .where(ProcessingJob.id == job_id)
                    .where(ProcessingJob.status == JobStatus.PENDING)
                    .values(status=JobStatus.PROCESSING)
                )
                await session.commit()

                if claim_result.rowcount == 0:
                    continue  # Anderer Worker hat den Job uebernommen

                # 3. Vollstaendiges Job-Objekt laden
                result = await session.execute(
                    select(ProcessingJob).where(ProcessingJob.id == job_id)
                )
                job = result.scalar_one()
                logger.info("Verarbeite Job %s: %s", job.id, job.original_filename)

                try:
                    await _process_job(job, settings, session)
                    # Nur auf COMPLETED setzen wenn noch PROCESSING
                    if job.status == JobStatus.PROCESSING:
                        job.status = JobStatus.COMPLETED
                    await session.commit()
                    logger.info("Job %s abgeschlossen (Status: %s)", job.id, job.status)

                except Exception as e:
                    error_msg = str(e) or f"{type(e).__name__}: Unbekannter Fehler"
                    job.retry_count += 1
                    if job.retry_count >= settings.MAX_RETRIES:
                        job.status = JobStatus.FAILED
                        job.error_message = error_msg
                        logger.error(
                            "Job %s endgueltig fehlgeschlagen nach %d Versuchen: %s",
                            job.id,
                            job.retry_count,
                            e,
                        )
                    else:
                        job.status = JobStatus.PENDING
                        job.error_message = error_msg
                        logger.warning(
                            "Job %s fehlgeschlagen (Versuch %d/%d): %s",
                            job.id,
                            job.retry_count,
                            settings.MAX_RETRIES,
                            e,
                        )
                    await session.commit()

        except asyncio.CancelledError:
            logger.info("Queue-Worker wird beendet")
            return
        except Exception:
            logger.exception("Unerwarteter Fehler im Queue-Worker")
            await asyncio.sleep(settings.QUEUE_POLL_INTERVAL)
