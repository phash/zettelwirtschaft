"""PDF-Operationen fuers Aufteilen von Stapel-Scans (pypdfium2, synchron).

Alle Funktionen sind blockierend und werden aus async-Code ueber
``asyncio.to_thread`` aufgerufen. pdfium ist nicht thread-safe: jede Funktion
oeffnet und schliesst ihr Dokument unter einem modulweiten Lock (billige
Absicherung — gerendert wird nur im einzigen Queue-Worker; die API liest
hoechstens die Seitenzahl bzw. schreibt beim Bestaetigen die Teildateien, was
nur Seiten kopiert).

Schneiden ist verlustfrei: ``import_pages`` uebernimmt die Seitenobjekte samt
Bildstroemen (CCITT/JPEG) unveraendert, es wird nichts neu kodiert.
"""

import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.image_safety import MAX_IMAGE_PIXELS

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

# Binarisierung fuer die Tinten-Messung (Graustufe < Schwelle = dunkel).
_INK_THRESHOLD = 128
# Rand (Anteil je Seite), der fuer die Tinte ignoriert wird: Scannerkanten.
_INK_MARGIN = 0.03
# Linker Randstreifen mit Druckerei-/Sendungscodes (DataMatrix + Zaehler).
_INK_LEFT_STRIP = 0.06
# Oberer Bereich fuer top_ink (Briefkopf/Anschrift).
_TOP_FRACTION = 0.15


class PdfSplitError(Exception):
    """PDF kann nicht gelesen oder geschnitten werden (verschluesselt/defekt)."""


@dataclass(frozen=True)
class PageGeom:
    w: float  # Breite in pt (ungedreht)
    h: float  # Hoehe in pt
    rot: int  # /Rotate in Grad (0/90/180/270)


def _open(path: Path):
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise PdfSplitError(f"PDF kann nicht geöffnet werden: {exc}") from exc
    except OSError as exc:
        raise PdfSplitError(f"PDF kann nicht gelesen werden: {exc}") from exc


def page_count(path: Path) -> int:
    """Seitenzahl. Raises PdfSplitError."""
    with _LOCK:
        pdf = _open(path)
        try:
            return len(pdf)
        finally:
            pdf.close()


def pdf_page_geometry(path: Path) -> list[PageGeom]:
    """Groesse und Drehung jeder Seite (schnell, ohne Rendern)."""
    with _LOCK:
        pdf = _open(path)
        try:
            out = []
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    w, h = page.get_size()
                    out.append(PageGeom(w=round(float(w), 2), h=round(float(h), 2), rot=int(page.get_rotation())))
                finally:
                    page.close()
            return out
        finally:
            pdf.close()


def _render_scale(w_pts: float, h_pts: float, dpi: float, max_px: int | None) -> float:
    """Skalierung fuer pdfium (1.0 = 72 dpi), begrenzt auf MAX_IMAGE_PIXELS
    bzw. ``max_px`` als laengste Kante — pdfium hat keinen Timeout, die
    Render-Groesse ist die einzige Bremse."""
    scale = dpi / 72.0
    if max_px:
        scale = min(scale, max_px / max(w_pts, h_pts, 1.0))
    pixels = (w_pts * scale) * (h_pts * scale)
    if pixels > MAX_IMAGE_PIXELS:
        scale *= math.sqrt(MAX_IMAGE_PIXELS / pixels)
    return max(scale, 0.01)


