"""Regressionstests fuer die OCR-Pipeline: Zeilenstruktur, mehrseitige TIFFs,
gemischte PDFs, Upscale-Grenze, zweiter Durchgang (Binarisierung + Deskew)."""

import random
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import Settings
from app.services import ocr_service
from app.services.ocr_service import (
    PageText,
    _OcrPass,
    _extract_image_ocr_sync,
    _extract_pdf_sync,
    _ocr_image_sync,
    _preprocess_for_ocr,
    _text_from_data,
    binarize_adaptive,
    binarize_otsu,
    estimate_skew_angle,
    otsu_threshold,
)

_REPO_TESSERACT = Path(__file__).resolve().parents[3] / "tools" / "tesseract"


def _data(rows):
    """image_to_data-DICT aus (block, par, line, text, conf)-Tupeln."""
    return {
        "block_num": [r[0] for r in rows],
        "par_num": [r[1] for r in rows],
        "line_num": [r[2] for r in rows],
        "text": [r[3] for r in rows],
        "conf": [r[4] for r in rows],
    }


class TestTextFromData:
    def test_lines_and_paragraphs_preserved(self):
        """Woerter einer Zeile mit Leerzeichen, Zeilen mit \\n, Absaetze mit \\n\\n."""
        data = _data([
            (1, 1, 1, "Rechnung", "96"),
            (1, 1, 1, "Nr.", "91.5"),
            (1, 1, 2, "Betrag:", 90),
            (1, 1, 2, "119,00", "88"),
            (1, 2, 1, "Danke", "95"),
            (1, 2, 1, "", "-1"),          # Strukturzeile ohne Wort
            (2, 1, 1, "Seite", "-1"),     # conf -1 -> kein Wort
        ])
        text, words = _text_from_data(data)
        assert text == "Rechnung Nr.\nBetrag: 119,00\n\nDanke"
        assert ("Nr.", 91) in words  # conf als int(float(...))

    def test_empty(self):
        assert _text_from_data(_data([])) == ("", [])


def _tiff(path: Path, frames: int) -> Path:
    images = [Image.new("RGB", (300, 200), "white") for _ in range(frames)]
    images[0].save(path, save_all=True, append_images=images[1:])
    return path


class TestMultiPageTiff:
    def test_all_frames_are_ocrd(self, tmp_path: Path, test_settings: Settings):
        src = _tiff(tmp_path / "scan.tiff", 2)
        texts = iter([("Seite eins", 0.9), ("Seite zwei", 0.7)])
        with patch("app.services.ocr_service._ocr_image_sync", side_effect=lambda img, lang: next(texts)):
            result = _extract_image_ocr_sync(src, test_settings)
        assert result.page_count == 2
        assert result.full_text == "Seite eins\n\nSeite zwei"
        assert [p.page_number for p in result.pages] == [1, 2]

    def test_frames_capped_at_max_ocr_pages(self, tmp_path: Path, test_settings: Settings):
        test_settings.MAX_OCR_PAGES = 2
        src = _tiff(tmp_path / "scan.tiff", 3)
        with patch("app.services.ocr_service._ocr_image_sync", return_value=("Text", 0.9)) as ocr:
            result = _extract_image_ocr_sync(src, test_settings)
        assert ocr.call_count == 2
        assert result.page_count == 2


class TestMixedPdf:
    def _pdf(self, tmp_path: Path) -> Path:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        path = tmp_path / "gemischt.pdf"
        c = canvas.Canvas(str(path), pagesize=A4)
        c.drawString(100, 750, "Digitales Deckblatt mit ausreichend viel Text darauf")
        c.showPage()
        c.drawString(100, 750, "S. 2")  # nur ein Stempel -> gilt als Scan-Seite
        c.showPage()
        c.save()
        return path

    def test_only_pages_without_text_layer_are_ocrd(self, tmp_path: Path, test_settings: Settings):
        pdf = self._pdf(tmp_path)
        scanned = PageText(page_number=2, text="Gescannte Seite mit Betrag 19,99 EUR", confidence=0.5)
        with patch("app.services.ocr_service._ocr_pdf_page", return_value=scanned) as ocr:
            result = _extract_pdf_sync(pdf, test_settings)
        ocr.assert_called_once()
        assert ocr.call_args.args[1] == 2  # nur Seite 2
        assert result.page_count == 2
        assert result.full_text.startswith("Digitales Deckblatt")
        assert result.full_text.endswith("Gescannte Seite mit Betrag 19,99 EUR")
        # zeichengewichtete Konfidenz zwischen Scan (0.5) und Digital (1.0)
        assert 0.5 < result.average_confidence < 1.0

    def test_ocr_budget_respected(self, tmp_path: Path, test_settings: Settings):
        test_settings.MAX_OCR_PAGES = 0
        pdf = self._pdf(tmp_path)
        with patch("app.services.ocr_service._ocr_pdf_page") as ocr:
            result = _extract_pdf_sync(pdf, test_settings)
        ocr.assert_not_called()
        assert result.full_text.endswith("S. 2")  # kurzer Textlayer bleibt erhalten


