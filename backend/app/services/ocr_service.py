import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings

logger = logging.getLogger("zettelwirtschaft.ocr")


@dataclass
class PageText:
    page_number: int
    text: str
    confidence: float


@dataclass
class OcrResult:
    full_text: str
    pages: list[PageText] = field(default_factory=list)
    average_confidence: float = 0.0
    page_count: int = 0


def _ocr_image_sync(image, languages: str) -> tuple[str, float]:
    """Fuehrt OCR auf einem PIL-Image aus (synchron)."""
    import pytesseract
    from PIL import ImageFilter, ImageOps

    # Vorverarbeitung: Graustufen + Autokontrast
    processed = ImageOps.grayscale(image)
    processed = ImageOps.autocontrast(processed)
    processed = processed.filter(ImageFilter.SHARPEN)

    # OCR mit Detaildaten fuer Konfidenz
    data = pytesseract.image_to_data(
        processed,
        lang=languages,
        output_type=pytesseract.Output.DICT,
    )

    # Text extrahieren
    text = pytesseract.image_to_string(processed, lang=languages)

    # Durchschnittliche Konfidenz berechnen (nur Woerter mit conf > 0)
    confidences = [
        int(c) for c in data["conf"] if isinstance(c, (int, float)) and int(c) > 0
    ]
    avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0

    return text.strip(), avg_confidence


def _extract_pdf_digital_sync(file_path: Path) -> OcrResult | None:
    """Versucht Text aus einem digitalen PDF zu extrahieren (ohne OCR)."""
    import pdfplumber

    pages: list[PageText] = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(PageText(
                        page_number=i + 1,
                        text=text.strip(),
                        confidence=1.0,  # Digitaler Text hat perfekte Konfidenz
                    ))
    except Exception as e:
        logger.warning("pdfplumber konnte PDF nicht lesen: %s", e)
        return None

    if not pages:
        return None

    full_text = "\n\n".join(p.text for p in pages)
    return OcrResult(
        full_text=full_text,
        pages=pages,
        average_confidence=1.0,
        page_count=len(pages),
    )


def _extract_pdf_ocr_sync(file_path: Path, settings: Settings) -> OcrResult | None:
    """OCR fuer gescannte PDFs via pdf2image + Tesseract."""
    from pdf2image import convert_from_path

    try:
        images = convert_from_path(
            file_path,
            dpi=300,
            last_page=settings.MAX_OCR_PAGES,
        )
    except Exception as e:
        logger.error("pdf2image Konvertierung fehlgeschlagen: %s", e)
        return None

    pages: list[PageText] = []
    for i, image in enumerate(images):
        try:
            text, confidence = _ocr_image_sync(image, settings.OCR_LANGUAGES)
            if text:
                pages.append(PageText(
                    page_number=i + 1,
                    text=text,
                    confidence=confidence,
                ))
        except Exception as e:
            logger.warning("OCR fuer Seite %d fehlgeschlagen: %s", i + 1, e)

    if not pages:
        return None

    full_text = "\n\n".join(p.text for p in pages)
    total_conf = sum(p.confidence for p in pages)
    avg_conf = total_conf / len(pages) if pages else 0.0

    return OcrResult(
        full_text=full_text,
        pages=pages,
        average_confidence=avg_conf,
        page_count=len(pages),
    )


def _extract_image_ocr_sync(file_path: Path, settings: Settings) -> OcrResult | None:
    """OCR fuer Bilddateien via Tesseract."""
    from PIL import Image

    try:
        image = Image.open(file_path)
    except Exception as e:
        logger.error("Bild konnte nicht geoeffnet werden: %s", e)
        return None

    try:
        text, confidence = _ocr_image_sync(image, settings.OCR_LANGUAGES)
    except Exception as e:
        logger.error("OCR fehlgeschlagen: %s", e)
        return None

    if not text:
        return None

    page = PageText(page_number=1, text=text, confidence=confidence)
    return OcrResult(
        full_text=text,
        pages=[page],
        average_confidence=confidence,
        page_count=1,
    )


async def extract_text(
    file_path: Path,
    file_type: str,
    settings: Settings,
) -> OcrResult | None:
    """Extrahiert Text aus einem Dokument (PDF oder Bild).

    Fuer PDFs: Zuerst pdfplumber (digitaler Text), dann Tesseract (Scan).
    Fuer Bilder: Direkt Tesseract mit Vorverarbeitung.

    Returns:
        OcrResult bei Erfolg, None bei Fehler.
    """
    file_type_lower = file_type.lower()

    try:
        if file_type_lower == "pdf":
            # Zuerst digitalen Text versuchen
            result = await asyncio.to_thread(_extract_pdf_digital_sync, file_path)
            if result and result.full_text.strip():
                logger.info(
                    "Digitaler Text aus PDF extrahiert (%d Seiten, %d Zeichen)",
                    result.page_count,
                    len(result.full_text),
                )
                return result

            # Fallback: OCR fuer gescannte PDFs
            logger.info("Kein digitaler Text gefunden, starte OCR...")
            result = await asyncio.to_thread(
                _extract_pdf_ocr_sync, file_path, settings
            )
            if result:
                logger.info(
                    "OCR-Text aus PDF extrahiert (%d Seiten, Konfidenz: %.1f%%)",
                    result.page_count,
                    result.average_confidence * 100,
                )
            return result

        elif file_type_lower in ("jpg", "jpeg", "png", "tiff", "bmp"):
            result = await asyncio.to_thread(
                _extract_image_ocr_sync, file_path, settings
            )
            if result:
                logger.info(
                    "OCR-Text aus Bild extrahiert (%d Zeichen, Konfidenz: %.1f%%)",
                    len(result.full_text),
                    result.average_confidence * 100,
                )
            return result

        else:
            logger.warning("Nicht unterstuetzter Dateityp fuer OCR: %s", file_type)
            return None

    except Exception:
        logger.exception("Unerwarteter Fehler bei der Textextraktion")
        return None
