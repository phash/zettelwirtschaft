import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.core.file_utils import is_image_type, is_pdf_type

# S-LOW3: Decompression-Bomb-Schutz zentral in app.core.image_safety (hartes
# Pixel-Limit beim Oeffnen, setzt auch Pillows MAX_IMAGE_PIXELS). Dort auch die
# EXIF-Drehung (Smartphone-Fotos) und die Modus-Normalisierung.
from app.core.image_safety import MAX_IMAGE_PIXELS, iter_normalized_frames

logger = logging.getLogger(__name__)

# Unter dieser Zeichenzahl gilt eine PDF-Seite als "ohne Textlayer" und wird
# per OCR gelesen (z.B. eingescannte Seite hinter einem digitalen Deckblatt;
# ein gestempelter Fusszeilentext allein reicht nicht).
_MIN_DIGITAL_PAGE_CHARS = 30

# FH-02: Grenzen fuer untrusted PDFs (Upload, Watch-Ordner, E-Mail-Anhang).
_OCR_DPI = 300
# Wall-Clock-Limit pro pdftoppm-/pdfinfo-Aufruf (Sekunden).
_POPPLER_TIMEOUT_S = 120
# Textlayer: hoechstens so viele Seiten / so lange (Sekunden) lesen.
# TODO(FH-02): Eine einzelne Seite mit riesigem Content-Stream kann
# extract_text() weiterhin minutenlang beschaeftigen — das Zeitbudget greift
# nur zwischen Seiten. Saubere Loesung: Extraktion in einem Subprozess mit
# hartem Timeout (Native braucht dafuer multiprocessing.freeze_support).
_MAX_TEXT_PAGES = 200
_TEXT_TIME_BUDGET_S = 60

# Zweiter OCR-Durchgang (Binarisierung + Schraeglagenkorrektur), wenn der
# erste schwach ist — typisch fuer verrauschte Thermobon-Scans, die im ersten
# Durchgang nur 0-14 Zeichen liefern.
_WEAK_MIN_CHARS = 40
_WEAK_MIN_CONFIDENCE = 0.6
_DESKEW_MAX_ANGLE = 5.0
_DESKEW_MIN_ANGLE = 0.3
_DESKEW_ANALYSIS_SIDE = 1000
# Zeilenhoehe (Pixel), auf die der zweite Durchgang skaliert — Tesseract liest
# Zeilen von ~20 px Hoehe (Ober- bis Unterlaenge) am zuverlaessigsten.
_TARGET_LINE_HEIGHT = 20
# Nur Woerter ab dieser Konfidenz zaehlen zur Qualitaet eines Durchgangs —
# sonst "gewinnt" ein Durchgang, der Bildrauschen als tausende Zeichen liest.
_QUALITY_MIN_WORD_CONF = 50


@dataclass
class PageText:
    # Pro Seite erkannter Text. Wird zum Zusammenfuehren gemischter PDFs
    # (digitale + gescannte Seiten in Seitenreihenfolge) und fuer die
    # zeichengewichtete Konfidenz gebraucht.
    page_number: int
    text: str
    confidence: float


@dataclass
class OcrResult:
    full_text: str
    pages: list[PageText] = field(default_factory=list)
    average_confidence: float = 0.0
    page_count: int = 0


@dataclass
class _OcrPass:
    text: str
    confidence: float
    # Konfidenz-gewichtete Zeichenzahl (Summe len(wort) * conf ueber Woerter
    # mit conf >= _QUALITY_MIN_WORD_CONF) — Mass dafuer, wie viel
    # verlaesslicher Text erkannt wurde. Vergleicht zwei Durchgaenge
    # fairer als die reine Durchschnitts-Konfidenz (3 sichere Zeichen < 300
    # halbwegs sichere).
    quality: float

    @property
    def weak(self) -> bool:
        return len(self.text) < _WEAK_MIN_CHARS or self.confidence < _WEAK_MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# Bildvorverarbeitung (Pillow-only, kein OpenCV/numpy)
# ---------------------------------------------------------------------------


def _upscale_factor(size: tuple[int, int]) -> int:
    """2x-Upscale fuer kleine Bilder — aber nur, wenn das Ergebnis unter dem
    Pixel-Limit bleibt (ein 1499x42000-Bild wuerde sonst ~252 Mpx gross)."""
    width, height = size
    if min(width, height) < 1500 and width * height * 4 <= MAX_IMAGE_PIXELS:
        return 2
    return 1


