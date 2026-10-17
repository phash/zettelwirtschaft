import asyncio
import logging
import shutil
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.file_utils import generate_stored_filename, get_file_extension, normalize_file_type
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.services.file_validation_service import (
    FileValidationError,
    max_upload_bytes,
    validate_file,
    validate_file_metadata,
)

logger = logging.getLogger(__name__)

_COPY_CHUNK = 1024 * 1024


def _upload_destination(original_name: str, settings: Settings) -> tuple[str, Path]:
    """Eindeutiger Dateiname + Zielpfad in UPLOAD_DIR."""
    stored_name = generate_stored_filename(original_name)
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return stored_name, upload_dir / stored_name


async def create_job(
    dest_path: Path,
    stored_name: str,
    original_name: str,
    file_size: int,
    source: JobSource,
    db: AsyncSession,
    *,
    email_account_id: int | None = None,
) -> ProcessingJob:
    """Legt den PENDING-Verarbeitungsauftrag fuer eine schon validierte Datei an
    (gemeinsam fuer Upload, Watch-Ordner und E-Mail-Anhaenge)."""
    job = ProcessingJob(
        original_filename=original_name,
        stored_filename=stored_name,
        file_path=str(dest_path),
        # Kanonischer Typ (".tif" -> "tiff"), damit Thumbnail/OCR ihn erkennen.
        file_type=normalize_file_type(get_file_extension(original_name)),
        file_size_bytes=file_size,
        source=source,
        status=JobStatus.PENDING,
        email_account_id=email_account_id,
    )
    db.add(job)
    await db.flush()

    logger.info(
        "Dokument eingereicht: %s -> %s (Job %s, Quelle: %s)",
        original_name,
        stored_name,
        job.id,
        source.value,
    )
    return job


def remove_upload_copy(file_path: str | Path, settings: Settings) -> bool:
    """Loescht die Upload-Kopie eines Jobs — nie eine Datei ausserhalb von
    UPLOAD_DIR (Job-Pfade stammen aus der DB). True, wenn danach weg."""
    try:
        path = Path(file_path).resolve()
        if not path.is_relative_to(Path(settings.UPLOAD_DIR).resolve()):
            logger.warning("Job-Datei liegt ausserhalb von UPLOAD_DIR, nicht geloescht: %s", path)
            return False
        path.unlink(missing_ok=True)
        return True
    except OSError:
        logger.warning("Upload-Kopie konnte nicht entfernt werden: %s", file_path, exc_info=True)
        return False


def discard_upload(job: ProcessingJob) -> None:
    """Entfernt die Upload-Kopie eines Jobs, dessen Commit gescheitert ist."""
    try:
        Path(job.file_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Upload-Kopie konnte nicht entfernt werden: %s", job.file_path, exc_info=True)


async def process_upload(
    file_path: Path,
    original_name: str,
    file_size: int,
    source: JobSource,
    settings: Settings,
    db: AsyncSession,
) -> ProcessingJob:
    """Verarbeitet eine vorhandene Datei (Watch-Ordner, E-Mail-Anhang):
    Validierung, Kopie nach UPLOAD_DIR, Queue-Eintrag.

    Raises:
        FileValidationError bei ungueltiger Datei
    """
    validate_file(file_path, original_name, file_size, settings)

    stored_name, dest_path = _upload_destination(original_name, settings)

    # M4: IMMER kopieren (auch Watch-Ordner) und die Quelldatei erst NACH
    # erfolgreichem DB-Commit durch den Aufrufer loeschen bzw. verschieben —
    # rollback-sicher, analog zur "copy+delete"-Disziplin im archive_service.
    # T17: copy2 in to_thread, damit grosse Dateien den Event-Loop nicht blockieren.
    await asyncio.to_thread(shutil.copy2, str(file_path), str(dest_path))

    try:
        return await create_job(dest_path, stored_name, original_name, file_size, source, db)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise


def _stream_to_file(fileobj: BinaryIO, dest_path: Path, limit: int) -> int | None:
    """Kopiert blockweise; bricht ab sobald ``limit`` Bytes ueberschritten sind.

    Returns: geschriebene Bytes, oder None bei Ueberschreitung.
    """
    written = 0
    with open(dest_path, "wb") as out:
        while chunk := fileobj.read(_COPY_CHUNK):
            written += len(chunk)
            if written > limit:
                return None
            out.write(chunk)
    return written


async def process_upload_stream(
    fileobj: BinaryIO,
    original_name: str,
    declared_size: int | None,
    source: JobSource,
    settings: Settings,
    db: AsyncSession,
) -> ProcessingJob:
    """Verarbeitet einen HTTP-Upload: Datei direkt nach UPLOAD_DIR streamen,
    dort validieren, Queue-Eintrag anlegen.

    Extension und angegebene Groesse werden VOR dem Schreiben geprueft; die
    tatsaechlich geschriebene Groesse wird beim Kopieren begrenzt (die
    Content-Length eines Multipart-Teils ist nicht verbindlich). Bei einem
    Fehler wird die geschriebene Datei wieder entfernt.

    Raises:
        FileValidationError bei ungueltiger Datei
    """
    validate_file_metadata(original_name, declared_size, settings)

    stored_name, dest_path = _upload_destination(original_name, settings)
    try:
        written = await asyncio.to_thread(
            _stream_to_file, fileobj, dest_path, max_upload_bytes(settings),
        )
        if written is None:
            raise FileValidationError(
                f"Datei zu groß. Maximum: {settings.MAX_UPLOAD_SIZE_MB} MB",
                filename=original_name,
            )
        validate_file(dest_path, original_name, written, settings)
        return await create_job(dest_path, stored_name, original_name, written, source, db)
    except BaseException:
        dest_path.unlink(missing_ok=True)
        raise
