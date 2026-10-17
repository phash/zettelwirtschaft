"""FH-02: PDF-Render-Bomben und Endlos-Textlayer duerfen den Queue-Worker
nicht lahmlegen (Pixel-Limit + Timeout fuer pdftoppm, Seiten-/Zeitbudget
fuer pdfplumber)."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.config import Settings
from app.core.image_safety import MAX_IMAGE_PIXELS
from app.services import ocr_service, thumbnail_service
from app.services.ocr_service import PageText, _extract_pdf_sync, _ocr_pdf_page, _render_size

_REPO_POPPLER = Path(__file__).resolve().parents[3] / "tools" / "poppler" / "Library" / "bin"


def _blank_pdf(width: float, height: float, pages: int = 1, extra: str = "") -> bytes:
    """Minimales PDF ohne Textlayer mit beliebiger MediaBox (wenige hundert Byte)."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
    ] + [
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}]{extra} >>".encode()
        for _ in range(pages)
    ]
    out = b"%PDF-1.7\n"
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class TestRenderSize:
    def test_a4_keeps_300_dpi(self):
        assert _render_size(595.276, 841.89) == (2480, 3507)

    @pytest.mark.parametrize("size", [(14400, 14400), (3500, 3500), (14400, 200), (1, 14400)])
    def test_huge_mediabox_is_capped(self, size):
        width, height = _render_size(*size)
        assert width * height <= MAX_IMAGE_PIXELS
        # Seitenverhaeltnis bleibt (bis auf Rundung) erhalten.
        assert abs(width / height - size[0] / size[1]) / (size[0] / size[1]) < 0.3


class TestOcrPdfPageMocked:
    def test_render_gets_size_and_timeout(self, tmp_path: Path):
        pdf = tmp_path / "bomb.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        with patch(
            "pdf2image.pdfinfo_from_path",
            return_value={"Pages": 1, "Page    1 size": "14400 x 14400 pts", "Page    1 rot": "0"},
        ), patch(
            "pdf2image.convert_from_path", return_value=[Image.new("L", (10, 10), 255)],
        ) as convert, patch.object(ocr_service, "_ocr_page", return_value=None):
            _ocr_pdf_page(pdf, 1, "deu")
        kwargs = convert.call_args.kwargs
        width, height = kwargs["size"]
        assert width * height <= MAX_IMAGE_PIXELS
        assert kwargs["timeout"] == ocr_service._POPPLER_TIMEOUT_S

    def test_unknown_page_size_skips_rendering(self, tmp_path: Path):
        pdf = tmp_path / "kaputt.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        with patch("pdf2image.pdfinfo_from_path", side_effect=RuntimeError("pdfinfo kaputt")), patch(
            "pdf2image.convert_from_path",
        ) as convert:
            assert _ocr_pdf_page(pdf, 1, "deu") is None
        convert.assert_not_called()


@pytest.mark.skipif(not (_REPO_POPPLER / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm")).exists(),
                    reason="gebundeltes poppler (tools/poppler) fehlt")
class TestOcrPdfPageRealPoppler:
    """Echtes pdftoppm: ein 416-Byte-PDF mit 14400-pt-MediaBox darf nicht mit
    60000x60000 px gerastert werden."""

    @pytest.fixture(autouse=True)
    def _poppler_on_path(self, monkeypatch):
        monkeypatch.setenv("PATH", str(_REPO_POPPLER) + os.pathsep + os.environ.get("PATH", ""))

    @pytest.mark.parametrize("extra", ["", " /Rotate 90", " /UserUnit 10"])
    def test_render_bomb_is_capped(self, tmp_path: Path, extra: str):
        pdf = tmp_path / "bomb.pdf"
        pdf.write_bytes(_blank_pdf(14400, 14400, extra=extra))
        seen: list[tuple[int, int]] = []

        def fake_ocr(image, page_number, languages):
            seen.append(image.size)
            return None

        with patch.object(ocr_service, "_ocr_page", side_effect=fake_ocr):
            _ocr_pdf_page(pdf, 1, "deu")
        assert len(seen) == 1
        assert seen[0][0] * seen[0][1] <= MAX_IMAGE_PIXELS

    def test_rotated_page_is_not_distorted(self, tmp_path: Path):
        pdf = tmp_path / "quer.pdf"
        pdf.write_bytes(_blank_pdf(600, 200, extra=" /Rotate 90"))
        seen: list[tuple[int, int]] = []
        with patch.object(ocr_service, "_ocr_page", side_effect=lambda img, *_: seen.append(img.size)):
            _ocr_pdf_page(pdf, 1, "deu")
        assert seen == [(833, 2500)]  # 200x600 pt bei 300 dpi, gedreht


def _text_pdf(path: Path, pages: int) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(pages):
        c.drawString(100, 750, f"Seite {i + 1} mit ausreichend digitalem Text fuer den Textlayer")
        c.showPage()
    c.save()
    return path


class TestTextLayerBudget:
    def test_page_cap(self, tmp_path: Path, test_settings: Settings, monkeypatch):
        monkeypatch.setattr(ocr_service, "_MAX_TEXT_PAGES", 2)
        pdf = _text_pdf(tmp_path / "lang.pdf", 3)
        texts = ocr_service._read_pdf_text_pages(pdf)
        assert texts[:2] != [None, None] and texts[2] is None
        with patch.object(ocr_service, "_ocr_pdf_page") as ocr:
            result = _extract_pdf_sync(pdf, test_settings)
        ocr.assert_not_called()  # nicht gelesene Seiten werden auch nicht gerendert
        assert result.page_count == 3
        assert "Seite 2" in result.full_text and "Seite 3" not in result.full_text

    def test_time_budget(self, tmp_path: Path, test_settings: Settings, monkeypatch):
        monkeypatch.setattr(ocr_service, "_TEXT_TIME_BUDGET_S", -1)
        pdf = _text_pdf(tmp_path / "langsam.pdf", 3)
        assert ocr_service._read_pdf_text_pages(pdf) == [None, None, None]
        with patch.object(ocr_service, "_ocr_pdf_page") as ocr:
            assert _extract_pdf_sync(pdf, test_settings) is None
        ocr.assert_not_called()

    def test_normal_pdf_unchanged(self, tmp_path: Path, test_settings: Settings):
        pdf = _text_pdf(tmp_path / "normal.pdf", 3)
        with patch.object(ocr_service, "_ocr_pdf_page", return_value=PageText(1, "x", 1.0)) as ocr:
            result = _extract_pdf_sync(pdf, test_settings)
        ocr.assert_not_called()
        assert result.page_count == 3 and "Seite 3" in result.full_text


async def test_thumbnail_render_has_timeout(tmp_path: Path, test_settings: Settings):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4")
    with patch("pdf2image.convert_from_path", return_value=[Image.new("RGB", (20, 30), "white")]) as convert:
        assert await thumbnail_service.generate_thumbnail(src, "pdf", "t-id", test_settings) is not None
    assert convert.call_args.kwargs["timeout"] == thumbnail_service._PDF_RENDER_TIMEOUT_S
