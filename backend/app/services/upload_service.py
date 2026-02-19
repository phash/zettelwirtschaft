import logging
import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.file_utils import generate_stored_filename, get_file_extension
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.services.file_validation_service import validate_file

logger = logging.getLogger("zettelwirtschaft.upload")


async def process_upload(
    file_path: Path,
    original_name: str,
    file_size: int,
    source: JobSource,
    settings: Settings,
    db: AsyncSession,
) -> ProcessingJob:
    """Verarbeitet einen Datei-Upload: Validierung, Speicherung, Queue-Eintrag.

    Args:
        file_path: Pfad zur temporaeren/originalen Datei
        original_name: Originaler Dateiname
        file_size: Dateigroesse in Bytes
        source: Quelle (UPLOAD oder WATCH_FOLDER)
        settings: App-Einstellungen
        db: Datenbank-Session

    Returns:
        ProcessingJob mit Status PENDING

    Raises:
        FileValidationError bei ungueltige Datei
    """
    # Validieren
    validate_file(file_path, original_name, file_size, settings)

    # Eindeutigen Dateinamen generieren und in UPLOAD_DIR speichern
    stored_name = generate_stored_filename(original_name)
    ext = get_file_extension(original_name)
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_path = upload_dir / stored_name

    # Datei kopieren/verschieben
    if source == JobSource.WATCH_FOLDER:
        shutil.move(str(file_path), str(dest_path))
    else:
        shutil.copy2(str(file_path), str(dest_path))

    # Queue-Eintrag erstellen
    job = ProcessingJob(
        original_filename=original_name,
        stored_filename=stored_name,
        file_path=str(dest_path),
        file_type=ext,
        file_size_bytes=file_size,
        source=source,
        status=JobStatus.PENDING,
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
