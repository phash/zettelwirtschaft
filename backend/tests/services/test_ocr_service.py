import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.services.ocr_service import OcrResult, PageText, extract_text


def _tesseract_available() -> bool:
    """Prueft ob Tesseract installiert ist."""
    return shutil.which("tesseract") is not None


def _pdfplumber_available() -> bool:
    """Prueft ob pdfplumber importierbar ist."""
    try:
        import pdfplumber  # noqa: F401
        return True
    except ImportError:
        return False


class TestOcrResult:
    def test_ocr_result_dataclass(self):
        page = PageText(page_number=1, text="Hallo Welt", confidence=0.95)
        result = OcrResult(
            full_text="Hallo Welt",
            pages=[page],
            average_confidence=0.95,
            page_count=1,
        )
        assert result.full_text == "Hallo Welt"
        assert len(result.pages) == 1
        assert result.average_confidence == 0.95
        assert result.page_count == 1

    def test_ocr_result_defaults(self):
        result = OcrResult(full_text="Test")
        assert result.pages == []
        assert result.average_confidence == 0.0
        assert result.page_count == 0


class TestExtractTextPdf:
    @pytest.mark.skipif(not _pdfplumber_available(), reason="pdfplumber nicht installiert")
    async def test_digital_pdf_extraction(
        self,
        sample_pdf_with_text: Path,
        test_settings: Settings,
    ):
        """Digitale PDFs werden ohne OCR extrahiert (pdfplumber)."""
        result = await extract_text(sample_pdf_with_text, "pdf", test_settings)
        assert result is not None
        assert result.full_text
        assert result.average_confidence == 1.0
        assert "Rechnung" in result.full_text or "rechnung" in result.full_text.lower()

    async def test_pdf_file_not_found(self, test_settings: Settings):
        """Nicht existierende Datei gibt None zurueck."""
        result = await extract_text(Path("/nonexistent/test.pdf"), "pdf", test_settings)
        assert result is None

    async def test_unsupported_file_type(self, test_settings: Settings, tmp_path: Path):
        """Nicht unterstuetzter Dateityp gibt None zurueck."""
        dummy = tmp_path / "test.docx"
        dummy.write_bytes(b"dummy content")
        result = await extract_text(dummy, "docx", test_settings)
        assert result is None


class TestExtractTextImage:
    @pytest.mark.skipif(not _tesseract_available(), reason="Tesseract nicht installiert")
    async def test_image_ocr(
        self,
        sample_image_with_text: Path,
        test_settings: Settings,
    ):
        """Bild-OCR extrahiert Text aus Bildern."""
        result = await extract_text(sample_image_with_text, "png", test_settings)
        assert result is not None
        assert result.full_text
        assert result.page_count == 1
        assert 0.0 <= result.average_confidence <= 1.0

    async def test_corrupt_image(self, test_settings: Settings, tmp_path: Path):
        """Defektes Bild gibt None zurueck."""
        corrupt = tmp_path / "corrupt.jpg"
        corrupt.write_bytes(b"not an image at all")
        result = await extract_text(corrupt, "jpg", test_settings)
        assert result is None


class TestExtractTextMocked:
    async def test_pdf_without_text_layer_is_ocrd(self, test_settings: Settings, tmp_path: Path):
        """Wenn pdfplumber keinen Text findet, wird jede Seite per OCR gelesen."""
        dummy_pdf = tmp_path / "scan.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 minimal")

        with patch(
            "app.services.ocr_service._read_pdf_text_pages", return_value=[""]
        ), patch(
            "app.services.ocr_service._ocr_pdf_page",
            return_value=PageText(page_number=1, text="Gescannter Text", confidence=0.85),
        ):
            result = await extract_text(dummy_pdf, "pdf", test_settings)
            assert result is not None
            assert result.full_text == "Gescannter Text"
            assert result.average_confidence == 0.85


class TestPreprocessForOcr:
    """Echter Pillow-Pfad (ohne Mocks) — faengt API-Brueche bei Pillow-Bumps."""

    def test_small_image_upscaled_and_grayscale(self):
        from PIL import Image

        from app.services.ocr_service import _preprocess_for_ocr

        img = Image.new("RGB", (600, 400), "white")
        out = _preprocess_for_ocr(img)
        assert out.mode == "L"
        assert out.size == (1200, 800)

    def test_large_image_not_upscaled(self):
        from PIL import Image

        from app.services.ocr_service import _preprocess_for_ocr

        img = Image.new("RGB", (1600, 2000), "white")
        out = _preprocess_for_ocr(img)
        assert out.size == (1600, 2000)

    @pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
    def test_decompression_bomb_returns_none(self, tmp_path: Path, test_settings: Settings):
        from PIL import Image

        from app.services.ocr_service import _extract_image_ocr_sync

        src = tmp_path / "bomb.png"
        Image.new("1", (10_000, 7_000)).save(src)  # 70 Mpx > 64 Mpx-Limit
        with patch("pytesseract.image_to_data") as ocr:
            assert _extract_image_ocr_sync(src, test_settings) is None
            ocr.assert_not_called()
