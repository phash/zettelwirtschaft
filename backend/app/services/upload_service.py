import asyncio
import logging
import shutil
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.file_utils import generate_stored_filename, get_file_extension, normalize_file_type
from app.models.processing_job import JobSource, JobStatus, ProcessingJob, SplitMode
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
    split_mode: SplitMode = SplitMode.AUTO,
) -> ProcessingJob:
    """Legt den PENDING-Verarbeitungsauftrag fuer eine schon validierte Datei an
    (gemeinsam fuer Upload, Watch-Ordner und E-Mail-Anhaenge).

    ``split_mode=FORCE``: Upload-Option "Enthaelt mehrere Dokumente" — PDFs ab
    zwei Seiten gehen vor der Analyse in den Aufteilungs-Editor."""
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
        split_mode=split_mode.value,
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


def is_job_file_location(file_path: str | Path, settings: Settings) -> bool:
    """True, wenn der Pfad in einem Ablageort fuer Job-Dateien liegt: UPLOAD_DIR
    (Uploads, Watch-Ordner-/E-Mail-Kopien) oder ``data/split/`` (Stapel- und
    Teildateien der Aufteilung)."""
    path = Path(file_path).resolve()
    return any(
        path.is_relative_to(Path(root).resolve())
        for root in (settings.UPLOAD_DIR, settings.split_dir)
    )


def remove_job_file(file_path: str | Path, settings: Settings) -> bool:
    """Loescht die Arbeitskopie eines Jobs — nie eine Datei ausserhalb von
    UPLOAD_DIR bzw. ``data/split/`` (Job-Pfade stammen aus der DB). True, wenn
    danach weg. Aufrufer muessen Quellen aktiver Stapel vorher ausnehmen
    (split_service.protected_source_paths)."""
    try:
        path = Path(file_path).resolve()
        if not is_job_file_location(path, settings):
            logger.warning("Job-Datei liegt ausserhalb der Job-Ablage, nicht geloescht: %s", path)
            return False
        path.unlink(missing_ok=True)
        prune_empty_split_dir(path, settings)
        return True
    except OSError:
        logger.warning("Upload-Kopie konnte nicht entfernt werden: %s", file_path, exc_info=True)
        return False


def prune_empty_split_dir(file_path: str | Path, settings: Settings) -> None:
    """Leeren Stapel-Ordner ``data/split/<id>/`` entfernen, nachdem seine
    letzte Datei geloescht wurde — z.B. bei der automatischen Pruefung, die
    nur EIN Dokument fand: der Stapel ist dann geloescht, die Datei laeuft
    aus ``data/split/<id>/source.pdf`` durch die normale Pipeline."""
    try:
        parent = Path(file_path).resolve().parent
        if parent.parent == Path(settings.split_dir).resolve() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        logger.debug("Stapel-Ordner nicht entfernt: %s", file_path, exc_info=True)


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
    split_mode: SplitMode = SplitMode.AUTO,
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
        return await create_job(
            dest_path, stored_name, original_name, written, source, db, split_mode=split_mode,
        )
    except BaseException:
        dest_path.unlink(missing_ok=True)
        raise