def _preprocess_for_ocr(image):
    """Pre-Processing-Pipeline fuer Tesseract.

    1. Auto-Upscale: Tesseract braucht ~300 DPI fuer gute Resultate. Bilder
       deren kuerzere Kante < 1500 px hat (typische Handy-Fotos auf Belegen)
       werden auf 2x hochskaliert mit Lanczos (sofern unter dem Pixel-Limit).
    2. Grayscale + aggressives Autocontrast (cutoff=2) — schneidet die
       extremsten 2 % schwarzer und weisser Pixel ab, dehnt den Rest auf
       0..255. Holt mehr Detail aus unterbelichteten Fotos.
    3. MedianFilter(3) — entfernt Salt-and-Pepper-Noise von schlechten Scans
       ohne Linien zu zerstoeren.
    4. Sharpen — Kanten betonen.
    """
    from PIL import Image, ImageFilter, ImageOps

    scale = _upscale_factor(image.size)
    if scale > 1:
        image = image.resize((image.size[0] * scale, image.size[1] * scale), Image.Resampling.LANCZOS)

    processed = ImageOps.grayscale(image)
    processed = ImageOps.autocontrast(processed, cutoff=2)
    processed = processed.filter(ImageFilter.MedianFilter(size=3))
    processed = processed.filter(ImageFilter.SHARPEN)
    return processed


def otsu_threshold(gray) -> int:
    """Globale Binarisierungsschwelle nach Otsu (aus dem Histogramm, 0..255)."""
    histogram = gray.histogram()[:256]
    total = sum(histogram)
    if total == 0:
        return 127
    sum_all = sum(i * h for i, h in enumerate(histogram))
    sum_bg = 0.0
    weight_bg = 0
    best_threshold, best_variance = 127, -1.0
    for t, count in enumerate(histogram):
        weight_bg += count
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * count
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_all - sum_bg) / weight_fg
        variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if variance > best_variance:
            best_threshold, best_variance = t, variance
    return best_threshold


def binarize_otsu(gray):
    """Schwarz/Weiss (Modus L, 0/255) mit globaler Otsu-Schwelle."""
    threshold = otsu_threshold(gray)
    return gray.point([0 if v <= threshold else 255 for v in range(256)])


def binarize_adaptive(gray, radius: int = 15, offset: int = 10):
    """Adaptive Mittelwert-Binarisierung (Modus L, 0/255).

    Ein Pixel ist Schrift, wenn es mindestens ``offset`` dunkler ist als der
    Mittelwert seiner Umgebung. Robust gegen ungleichmaessige Ausleuchtung und
    vergilbtes Thermopapier, wo eine globale Schwelle ganze Bereiche schwaerzt.
    """
    from PIL import ImageChops, ImageFilter

    local_mean = gray.filter(ImageFilter.BoxBlur(radius))
    darker_by = ImageChops.subtract(local_mean, gray)  # max(0, mean - pixel)
    return darker_by.point([0 if v > offset else 255 for v in range(256)])


def _projection_score(ink, angle: float) -> float:
    """Schaerfe des Zeilenprofils nach Drehung um ``angle``: bei korrekter
    Ausrichtung wechseln Textzeilen und Zeilenabstand abrupt."""
    from PIL import Image

    rotated = ink.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
    # Breite 1 + BOX-Filter = Mittelwert jeder Zeile
    profile = list(rotated.resize((1, rotated.size[1]), Image.Resampling.BOX).get_flattened_data())
    return float(sum((b - a) ** 2 for a, b in zip(profile, profile[1:])))


