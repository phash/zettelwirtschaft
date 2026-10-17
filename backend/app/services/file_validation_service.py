import logging
from pathlib import Path

from app.config import Settings
from app.core.file_utils import get_file_extension, normalize_file_type, validate_magic_bytes

logger = logging.getLogger(__name__)


class FileValidationError(Exception):
    """Fehler bei der Datei-Validierung."""

    def __init__(self, message: str, filename: str = ""):
        self.message = message
        self.filename = filename
        super().__init__(message)


def is_allowed_file_type(ext: str, settings: Settings) -> bool:
    """True wenn die Extension (inkl. Alias wie .tif -> tiff) erlaubt ist."""
    allowed = {normalize_file_type(t) for t in settings.allowed_file_types_list}
    return normalize_file_type(ext) in allowed


def validate_file_metadata(original_name: str, file_size: int | None, settings: Settings) -> None:
    """Prueft Extension und (falls bekannt) Groesse — ohne den Dateiinhalt.

    Upload-Pfad: vor dem Schreiben auf die Platte aufrufen, damit unzulaessige
    oder zu grosse Dateien gar nicht erst gespeichert werden.
    """
    ext = get_file_extension(original_name)

    # Extension pruefen
    if not is_allowed_file_type(ext, settings):
        raise FileValidationError(
            f"Dateityp '.{ext}' nicht erlaubt. Erlaubt: {', '.join(settings.allowed_file_types_list)}",
            filename=original_name,
        )

    # Groesse pruefen
    if file_size is not None and file_size > max_upload_bytes(settings):
        size_mb = file_size / (1024 * 1024)
        raise FileValidationError(
            f"Datei zu groß ({size_mb:.1f} MB). Maximum: {settings.MAX_UPLOAD_SIZE_MB} MB",
            filename=original_name,
        )


def max_upload_bytes(settings: Settings) -> int:
    return settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def validate_file(
    path: Path,
    original_name: str,
    file_size: int,
    settings: Settings,
) -> None:
    """Validiert eine Datei (Extension, Groesse, Magic Bytes).

    Raises FileValidationError bei Problemen.
    """
    validate_file_metadata(original_name, file_size, settings)

    # Magic Bytes pruefen
    ext = get_file_extension(original_name)
    if not validate_magic_bytes(path, ext):
        raise FileValidationError(
            f"Dateiinhalt stimmt nicht mit Dateityp '.{ext}' überein",
            filename=original_name,
        )

    logger.debug("Datei validiert: %s (%s, %d bytes)", original_name, ext, file_size)
