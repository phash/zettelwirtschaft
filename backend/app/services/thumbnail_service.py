import asyncio
import logging
from pathlib import Path

from app.config import Settings
from app.core.file_utils import is_image_type, is_pdf_type

# S-LOW3: Decompression-Bomb-Schutz zentral in app.core.image_safety —
# load_normalized() wirft oberhalb des Pixel-Limits, generate_thumbnail() faengt
# das als Fehler (None) ab. Zusaetzlich EXIF-Drehung + Modus-Normalisierung
# (CMYK-JPEGs liessen sich frueher nicht als PNG speichern).
from app.core.image_safety import load_normalized

logger = logging.getLogger("zettelwirtschaft.thumbnail")

# Wall-Clock-Limit (Sekunden) fuer das pdftoppm-Rendering der ersten Seite.
_PDF_RENDER_TIMEOUT_S = 60


def _generate_image_thumbnail(
    source_path: Path,
    output_path: Path,
    max_size: int,
) -> Path:
    """Generiert ein Thumbnail aus einem Bild (EXIF-gedreht, RGB/RGBA/L)."""
    img = load_normalized(source_path)
    try:
        img.thumbnail((max_size, max_size))
        img.save(output_path, "PNG")
    finally:
        img.close()
    return output_path


def _generate_pdf_thumbnail(
    source_path: Path,
    output_path: Path,
    max_size: int,
) -> Path:
    """Generiert ein Thumbnail aus der ersten Seite einer PDF."""
    from pdf2image import convert_from_path

    # size=max_size (int) -> pdftoppm -scale-to: rendert direkt in Zielgroesse
    # statt die erste Seite mit 200 dpi voll zu rastern (riesige MediaBox).
    # timeout: ein haengendes pdftoppm (praepariertes PDF) blockiert sonst den
    # Queue-Worker unbegrenzt (FH-02).
    images = convert_from_path(
        str(source_path), first_page=1, last_page=1, size=max_size,
        timeout=_PDF_RENDER_TIMEOUT_S,
    )
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
        if is_image_type(file_type):
            await asyncio.to_thread(
                _generate_image_thumbnail,
                file_path,
                output_path,
                max_size,
            )
        elif is_pdf_type(file_type):
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