def estimate_skew_angle(gray, max_angle: float = _DESKEW_MAX_ANGLE) -> float:
    """Schraeglage in Grad (Projektionsprofil-Suche in [-max_angle, max_angle]).

    Arbeitet auf einer verkleinerten, binarisierten Kopie: grob in 1°-Schritten,
    dann fein in 0.2°-Schritten um das beste Grob-Ergebnis. Der Rueckgabewert
    ist der Winkel, um den das Bild gedreht werden muss (PIL: gegen den
    Uhrzeigersinn positiv).
    """
    from PIL import Image, ImageOps

    scale = _DESKEW_ANALYSIS_SIDE / max(gray.size)
    small = gray
    if scale < 1:
        small = gray.resize(
            (max(1, int(gray.size[0] * scale)), max(1, int(gray.size[1] * scale))),
            Image.Resampling.BILINEAR,
        )
    ink = ImageOps.invert(binarize_otsu(small))  # Schrift = hell
    if not ink.getbbox():
        return 0.0

    def best_of(angles):
        return max(angles, key=lambda a: _projection_score(ink, a))

    steps = int(max_angle)
    coarse = best_of([float(a) for a in range(-steps, steps + 1)])
    fine = best_of([coarse + d / 5 for d in range(-5, 6) if abs(coarse + d / 5) <= max_angle])
    return round(fine, 2)