def render_page(path: Path, n: int, dpi: float = 50, max_px: int | None = None, grayscale: bool = True):
    """Rendert Seite ``n`` (1-basiert) als PIL-Image (Modus L bzw. RGB).
    Raises PdfSplitError."""
    import pypdfium2 as pdfium

    with _LOCK:
        pdf = _open(path)
        try:
            if not 1 <= n <= len(pdf):
                raise PdfSplitError(f"Seite {n} existiert nicht")
            page = pdf[n - 1]
            try:
                w, h = page.get_size()
                try:
                    bitmap = page.render(scale=_render_scale(w, h, dpi, max_px), grayscale=grayscale)
                except pdfium.PdfiumError as exc:
                    # Defekter Seiteninhalt: wie ein defektes PDF behandeln (AUTO
                    # laeuft normal weiter) statt den Job per Retry scheitern zu lassen.
                    raise PdfSplitError(f"Seite {n} kann nicht gerendert werden: {exc}") from exc
                try:
                    image = bitmap.to_pil()
                    image.load()
                    return image.convert("L" if grayscale else "RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            pdf.close()


def page_ink_stats(image) -> tuple[float, float]:
    """(ink, top_ink): Anteil dunkler Pixel der Seite bzw. der oberen 15 %.

    Ignoriert 3 % Rand ringsum und den linken Randstreifen (x < 6 %), in dem
    Einzugsscanner-Sendungen Druckerei-Codes tragen. Ueber ``histogram()`` —
    kein numpy.
    """
    gray = image.convert("L")
    width, height = gray.size
    left = max(int(width * _INK_LEFT_STRIP), int(width * _INK_MARGIN))
    top = int(height * _INK_MARGIN)
    right = width - int(width * _INK_MARGIN)
    bottom = height - int(height * _INK_MARGIN)
    if right <= left or bottom <= top:
        return 0.0, 0.0

    def dark_ratio(box) -> float:
        histogram = gray.crop(box).histogram()[:256]
        total = sum(histogram)
        return sum(histogram[:_INK_THRESHOLD]) / total if total else 0.0

    top_bottom = top + max(1, int(height * _TOP_FRACTION))
    return (
        round(dark_ratio((left, top, right, bottom)), 5),
        round(dark_ratio((left, top, right, min(top_bottom, bottom))), 5),
    )


LEFT_STRIP = _INK_LEFT_STRIP


def left_strip_ink(image) -> float:
    """Anteil dunkler Pixel im linken Randstreifen (x < 6 %, ohne 3 % oben/
    unten) — nur wenn dort etwas steht, lohnt die OCR des Randcodes."""
    gray = image.convert("L")
    width, height = gray.size
    right = max(1, int(width * _INK_LEFT_STRIP))
    top = int(height * _INK_MARGIN)
    bottom = height - top
    if bottom <= top:
        return 0.0
    histogram = gray.crop((0, top, right, bottom)).histogram()[:256]
    total = sum(histogram)
    return round(sum(histogram[:_INK_THRESHOLD]) / total, 5) if total else 0.0


def _write_tmp(pdfium, src: Path, page_indices: list[int], tmp: Path) -> None:
    source = _open(src)
    try:
        if any(not 1 <= n <= len(source) for n in page_indices):
            raise PdfSplitError("Seitenangabe außerhalb des Dokuments")
        part = pdfium.PdfDocument.new()
        try:
            part.import_pages(source, pages=[n - 1 for n in page_indices])
            with open(tmp, "wb") as handle:
                part.save(handle, version=source.get_version())
                handle.flush()
                os.fsync(handle.fileno())
        except pdfium.PdfiumError as exc:
            raise PdfSplitError(f"Teil konnte nicht geschrieben werden: {exc}") from exc
        finally:
            part.close()
    finally:
        source.close()


def self_test() -> str:
    """Prueft, ob pdfium geladen werden kann und schneiden funktioniert (im
    Speicher, ohne Dateien): neues 3-seitiges PDF -> Seiten 2+3 per
    ``import_pages`` uebernehmen -> speichern -> erneut oeffnen.

    Fuer den Native-Build (``zettelwirtschaft-backend.exe --self-test-pdfium``):
    eine fehlende/inkompatible ``pdfium.dll`` oder fehlende Paketdaten
    (``version.json``) fallen so beim Bauen auf statt erst bei der ersten
    Aufteilung. Returns: pdfium-Version. Raises: PdfSplitError.
    """
    import io

    try:
        import pypdfium2 as pdfium
        import pypdfium2_raw  # noqa: F401 — laedt pdfium.dll
    except Exception as exc:  # ImportError, OSError (DLL), JSON-Fehler
        raise PdfSplitError(f"pypdfium2 kann nicht geladen werden: {exc}") from exc

    with _LOCK:
        try:
            source = pdfium.PdfDocument.new()
            part = pdfium.PdfDocument.new()
            try:
                for _ in range(3):
                    source.new_page(595, 842).close()
                part.import_pages(source, pages=[1, 2])
                buffer = io.BytesIO()
                part.save(buffer)
            finally:
                part.close()
                source.close()
            check = pdfium.PdfDocument(buffer.getvalue())
            try:
                pages = len(check)
            finally:
                check.close()
        except pdfium.PdfiumError as exc:
            raise PdfSplitError(f"pdfium-Selbsttest fehlgeschlagen: {exc}") from exc
    if pages != 2:
        raise PdfSplitError(f"pdfium-Selbsttest: {pages} statt 2 Seiten")
    return str(getattr(pdfium, "PDFIUM_INFO", "pdfium"))


def write_part(src: Path, page_indices: list[int], dst: Path) -> int:
    """Schreibt die Seiten ``page_indices`` (1-basiert, in dieser Reihenfolge)
    verlustfrei nach ``dst``.

    tmp-Datei -> erneut oeffnen und Seitenzahl pruefen -> fsync ->
    ``os.replace``: ``dst`` existiert danach entweder vollstaendig oder gar
    nicht. Returns: Dateigroesse in Bytes.
    """
    import pypdfium2 as pdfium

    if not page_indices:
        raise PdfSplitError("Ein Teil braucht mindestens eine Seite")
    tmp = dst.with_name(dst.name + ".tmp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        try:
            _write_tmp(pdfium, src, page_indices, tmp)
            check = _open(tmp)
            try:
                written = len(check)
            finally:
                check.close()
            if written != len(page_indices):
                raise PdfSplitError(f"Teil hat {written} statt {len(page_indices)} Seiten")
            os.replace(tmp, dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return dst.stat().st_size
