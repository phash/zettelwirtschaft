"""S-LOW3: Decompression-Bomb-Schutz fuer alle Pillow-Pfade (Thumbnail + OCR).

Pillow behandelt ``Image.MAX_IMAGE_PIXELS`` nur als *Warn*-Schwelle und wirft
``DecompressionBombError`` erst ab dem Doppelten. Ein Mini-File mit z.B. 100 Mpx
wuerde also nur eine Warnung erzeugen und danach voll dekomprimiert — auf der
8-GB-Zielhardware ein DoS. ``open_image`` prueft deshalb selbst hart gegen das
Limit, bevor Pixeldaten geladen werden (``Image.open`` liest nur den Header).
"""

from pathlib import Path

from PIL import Image

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
            f"Bild zu gross ({width}x{height} = {width * height} Pixel, "
            f"Limit {MAX_IMAGE_PIXELS})"
        )
    return img