class TestPreprocessing:
    def test_upscale_respects_pixel_limit(self):
        """Ein schmales, sehr langes Bild darf durch den 2x-Upscale nicht ueber das Limit wachsen."""
        tall = Image.new("L", (1000, 20000), 255)  # 20 Mpx -> 2x waeren 80 Mpx
        assert _preprocess_for_ocr(tall).size == (1000, 20000)

    def test_otsu_threshold_separates_two_levels(self):
        img = Image.new("L", (100, 100), 200)
        ImageDraw.Draw(img).rectangle([0, 0, 49, 99], fill=40)
        threshold = otsu_threshold(img)
        assert 40 <= threshold < 200
        assert set(binarize_otsu(img).get_flattened_data()) == {0, 255}

    def test_adaptive_binarization_handles_uneven_lighting(self):
        """Dunkle Schrift auf Verlauf: global wuerde die dunkle Seite schwarz."""
        img = Image.new("L", (400, 100))
        draw = ImageDraw.Draw(img)
        for x in range(400):
            draw.line([(x, 0), (x, 99)], fill=60 + x // 2)  # 60 .. 259 -> Verlauf
        draw.rectangle([20, 40, 30, 60], fill=10)           # "Schrift" links (dunkler Bereich)
        draw.rectangle([350, 40, 360, 60], fill=150)        # "Schrift" rechts (heller Bereich)
        out = binarize_adaptive(img)
        assert out.getpixel((25, 50)) == 0 and out.getpixel((355, 50)) == 0
        assert out.getpixel((100, 10)) == 255 and out.getpixel((300, 10)) == 255

    @pytest.mark.parametrize("tilt", [3.0, -2.5, 0.0])
    def test_skew_angle_estimated(self, tilt):
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)
        for y in range(60, 560, 40):
            draw.rectangle([60, y, 740, y + 12], fill=0)  # Textzeilen-Balken
        tilted = img.rotate(tilt, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
        assert abs(estimate_skew_angle(tilted) + tilt) <= 0.5

    def test_blank_image_has_no_skew(self):
        assert estimate_skew_angle(Image.new("L", (200, 200), 255)) == 0.0


class TestSecondPass:
    def test_strong_first_pass_runs_tesseract_once(self):
        strong = _OcrPass(text="x" * 100, confidence=0.9, quality=90.0)
        with patch("app.services.ocr_service._run_tesseract", return_value=strong) as run:
            assert _ocr_image_sync(Image.new("RGB", (50, 50), "white"), "deu") == ("x" * 100, 0.9)
        assert run.call_count == 1

    def test_weak_first_pass_keeps_better_result(self):
        weak = _OcrPass(text="ab", confidence=0.3, quality=0.6)
        better = _OcrPass(text="SUMME 7,90", confidence=0.8, quality=8.0)
        worse = _OcrPass(text="~", confidence=0.2, quality=0.2)
        # erster Durchgang + zwei Kandidaten (Otsu, adaptiv)
        with patch("app.services.ocr_service._run_tesseract", side_effect=[weak, better, worse]) as run:
            text, conf = _ocr_image_sync(Image.new("RGB", (50, 50), "white"), "deu")
        assert run.call_count == 3
        assert (text, conf) == ("SUMME 7,90", 0.8)

    def test_second_pass_not_taken_when_worse(self):
        weak = _OcrPass(text="Brot 3,20", confidence=0.5, quality=4.5)
        worse = _OcrPass(text="~", confidence=0.2, quality=0.2)
        with patch("app.services.ocr_service._run_tesseract", side_effect=[weak, worse, worse]):
            assert _ocr_image_sync(Image.new("RGB", (50, 50), "white"), "deu") == ("Brot 3,20", 0.5)


def _use_tesseract(monkeypatch) -> bool:
    """Systemweites Tesseract oder das gebundelte aus tools/tesseract (Windows-Build-Host)."""
    if shutil.which("tesseract"):
        return True
    exe = _REPO_TESSERACT / "tesseract.exe"
    if exe.exists() and (_REPO_TESSERACT / "tessdata" / "deu.traineddata").exists():
        import pytesseract
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", str(exe))
        monkeypatch.setenv("TESSDATA_PREFIX", str(_REPO_TESSERACT / "tessdata"))
        return True
    return False


def _faded_tilted_receipt(seed: int = 1) -> Image.Image:
    """Kassenbon mit ungleichmaessiger Ausleuchtung (vergilbtes Thermopapier),
    leichter Unschaerfe, Rauschen und 3° Schraeglage."""
    rnd = random.Random(seed)
    width, height = 700, 760
    img = Image.new("L", (width, height))
    draw = ImageDraw.Draw(img)
    for x in range(width):
        draw.line([(x, 0), (x, height)], fill=int(150 + 80 * x / width))
    font = ImageFont.load_default(size=34)
    lines = ["BAECKEREI MUELLER", "Brot 3,20", "Kaffee 2,80", "SUMME EUR 7,90", "Datum 12.03.2025"]
    for i, line in enumerate(lines):
        draw.text((60, 80 + i * 120), line, fill=60, font=font)
    pixels = img.load()
    for _ in range(int(width * height * 0.01)):
        pixels[rnd.randrange(width), rnd.randrange(height)] = rnd.choice((0, 255))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img.rotate(3.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=200).convert("RGB")


class TestRealTesseract:
    def test_faded_tilted_receipt_recovered_by_second_pass(self, monkeypatch):
        if not _use_tesseract(monkeypatch):
            pytest.skip("Tesseract nicht installiert")
        receipt = _faded_tilted_receipt()
        first = ocr_service._run_tesseract(_preprocess_for_ocr(receipt), "deu+eng")
        text, confidence = _ocr_image_sync(receipt, "deu+eng")
        assert len(text) > len(first.text)
        assert "SUMME" in text and "7,90" in text
        assert confidence >= 0.6
