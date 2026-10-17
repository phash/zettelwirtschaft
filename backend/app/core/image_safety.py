"""S-LOW3: Decompression-Bomb-Schutz fuer alle Pillow-Pfade (Thumbnail + OCR).

Pillow behandelt ``Image.MAX_IMAGE_PIXELS`` nur als *Warn*-Schwelle und wirft
``DecompressionBombError`` erst ab dem Doppelten. Ein Mini-File mit z.B. 100 Mpx
wuerde also nur eine Warnung erzeugen und danach voll dekomprimiert — auf der
8-GB-Zielhardware ein DoS. ``open_image`` prueft deshalb selbst hart gegen das
Limit, bevor Pixeldaten geladen werden (``Image.open`` liest nur den Header).
"""

from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

MAX_IMAGE_PIXELS = 64_000_000

# Pillow-eigene Pruefung auf denselben Wert setzen: oberhalb davon warnt Pillow,
# ab 2x wirft es selbst — unser harter Check unten greift schon beim 1x-Wert.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def open_image(path: Path | str) -> Image.Image:
    """Oeffnet ein Bild und wirft ``DecompressionBombError`` ueber dem Pixel-Limit.

    Als Context-Manager verwendbar (``with open_image(p) as img:``).
    """
    img = Image.open(path)
    width, height = img.size
    if width * height > MAX_IMAGE_PIXELS:
        img.close()
        raise Image.DecompressionBombError(
            f"Bild zu groß ({width}x{height} = {width * height} Pixel, "
            f"Limit {MAX_IMAGE_PIXELS})"
        )
    return img


# Modi, die Thumbnail (PNG) und Tesseract direkt verarbeiten koennen.
_SAFE_MODES = {"RGB", "RGBA", "L"}


def normalize_image(img: Image.Image) -> Image.Image:
    """Dreht ein geoeffnetes Bild gemaess EXIF-Orientation und konvertiert es
    nach RGB/RGBA/L. Ergebnis ist vollstaendig geladen und unabhaengig von
    ``img`` (auch wenn ``img`` ein Frame einer Bildsequenz ist).
    """
    transposed = ImageOps.exif_transpose(img)
    mode = transposed.mode
    if mode in _SAFE_MODES:
        result = transposed.copy() if transposed is img else transposed
    elif mode in ("PA", "LA") or (mode == "P" and "transparency" in transposed.info):
        result = transposed.convert("RGBA")
    elif mode in ("1", "I", "I;16", "F"):
        result = transposed.convert("L")
    else:  # CMYK, YCbCr, P ohne Transparenz, ...
        result = transposed.convert("RGB")
    result.load()
    return result


def load_normalized(path: Path | str) -> Image.Image:
    """Laedt ein Bild sicher (Pixel-Limit), dreht es gemaess EXIF-Orientation
    und konvertiert es nach RGB/RGBA/L.

    Smartphone-Fotos speichern die Drehung nur als EXIF-Tag — ohne
    ``exif_transpose`` landen Thumbnail und OCR-Eingabe seitlich. CMYK-JPEGs
    (Scanner, Druckvorlagen) lassen sich nicht als PNG speichern; P/1/I;16
    stoeren Tesseract. Das zurueckgegebene Bild ist vollstaendig geladen und
    unabhaengig von der Datei (kein offener File-Handle).
    """
    with open_image(path) as img:
        return normalize_image(img)


def iter_normalized_frames(path: Path | str, max_frames: int) -> Iterator[Image.Image]:
    """Wie ``load_normalized``, aber fuer alle Frames (mehrseitige TIFF-Scans).

    Liefert hoechstens ``max_frames`` Frames nacheinander (nie alle gleichzeitig
    im Speicher); jeder Frame wird einzeln gegen das Pixel-Limit geprueft.
    """
    with open_image(path) as img:
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            if index >= max_frames:
                break
            width, height = frame.size
            if width * height > MAX_IMAGE_PIXELS:
                raise Image.DecompressionBombError(
                    f"Seite {index + 1} zu groß ({width}x{height} Pixel, Limit {MAX_IMAGE_PIXELS})"
                )
            yield normalize_image(frame)
