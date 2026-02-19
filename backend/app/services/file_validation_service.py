import logging
from pathlib import Path

from app.config import Settings
from app.core.file_utils import get_file_extension, validate_magic_bytes

logger = logging.getLogger("zettelwirtschaft.validation")


class FileValidationError(Exception):
    """Fehler bei der Datei-Validierung."""

    def __init__(self, message: str, filename: str = ""):
        self.message = message
        self.filename = filename
        super().__init__(message)


def validate_file(
    path: Path,
    original_name: str,
    file_size: int,
    settings: Settings,
) -> None:
    """Validiert eine Datei (Extension, Groesse, Magic Bytes).

    Raises FileValidationError bei Problemen.
    """
    ext = get_file_extension(original_name)

    # Extension pruefen
    if ext not in settings.allowed_file_types_list:
        raise FileValidationError(
            f"Dateityp '.{ext}' nicht erlaubt. Erlaubt: {', '.join(settings.allowed_file_types_list)}",
            filename=original_name,
        )

    # Groesse pruefen
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        size_mb = file_size / (1024 * 1024)
        raise FileValidationError(
            f"Datei zu gross ({size_mb:.1f} MB). Maximum: {settings.MAX_UPLOAD_SIZE_MB} MB",
            filename=original_name,
        )

    # Magic Bytes pruefen
    if not validate_magic_bytes(path, ext):
        raise FileValidationError(
            f"Dateiinhalt stimmt nicht mit Dateityp '.{ext}' ueberein",
            filename=original_name,
        )

    logger.debug("Datei validiert: %s (%s, %d bytes)", original_name, ext, file_size)
