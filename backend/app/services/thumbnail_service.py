import asyncio
import logging
from pathlib import Path

from app.config import Settings

logger = logging.getLogger("zettelwirtschaft.thumbnail")


def _generate_image_thumbnail(
    source_path: Path,
    output_path: Path,
    max_size: int,
) -> Path:
    """Generiert ein Thumbnail aus einem Bild."""
    from PIL import Image

    with Image.open(source_path) as img:
        img.thumbnail((max_size, max_size))
        img.save(output_path, "PNG")
    return output_path


def _generate_pdf_thumbnail(
    source_path: Path,
    output_path: Path,
    max_size: int,
) -> Path:
    """Generiert ein Thumbnail aus der ersten Seite einer PDF."""
    from pdf2image import convert_from_path
    from PIL import Image

    images = convert_from_path(str(source_path), first_page=1, last_page=1)
    if not images:
        raise ValueError("Keine Seiten in der PDF gefunden")

    img = images[0]
    img.thumbnail((max_size, max_size))
    img.save(output_path, "PNG")
    return output_path


async def generate_thumbnail(
    file_path: Path,
    file_type: str,
    job_id: str,
    settings: Settings,
) -> Path | None:
    """Generiert ein Thumbnail fuer ein Dokument.

    Returns:
        Pfad zum Thumbnail oder None bei Fehler.
    """
    thumbnail_dir = Path(settings.THUMBNAIL_DIR)
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    output_path = thumbnail_dir / f"{job_id}.png"
    max_size = settings.THUMBNAIL_MAX_SIZE

    try:
        if file_type in ("jpg", "jpeg", "png", "bmp", "tiff"):
            await asyncio.to_thread(
                _generate_image_thumbnail,
                file_path,
                output_path,
                max_size,
            )
        elif file_type == "pdf":
            await asyncio.to_thread(
                _generate_pdf_thumbnail,
                file_path,
                output_path,
                max_size,
            )
        else:
            logger.warning("Kein Thumbnail-Support fuer Dateityp: %s", file_type)
            return None

        logger.debug("Thumbnail erstellt: %s", output_path)
        return output_path

    except Exception:
        logger.exception("Fehler bei Thumbnail-Generierung fuer Job %s", job_id)
        return None
