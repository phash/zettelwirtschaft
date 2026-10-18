"""pdf_split_service: verlustfreies Schneiden, Geometrie, Tinte (synthetische PDFs)."""

import io
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services import pdf_split_service
from app.services.pdf_split_service import PdfSplitError


def _page_image(label: str) -> Image.Image:
    img = Image.new("L", (400, 560), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((60, 80, 340, 120), fill=0)
    draw.text((70, 200), label, fill=0)
    return img


def make_pdf(path: Path, pages: int, *, blank: set[int] = frozenset(), sizes: dict | None = None,
             encrypt: bool = False) -> Path:
    kwargs = {}
    if encrypt:
        from reportlab.lib.pdfencrypt import StandardEncryption

        kwargs["encrypt"] = StandardEncryption("geheim", ownerPassword="besitzer")
    c = canvas.Canvas(str(path), pagesize=A4, **kwargs)
    for n in range(1, pages + 1):
        size = (sizes or {}).get(n, A4)
        c.setPageSize(size)
        if n not in blank:
            buf = io.BytesIO()
            _page_image(f"Seite {n}").save(buf, format="PNG")
            buf.seek(0)
            c.drawImage(ImageReader(buf), 50, 200, width=300, height=420)
            c.drawString(60, 100, f"Seite {n} von {pages}")
        c.showPage()
    c.save()
    return path


def _image_streams(path: Path) -> list[bytes]:
    """Rohdaten (nicht dekodiert) aller Bildobjekte, in Seitenreihenfolge."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for index in range(len(pdf)):
            page = pdf[index]
            for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
                out.append(bytes(obj.get_data(decode_simple=False)))
            page.close()
        return out
    finally:
        pdf.close()


def test_page_count_and_geometry(tmp_path):
    pdf = make_pdf(tmp_path / "s.pdf", 3, sizes={2: A5, 3: landscape(A4)})
    assert pdf_split_service.page_count(pdf) == 3
    geom = pdf_split_service.pdf_page_geometry(pdf)
    assert [round(g.w) for g in geom] == [595, 420, 842]
    assert [round(g.h) for g in geom] == [842, 595, 595]
    assert all(g.rot == 0 for g in geom)


def test_write_part_is_lossless(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 5)
    dst = tmp_path / "out" / "teil-01.pdf"

    size = pdf_split_service.write_part(src, [2, 3, 5], dst)

    assert size == dst.stat().st_size > 0
    assert pdf_split_service.page_count(dst) == 3
    assert not dst.with_name(dst.name + ".tmp").exists()
    source_streams = _image_streams(src)
    assert _image_streams(dst) == [source_streams[1], source_streams[2], source_streams[4]]


def test_write_part_rejects_invalid_pages_and_leaves_no_files(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 2)
    dst = tmp_path / "teil-01.pdf"
    with pytest.raises(PdfSplitError):
        pdf_split_service.write_part(src, [1, 3], dst)
    with pytest.raises(PdfSplitError):
        pdf_split_service.write_part(src, [], dst)
    assert not dst.exists()
    assert not dst.with_name(dst.name + ".tmp").exists()


def test_write_part_replaces_existing_file_atomically(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 3)
    dst = tmp_path / "teil-01.pdf"
    dst.write_bytes(b"alt")
    pdf_split_service.write_part(src, [1], dst)
    assert pdf_split_service.page_count(dst) == 1


def test_encrypted_and_broken_pdf_raise(tmp_path):
    encrypted = make_pdf(tmp_path / "enc.pdf", 2, encrypt=True)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nkaputt")
    for path in (encrypted, broken):
        with pytest.raises(PdfSplitError):
            pdf_split_service.page_count(path)
        with pytest.raises(PdfSplitError):
            pdf_split_service.write_part(path, [1], tmp_path / "x.pdf")


def test_render_and_ink_stats_detect_blank_pages(tmp_path):
    pdf = make_pdf(tmp_path / "s.pdf", 2, blank={2})
    content = pdf_split_service.render_page(pdf, 1, dpi=50)
    blank = pdf_split_service.render_page(pdf, 2, dpi=50)
    assert content.mode == "L"
    assert abs(content.size[0] - round(595 * 50 / 72)) <= 1

    ink, top_ink = pdf_split_service.page_ink_stats(content)
    assert ink > 0.005
    assert 0.0 <= top_ink <= 1.0
    assert pdf_split_service.page_ink_stats(blank) == (0.0, 0.0)
    with pytest.raises(PdfSplitError):
        pdf_split_service.render_page(pdf, 3)


def test_ink_ignores_left_margin_strip():
    """Druckerei-/Sendungscodes im linken Randstreifen zaehlen nicht als Tinte."""
    img = Image.new("L", (400, 560), 255)
    ImageDraw.Draw(img).rectangle((0, 0, 20, 559), fill=0)  # x < 6 %
    assert pdf_split_service.page_ink_stats(img) == (0.0, 0.0)


def test_render_size_is_capped(monkeypatch):
    monkeypatch.setattr(pdf_split_service, "MAX_IMAGE_PIXELS", 1_000_000)
    scale = pdf_split_service._render_scale(14400, 14400, 300, None)
    assert (14400 * scale) ** 2 <= 1_000_000 * 1.001
    assert pdf_split_service._render_scale(595, 842, 100, 500) == pytest.approx(500 / 842)


def test_self_test_cuts_in_memory():
    """Native-Build-Check: pdfium ladbar, import_pages + save funktionieren."""
    assert pdf_split_service.self_test()


def test_self_test_reports_a_broken_library(monkeypatch):
    def broken(*_args, **_kwargs):
        raise pdfium.PdfiumError("kaputt")

    monkeypatch.setattr(pdfium.PdfDocument, "import_pages", broken)
    with pytest.raises(PdfSplitError, match="Selbsttest"):
        pdf_split_service.self_test()
