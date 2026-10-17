import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.public_errors import internal_error_message
from app.models.document import ReviewStatus
from app.models.email_account import EmailAccount
from app.models.processing_job import JOB_ERROR_DUPLICATE, JobStatus, ProcessingJob
from app.models.review_question import ReviewQuestion
from app.services.analysis_service import analyze_document, apply_source_review_policy
from app.services.archive_service import (
    ArchiveDataError,
    DuplicateDocumentError,
    archive_document,
    check_duplicate,
    remove_created_files,
)
from app.services.correction_service import apply_auto_corrections
from app.services.filing_scope_service import scopes_for_analysis
from app.services.notification_service import notify_processing_failed, notify_review_needed
from app.services.thumbnail_service import generate_thumbnail
from app.services.upload_service import remove_upload_copy

logger = logging.getLogger(__name__)

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


class _NonRetryableJobError(Exception):
    """Fehler, bei dem ein erneuter Versuch dasselbe Ergebnis liefert."""

    def __init__(self, message: str, error_code: str | None = None, document_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.document_id = document_id


async def _heartbeat(job_id: str, session: AsyncSession) -> None:
    """N-04 (Re-Review): Heartbeat-Update zwischen Verarbeitungsphasen.

    Eigene kurze, sofort committete Transaktion: frueher setzte der Heartbeat
    das Attribut und flushte nur — damit war ab dem ersten Heartbeat eine
    SQLite-Schreibtransaktion offen, die bis zum Commit NACH OCR+LLM bestehen
    blieb. Jeder andere Schreiber (Upload, PATCH, Review, E-Mail-Import) lief
    so lange in "database is locked". Aufrufer duerfen an dieser Stelle keine
    ausstehenden Aenderungen in der Session haben; alle Ergebnis-Writes
    passieren in der einen Abschluss-Transaktion nach der langsamen Arbeit.
    """
    await session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(processing_started_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def _forced_scope_id(job: ProcessingJob, session: AsyncSession) -> str | None:
    """Ablagebereich des E-Mail-Kontos, aus dem der Job stammt (falls gesetzt)."""
    if job.email_account_id is None:
        return None
    result = await session.execute(
        select(EmailAccount.filing_scope_id).where(EmailAccount.id == job.email_account_id)
    )
    return result.scalar_one_or_none()


async def _count_open_questions(document_id: str, session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(ReviewQuestion.id)).where(
            ReviewQuestion.document_id == document_id,
            ReviewQuestion.is_answered.is_(False),
        )
    )
    return result.scalar() or 0


async def _process_job(
    job: ProcessingJob,
    settings: Settings,
    session: AsyncSession,
) -> None:
    """Verarbeitet einen einzelnen Job: Thumbnail + OCR + KI-Analyse + Archivierung.

    Setzt den Endstatus (COMPLETED / NEEDS_REVIEW) und committet alle
    Ergebnisse in EINER Transaktion nach der langsamen Arbeit.

    Raises:
        _NonRetryableJobError: Duplikat oder deterministischer Datenfehler.
        Exception: alles andere — der Aufrufer wiederholt den Job.
    """
    file_path = Path(job.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {file_path}")

    # Duplikat-Check VOR Thumbnail/OCR/LLM — frueher fiel ein Duplikat erst
    # nach der kompletten Analyse (~35 s) auf, bei jedem Retry erneut.
    existing = await check_duplicate(file_path, session)
    if existing is not None:
        error = DuplicateDocumentError(existing.id, existing.title)
        raise _NonRetryableJobError(str(error), JOB_ERROR_DUPLICATE, existing.id)

    filing_scopes = await scopes_for_analysis(session)
    forced_scope_id = await _forced_scope_id(job, session)

    # Thumbnail generieren
    thumbnail_path = await generate_thumbnail(file_path, job.file_type, job.id, settings)
    await _heartbeat(job.id, session)

    # OCR + KI-Analyse — Session uebergeben damit CorrectionMappings als
    # Few-Shot-Examples in den Prompt injiziert werden koennen.
    # F-07: bei E-Mail-Body ist ocr_text vorab am Job gesetzt; analyze_document
    # ueberspringt dann die OCR-Phase.
    ocr_result, analysis_result = await analyze_document(
        file_path, job.file_type, settings,
        filing_scopes=filing_scopes, session=session,
        pre_ocr_text=job.ocr_text,
    )
    await _heartbeat(job.id, session)

    # Lerneffekt: ab 3 gleichen Benutzer-Korrekturen ersetzt die Korrektur den
    # KI-Wert deterministisch, bevor archiviert wird (nur lesend, kein Write).
    if analysis_result is not None:
        await apply_auto_corrections(session, analysis_result)
    # INJ-05: steuerrelevante E-Mail-Belege nie ungeprueft archivieren.
    apply_source_review_policy(analysis_result, job.source)

    # Ab hier: Ergebnis-Writes, ein Commit am Ende.
    if ocr_result:
        job.ocr_text = ocr_result.full_text
        job.ocr_confidence = ocr_result.average_confidence
    if analysis_result:
        job.analysis_result = json.dumps(analysis_result.to_dict(), ensure_ascii=False)

    created_files: list[Path] = []
    try:
        archived_document = await archive_document(
            file_path=file_path,
            original_filename=job.original_filename,
            stored_filename=job.stored_filename,
            file_type=job.file_type,
            file_size_bytes=job.file_size_bytes,
            ocr_result=ocr_result,
            analysis_result=analysis_result,
            settings=settings,
            session=session,
            thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
            filing_scopes=filing_scopes,
            forced_scope_id=forced_scope_id,
            created_files=created_files,
        )
    except DuplicateDocumentError as e:
        # Paralleles Duplikat (zwischen Vorab-Check und Insert archiviert)
        raise _NonRetryableJobError(str(e), JOB_ERROR_DUPLICATE, e.existing_document_id) from e
    except ArchiveDataError as e:
        raise _NonRetryableJobError(str(e)) from e

    try:
        job.document_id = archived_document.id
        # Job-Status folgt dem Dokument: auch eine reine Scope-Rueckfrage (oder ein
        # unlesbares Datum) setzt das Dokument auf NEEDS_REVIEW, obwohl die Analyse
        # selbst needs_review=False meldete.
        if archived_document.review_status == ReviewStatus.NEEDS_REVIEW:
            job.status = JobStatus.NEEDS_REVIEW
            notify_review_needed(
                session,
                archived_document.id,
                archived_document.title,
                open_questions=await _count_open_questions(archived_document.id, session),
            )
            logger.info(
                "Job %s benoetigt Review (Konfidenz: %.1f%%)",
                job.id,
                (analysis_result.confidence if analysis_result else 0.0) * 100,
            )
        else:
            job.status = JobStatus.COMPLETED

        await session.commit()
    except Exception:
        # Ohne Commit kein Dokument — die schon kopierten Archiv-/Export-Dateien
        # wuerden sonst verwaisen (auch wenn schon der Autoflush der
        # Fragen-Zaehlung scheitert).
        remove_created_files(created_files)
        raise

    logger.info("Job %s verarbeitet -> Dokument %s archiviert", job.id, archived_document.id)

    # B3: Vektorisierung NACH dem Commit (best-effort, ausserhalb der DB-Transaction).
    # Ein langsamer ChromaDB-Call darf keine SQLite-Schreibsperre halten, ein
    # Fehler darf die archive-Transaction nicht rollbacken.
    from app.services.vectorize_service import VectorizeError, vectorize_document

    try:
        await vectorize_document(archived_document, settings)
    except VectorizeError as e:
        # Erwartet bei nicht erreichbarem Ollama/Embedding-Modell — ohne Traceback.
        logger.warning("Vektorisierung nach Archivierung fehlgeschlagen fuer %s: %s", archived_document.id, e)
    except Exception:
        logger.warning(
            "Vektorisierung nach Archivierung fehlgeschlagen fuer %s",
            archived_document.id,
            exc_info=True,
        )

    # Quelldatei erst nach erfolgreichem Commit loeschen (archive_service kopiert
    # statt move). Bei Fehler bleibt sie fuer einen Retry im Upload-Ordner; bei
    # einem Duplikat entfernt sie _fail_job (FH-05).
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        logger.warning("Quelldatei konnte nicht geloescht werden: %s", file_path)


async def _fail_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
    error_message: str,
    *,
    error_code: str | None = None,
    document_id: str | None = None,
    count_attempt: bool,
    max_retries: int,
    settings: Settings | None = None,
) -> None:
    """Fehler-Bookkeeping in frischer Session (die Job-Session kann nach einer
    Exception halb-committeten Zustand enthalten).

    count_attempt=True: Retry-Zaehler erhoehen, bis MAX_RETRIES zurueck auf
    PENDING. Nicht wiederholbare Fehler (count_attempt=False) gehen sofort auf
    FAILED. Endgueltige Fehler erzeugen eine SYSTEM-Benachrichtigung.

    FH-05: Bei einem Duplikat wird die Upload-Kopie nach dem Commit geloescht
    (mit ``settings``) — sie ist byte-identisch mit dem archivierten Dokument.
    Frueher blieb sie liegen; wiederholt zugeschickte Anhaenge fuellten so die
    Datenplatte.
    """
    duplicate_copy: str | None = None
    async with session_factory() as retry_session:
        rj = (await retry_session.execute(
            select(ProcessingJob).where(ProcessingJob.id == job_id)
        )).scalar_one_or_none()
        if rj is None:
            logger.error("Job %s nach Fehler nicht mehr in DB auffindbar", job_id)
            return

        rj.retry_count += 1
        rj.error_message = error_message
        rj.error_code = error_code
        if document_id is not None:
            # Duplikat: Verweis auf das bereits archivierte Dokument (Dashboard
            # verlinkt "Vorhandenes Dokument öffnen").
            rj.document_id = document_id
        if count_attempt and rj.retry_count < max_retries:
            rj.status = JobStatus.PENDING
            rj.processing_started_at = None
            logger.warning(
                "Job %s fehlgeschlagen (Versuch %d/%d): %s",
                rj.id, rj.retry_count, max_retries, error_message,
            )
        else:
            rj.status = JobStatus.FAILED
            if error_code == JOB_ERROR_DUPLICATE:
                duplicate_copy = rj.file_path
            notify_processing_failed(
                retry_session, rj.original_filename, error_message, document_id=document_id,
            )
            logger.error(
                "Job %s endgueltig fehlgeschlagen nach %d Versuch(en): %s",
                rj.id, rj.retry_count, error_message,
            )
        await retry_session.commit()

    if duplicate_copy and settings is not None:
        remove_upload_copy(duplicate_copy, settings)


async def recover_interrupted_jobs(session: AsyncSession, settings: Settings) -> int:
    """Setzt beim Start ALLE noch PROCESSING-Jobs zurueck.

    Es gibt genau einen In-Process-Worker: ein PROCESSING-Job beim Start wurde
    durch einen Neustart (Update, Reboot, Container-Restart) unterbrochen und
    kann von niemandem mehr bearbeitet werden. Frueher wurden nur Jobs mit
    Heartbeat aelter als 10 Minuten zurueckgesetzt — juengere blieben fuer immer
    haengen. Jeder Abbruch zaehlt als Versuch; nach MAX_RETRIES wird der Job
    FAILED, damit eine Datei, die den Prozess zum Absturz bringt, nicht endlos
    neu startet. Committet selbst. Returns: Anzahl zurueckgesetzter Jobs.
    """
    result = await session.execute(
        select(ProcessingJob).where(ProcessingJob.status == JobStatus.PROCESSING)
    )
    jobs = result.scalars().all()
    for job in jobs:
        job.retry_count += 1
        job.processing_started_at = None
        if job.retry_count >= settings.MAX_RETRIES:
            job.status = JobStatus.FAILED
            job.error_message = (
                "Verarbeitung wurde mehrfach durch einen Neustart abgebrochen."
            )
            notify_processing_failed(session, job.original_filename, job.error_message)
        else:
            job.status = JobStatus.PENDING
            job.error_message = "Verarbeitung durch Neustart unterbrochen — wird wiederholt."
    await session.commit()
    if jobs:
        logger.warning("%d unterbrochene(r) Job(s) nach Neustart zurueckgesetzt", len(jobs))
    return len(jobs)


async def purge_duplicate_upload_copies(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    """FH-05: Upload-Kopien von FAILED-Duplikat-Jobs entfernen.

    Aeltere Versionen liessen sie liegen. Der Job selbst bleibt (Hinweis +
    Link aufs vorhandene Dokument). Returns: Anzahl entfernter Dateien.
    """
    async with session_factory() as session:
        paths = (await session.execute(
            select(ProcessingJob.file_path).where(
                ProcessingJob.status == JobStatus.FAILED,
                ProcessingJob.error_code == JOB_ERROR_DUPLICATE,
            )
        )).scalars().all()

    def _purge() -> int:
        removed = 0
        for path in paths:
            if Path(path).exists() and remove_upload_copy(path, settings):
                removed += 1
        return removed

    removed = await asyncio.to_thread(_purge)
    if removed:
        logger.info("%d Upload-Kopie(n) von Duplikat-Jobs entfernt", removed)
    return removed


async def run_queue_worker(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Endlos-Loop: Pollt nach PENDING-Jobs und verarbeitet sie."""
    logger.info("Queue-Worker gestartet (Poll-Intervall: %ds)", settings.QUEUE_POLL_INTERVAL)
    try:
        await purge_duplicate_upload_copies(session_factory, settings)
    except Exception:
        logger.warning("Aufraeumen der Duplikat-Upload-Kopien fehlgeschlagen", exc_info=True)

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

                # 2. Atomic claim: nur erfolgreich wenn noch PENDING.
                # B5: processing_started_at als Heartbeat (Diagnose).
                claim_result = await session.execute(
                    update(ProcessingJob)
                    .where(ProcessingJob.id == job_id)
                    .where(ProcessingJob.status == JobStatus.PENDING)
                    .values(
                        status=JobStatus.PROCESSING,
                        processing_started_at=datetime.now(timezone.utc),
                    )
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
                    logger.info("Job %s abgeschlossen (Status: %s)", job.id, job.status)

                except Exception as e:
                    # B5: Session ist nach Exception nicht reentrant ohne rollback().
                    try:
                        await session.rollback()
                    except Exception:
                        logger.exception("Rollback nach Job-Fehler fehlgeschlagen")

                    if isinstance(e, _NonRetryableJobError):
                        await _fail_job(
                            session_factory, job_id, e.message,
                            error_code=e.error_code, document_id=e.document_id,
                            count_attempt=False, max_retries=settings.MAX_RETRIES,
                            settings=settings,
                        )
                    else:
                        # Unerwartet: Details (Pfade, SQL) nur ins Log, der
                        # Job-Fehler geht per /api/jobs an den Client.
                        logger.exception("Job %s fehlgeschlagen", job_id)
                        await _fail_job(
                            session_factory, job_id,
                            internal_error_message(e),
                            count_attempt=True, max_retries=settings.MAX_RETRIES,
                        )

        except asyncio.CancelledError:
            # Der unterbrochene Job bleibt PROCESSING; recover_interrupted_jobs
            # setzt ihn beim naechsten Start zurueck.
            logger.info("Queue-Worker wird beendet")
            return
        except Exception:
            logger.exception("Unerwarteter Fehler im Queue-Worker")
            await asyncio.sleep(settings.QUEUE_POLL_INTERVAL)