def estimate_line_height(gray) -> float | None:
    """Median-Hoehe der Textzeilen in Pixeln (horizontales Projektionsprofil
    des adaptiv binarisierten Bilds — eine globale Schwelle wuerde bei
    ungleichmaessiger Ausleuchtung ganze Flaechen als "Schrift" werten),
    ``None`` wenn keine Zeilen erkennbar sind."""
    from PIL import Image, ImageFilter, ImageOps

    ink = ImageOps.invert(binarize_adaptive(gray.filter(ImageFilter.MedianFilter(size=3))))
    profile = list(ink.resize((1, ink.size[1]), Image.Resampling.BOX).get_flattened_data())
    peak = max(profile, default=0)
    floor = min(profile, default=0)
    if peak - floor < 10:
        return None
    # Relativ zum Grundrauschen: verrauschte Scans haben auch zwischen den
    # Zeilen "Tinte".
    threshold = floor + (peak - floor) * 0.25
    runs: list[int] = []
    run = 0
    for value in profile + [0]:
        if value > threshold:
            run += 1
        elif run:
            if run >= 3:
                runs.append(run)
            run = 0
    if not runs:
        return None
    runs.sort()
    return float(runs[len(runs) // 2])


def _second_pass_images(image) -> list:
    """Kandidaten fuer den zweiten Durchgang.

    1. Schraeglage korrigieren — schon 2-3° Neigung reissen bei Kassenbons die
       Bezeichnung (links) vom Betrag (rechts) in verschiedene Zeilen.
    2. Auf Tesseract-freundliche Zeilenhoehe skalieren: zu grosse Schrift
       (hochaufloesende Fotos, nach dem 2x-Upscale) liefert sonst kaum Text.
    3. Entrauschen und binarisieren — global (Otsu, robust gegen Rauschen)
       und adaptiv (robust gegen ungleichmaessige Ausleuchtung/vergilbtes
       Thermopapier). Welche Variante besser liest, entscheidet die Qualitaet.
    """
    from PIL import Image, ImageFilter, ImageOps

    gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    angle = estimate_skew_angle(gray)
    if abs(angle) >= _DESKEW_MIN_ANGLE:
        gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)

    line_height = estimate_line_height(gray)
    if line_height:
        scale = min(max(_TARGET_LINE_HEIGHT / line_height, 0.25), 3.0)
        new_size = (max(1, int(gray.size[0] * scale)), max(1, int(gray.size[1] * scale)))
        if abs(scale - 1.0) > 0.15 and new_size[0] * new_size[1] <= MAX_IMAGE_PIXELS:
            gray = gray.resize(new_size, Image.Resampling.LANCZOS)

    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    return [binarize_otsu(gray), binarize_adaptive(gray)]


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------


def _parse_conf(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return -1


def _text_from_data(data: dict) -> tuple[str, list[tuple[str, int]]]:
    """Text mit Zeilenstruktur aus ``image_to_data`` rekonstruieren.

    Woerter einer Zeile mit Leerzeichen, Zeilen mit "\\n", Absaetze/Bloecke
    mit "\\n\\n" — sonst landet ein gescanntes Dokument als eine einzige
    Zeile bei LLM, Volltextsuche und Chunker.

    Returns:
        (Text, Liste der uebernommenen (Wort, Konfidenz)).
    """
    lines: list[tuple[tuple[int, int], list[str]]] = []  # ((block, par), woerter)
    current_key = None
    words: list[tuple[str, int]] = []
    count = len(data.get("text", []))
    for i in range(count):
        word = (data["text"][i] or "").strip()
        conf = _parse_conf(data["conf"][i])
        if not word or conf <= 0:
            continue
        block = data.get("block_num", [0] * count)[i]
        par = data.get("par_num", [0] * count)[i]
        line = data.get("line_num", [0] * count)[i]
        key = (block, par, line)
        if key != current_key:
            lines.append(((block, par), []))
            current_key = key
        lines[-1][1].append(word)
        words.append((word, conf))

    parts: list[str] = []
    previous_par = None
    for par_key, line_words in lines:
        if parts:
            parts.append("\n\n" if par_key != previous_par else "\n")
        parts.append(" ".join(line_words))
        previous_par = par_key
    return "".join(parts), words


def _run_tesseract(processed, languages: str) -> _OcrPass:
    import pytesseract

    data = pytesseract.image_to_data(
        processed,
        lang=languages,
        output_type=pytesseract.Output.DICT,
    )
    text, words = _text_from_data(data)
    confidences = [conf for _, conf in words]
    avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
    quality = sum(len(word) * conf for word, conf in words if conf >= _QUALITY_MIN_WORD_CONF) / 100.0
    return _OcrPass(text=text.strip(), confidence=avg_confidence, quality=quality)


def _ocr_image_sync(image, languages: str) -> tuple[str, float]:
    """Fuehrt OCR auf einem PIL-Image aus (synchron).

    Erster Durchgang mit der Standard-Vorverarbeitung. Ist das Ergebnis schwach
    (wenig Text oder niedrige Konfidenz), folgt ein zweiter Durchgang
    (Schraeglagenkorrektur, Skalierung auf Zeilenhoehe, Otsu- und adaptive
    Binarisierung, siehe _second_pass_images); behalten wird das Ergebnis mit
    der hoechsten konfidenz-gewichteten Zeichenzahl. Gute Scans
    kosten so keinen zweiten Tesseract-Lauf.
    """
    first = _run_tesseract(_preprocess_for_ocr(image), languages)
    if not first.weak:
        return first.text, first.confidence

    best = first
    try:
        for candidate in _second_pass_images(image):
            result = _run_tesseract(candidate, languages)
            if result.quality > best.quality:
                best = result
    except Exception:
        logger.warning("Zweiter OCR-Durchgang fehlgeschlagen", exc_info=True)

    logger.info(
        "OCR zweiter Durchgang: %d -> %d Zeichen, Konfidenz %.0f%% -> %.0f%%",
        len(first.text), len(best.text), first.confidence * 100, best.confidence * 100,
    )
    return best.text, best.confidence


def _ocr_page(image, page_number: int, languages: str) -> PageText | None:
    """OCR einer Seite; Fehler werden geloggt und ergeben None."""
    try:
        text, confidence = _ocr_image_sync(image, languages)
    except Exception as e:
        logger.warning("OCR fuer Seite %d fehlgeschlagen: %s", page_number, e)
        return None
    if not text:
        return None
    return PageText(page_number=page_number, text=text, confidence=confidence)


def _merge_pages(pages: list[PageText], page_count: int) -> OcrResult | None:
    """Seiten in Reihenfolge zusammenfuehren, Konfidenz nach Zeichen gewichten."""
    if not pages:
        return None
    pages = sorted(pages, key=lambda p: p.page_number)
    total_chars = sum(len(p.text) for p in pages)
    avg_conf = (
        sum(p.confidence * len(p.text) for p in pages) / total_chars if total_chars else 0.0
    )
    return OcrResult(
        full_text="\n\n".join(p.text for p in pages),
        pages=pages,
        average_confidence=avg_conf,
        page_count=page_count,
    )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _read_pdf_text_pages(file_path: Path) -> list[str | None] | None:
    """Digitaler Textlayer pro Seite (pdfplumber), None wenn unlesbar.

    FH-02: Ein kleines PDF mit vielen textlastigen Seiten (geteilter Content-
    Stream) blockiert sonst den einzigen Queue-Worker fuer Stunden. Gelesen
    werden deshalb hoechstens ``_MAX_TEXT_PAGES`` Seiten und nur, solange das
    Zeitbudget ``_TEXT_TIME_BUDGET_S`` reicht. Nicht gelesene Seiten sind
    ``None`` — sie werden weder als Text noch per OCR verarbeitet.
    """
    import pdfplumber

    try:
        with pdfplumber.open(file_path) as pdf:
            pages = pdf.pages
            texts: list[str | None] = [None] * len(pages)
            deadline = time.monotonic() + _TEXT_TIME_BUDGET_S
            for index, page in enumerate(pages[:_MAX_TEXT_PAGES]):
                if time.monotonic() > deadline:
                    logger.warning(
                        "PDF-Textlayer: Zeitbudget (%ds) erschoepft nach %d von %d Seiten",
                        _TEXT_TIME_BUDGET_S, index, len(pages),
                    )
                    break
                texts[index] = (page.extract_text() or "").strip()
                # pdfplumber cacht Layout-Objekte pro Seite — freigeben, damit
                # lange PDFs den Speicher nicht Seite fuer Seite aufblaehen.
                page.close()
            if len(pages) > _MAX_TEXT_PAGES:
                logger.warning(
                    "PDF-Textlayer: nur die ersten %d von %d Seiten gelesen",
                    _MAX_TEXT_PAGES, len(pages),
                )
            return texts
    except Exception as e:
        logger.warning("pdfplumber konnte PDF nicht lesen: %s", e)
        return None


def _pdf_page_count(file_path: Path) -> int | None:
    from pdf2image import pdfinfo_from_path

    try:
        return int(pdfinfo_from_path(file_path, timeout=_POPPLER_TIMEOUT_S)["Pages"])
    except Exception as e:
        logger.error("PDF-Seitenzahl nicht ermittelbar: %s", e)
        return None


_PAGE_SIZE_RE = re.compile(r"([0-9.]+)\s*x\s*([0-9.]+)\s*pts")


def _pdf_page_size_pts(file_path: Path, page_number: int) -> tuple[float, float] | None:
    """MediaBox-Groesse (Punkte, ungedreht) einer Seite laut pdfinfo, sonst None."""
    from pdf2image import pdfinfo_from_path

    try:
        info = pdfinfo_from_path(
            file_path, first_page=page_number, last_page=page_number, timeout=_POPPLER_TIMEOUT_S,
        )
    except Exception as e:
        logger.error("PDF-Seitengroesse von Seite %d nicht ermittelbar: %s", page_number, e)
        return None
    for key, value in info.items():
        # pdfinfo -f/-l: "Page    3 size:  595.276 x 841.89 pts (A4)"
        if key.split() == ["Page", str(page_number), "size"]:
            match = _PAGE_SIZE_RE.search(str(value))
            if match:
                width, height = float(match.group(1)), float(match.group(2))
                if width > 0 and height > 0:
                    return width, height
    logger.error("PDF-Seitengroesse von Seite %d nicht lesbar", page_number)
    return None


def _render_size(width_pts: float, height_pts: float, dpi: int = _OCR_DPI) -> tuple[int, int]:
    """Zielgroesse in Pixeln fuer das OCR-Rendering, hoechstens MAX_IMAGE_PIXELS.

    FH-02: ``dpi`` allein liesse pdftoppm eine 14400-pt-MediaBox auf
    60000x60000 px rastern (~3,6 GB), bevor Pillows Bomb-Check greift. Mit
    festem ``-scale-to-x/-scale-to-y`` ist die Ausgabe exakt so gross wie hier
    berechnet — unabhaengig von /Rotate oder /UserUnit.
    """
    scale = dpi / 72.0
    width, height = width_pts * scale, height_pts * scale
    if width * height > MAX_IMAGE_PIXELS:
        factor = math.sqrt(MAX_IMAGE_PIXELS / (width * height))
        width, height = width * factor, height * factor
    return max(1, math.floor(width)), max(1, math.floor(height))


def _ocr_pdf_page(file_path: Path, page_number: int, languages: str) -> PageText | None:
    """Rendert EINE Seite (300 dpi, Graustufen) und liest sie per OCR.

    Seite fuer Seite statt aller Seiten auf einmal: sonst liegen bis zu
    MAX_OCR_PAGES RGB-Seiten (~26 MB je Seite) gleichzeitig im Speicher.
    Die Pixelgroesse ist auf MAX_IMAGE_PIXELS begrenzt, pdftoppm bekommt ein
    Timeout (FH-02).
    """
    from pdf2image import convert_from_path

    page_size = _pdf_page_size_pts(file_path, page_number)
    if page_size is None:
        return None
    try:
        images = convert_from_path(
            file_path, dpi=_OCR_DPI, first_page=page_number, last_page=page_number, grayscale=True,
            size=_render_size(*page_size), timeout=_POPPLER_TIMEOUT_S,
        )
    except Exception as e:
        logger.error("pdf2image Konvertierung von Seite %d fehlgeschlagen: %s", page_number, e)
        return None
    if not images:
        return None
    return _ocr_page(images[0], page_number, languages)


def _extract_pdf_sync(file_path: Path, settings: Settings) -> OcrResult | None:
    """Text aus einem PDF: digitaler Textlayer, wo vorhanden, sonst OCR pro Seite.

    Gemischte PDFs (digitales Deckblatt + eingescannte Seiten) werden Seite fuer
    Seite behandelt — frueher reichte eine einzige Textseite, um alle Scan-
    Seiten zu ueberspringen. OCR ist auf MAX_OCR_PAGES Seiten begrenzt.
    """
    texts = _read_pdf_text_pages(file_path)
    if texts is None:
        page_count = _pdf_page_count(file_path)
        if not page_count:
            return None
        texts = [""] * page_count

    pages: list[PageText] = []
    ocr_budget = settings.MAX_OCR_PAGES
    ocr_pages = 0
    for page_number, text in enumerate(texts, start=1):
        if text is None:
            continue  # ausserhalb des Seiten-/Zeitbudgets (FH-02)
        if len(text) >= _MIN_DIGITAL_PAGE_CHARS:
            pages.append(PageText(page_number=page_number, text=text, confidence=1.0))
            continue
        scanned = None
        if ocr_budget > 0:
            ocr_budget -= 1
            ocr_pages += 1
            scanned = _ocr_pdf_page(file_path, page_number, settings.OCR_LANGUAGES)
        if scanned and len(scanned.text) > len(text):
            pages.append(scanned)
        elif text:
            pages.append(PageText(page_number=page_number, text=text, confidence=1.0))

    if ocr_pages:
        logger.info("PDF: %d von %d Seiten per OCR gelesen", ocr_pages, len(texts))
    return _merge_pages(pages, page_count=len(texts))


# ---------------------------------------------------------------------------
# Bilder (inkl. mehrseitiger TIFFs)
# ---------------------------------------------------------------------------


def _extract_image_ocr_sync(file_path: Path, settings: Settings) -> OcrResult | None:
    """OCR fuer Bilddateien via Tesseract; mehrseitige TIFFs Seite fuer Seite
    (bis MAX_OCR_PAGES)."""
    pages: list[PageText] = []
    frame_count = 0
    try:
        for frame in iter_normalized_frames(file_path, settings.MAX_OCR_PAGES):
            frame_count += 1
            page = _ocr_page(frame, frame_count, settings.OCR_LANGUAGES)
            if page:
                pages.append(page)
    except Exception as e:
        if frame_count == 0:
            logger.error("Bild konnte nicht geoeffnet werden: %s", e)
            return None
        logger.warning("Bild ab Seite %d nicht lesbar: %s", frame_count + 1, e)

    return _merge_pages(pages, page_count=frame_count)


async def extract_text(
    file_path: Path,
    file_type: str,
    settings: Settings,
) -> OcrResult | None:
    """Extrahiert Text aus einem Dokument (PDF oder Bild).

    Fuer PDFs: pro Seite digitaler Text (pdfplumber), sonst Tesseract (Scan).
    Fuer Bilder: Direkt Tesseract mit Vorverarbeitung.

    Returns:
        OcrResult bei Erfolg, None bei Fehler.
    """
    file_type_lower = file_type.lower()

    try:
        if is_pdf_type(file_type_lower):
            result = await asyncio.to_thread(_extract_pdf_sync, file_path, settings)
            if result:
                logger.info(
                    "Text aus PDF extrahiert (%d Seiten, %d Zeichen, Konfidenz: %.1f%%)",
                    result.page_count,
                    len(result.full_text),
                    result.average_confidence * 100,
                )
            return result

        elif is_image_type(file_type_lower):
            result = await asyncio.to_thread(
                _extract_image_ocr_sync, file_path, settings
            )
            if result:
                logger.info(
                    "OCR-Text aus Bild extrahiert (%d Seiten, %d Zeichen, Konfidenz: %.1f%%)",
                    result.page_count,
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
