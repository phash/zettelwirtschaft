from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.services.thumbnail_service import generate_thumbnail


class TestGenerateThumbnail:
    async def test_unreadable_image_fails_gracefully(self, sample_jpg: Path, test_settings: Settings):
        """Nicht von Pillow lesbares JPEG -> None statt Exception (Erfolgspfad:
        test_real_png_thumbnail)."""
        result = await generate_thumbnail(sample_jpg, "jpg", "test-id", test_settings)
        assert result is None
        assert not any(Path(test_settings.THUMBNAIL_DIR).glob("test-id*"))

    async def test_real_png_thumbnail(self, tmp_path: Path, test_settings: Settings):
        """Echter Pillow-Pfad: Bild wird auf THUMBNAIL_MAX_SIZE verkleinert."""
        from PIL import Image

        src = tmp_path / "scan.png"
        Image.new("RGB", (1200, 800), "white").save(src)

        result = await generate_thumbnail(src, "png", "thumb-id", test_settings)

        assert result is not None and result.exists()
        with Image.open(result) as thumb:
            assert thumb.format == "PNG"
            assert max(thumb.size) <= test_settings.THUMBNAIL_MAX_SIZE
            assert thumb.size[0] > thumb.size[1]  # Seitenverhaeltnis erhalten

    @pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
    async def test_decompression_bomb_rejected(self, tmp_path: Path, test_settings: Settings):
        """S-LOW3: Bilder ueber MAX_IMAGE_PIXELS liefern None statt RAM-Explosion."""
        from PIL import Image

        src = tmp_path / "bomb.png"
        Image.new("1", (10_000, 7_000)).save(src)  # 70 Mpx > 64 Mpx-Limit

        result = await generate_thumbnail(src, "png", "bomb-id", test_settings)
        assert result is None

    async def test_unsupported_type(self, tmp_path: Path, test_settings: Settings):
        dummy = tmp_path / "test.xyz"
        dummy.write_bytes(b"content")
        result = await generate_thumbnail(dummy, "xyz", "test-id", test_settings)
        assert result is None

    async def test_nonexistent_file(self, test_settings: Settings):
        result = await generate_thumbnail(
            Path("/nonexistent/file.jpg"), "jpg", "test-id", test_settings
        )
        assert result is None


class TestNormalizedImageThumbnail:
    async def test_exif_orientation_applied(self, tmp_path: Path, test_settings: Settings):
        """Smartphone-Foto mit EXIF-Orientation 6 (90 Grad) -> Thumbnail hochkant."""
        from PIL import Image

        src = tmp_path / "foto.jpg"
        img = Image.new("RGB", (600, 300), "white")
        exif = img.getexif()
        exif[0x0112] = 6  # Orientation: um 90 Grad im Uhrzeigersinn drehen
        img.save(src, "JPEG", exif=exif.tobytes())

        result = await generate_thumbnail(src, "jpg", "exif-id", test_settings)

        assert result is not None
        with Image.open(result) as thumb:
            assert thumb.size[1] > thumb.size[0]

    async def test_cmyk_jpeg_gets_thumbnail(self, tmp_path: Path, test_settings: Settings):
        from PIL import Image

        src = tmp_path / "cmyk.jpg"
        Image.new("CMYK", (400, 200), (0, 0, 0, 0)).save(src, "JPEG")

        result = await generate_thumbnail(src, "jpg", "cmyk-id", test_settings)

        assert result is not None and result.exists()
        with Image.open(result) as thumb:
            assert thumb.mode in ("RGB", "RGBA", "L")

    async def test_tif_alias(self, tmp_path: Path, test_settings: Settings):
        from PIL import Image

        src = tmp_path / "scan.tif"
        Image.new("L", (400, 200), 255).save(src, "TIFF")

        result = await generate_thumbnail(src, "tif", "tif-id", test_settings)
        assert result is not None and result.exists()

    async def test_pdf_rendered_at_target_size(self, tmp_path: Path, test_settings: Settings):
        """PDF-Thumbnail rendert direkt in Zielgroesse (pdftoppm -scale-to)."""
        from PIL import Image

        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")
        with patch(
            "pdf2image.convert_from_path",
            return_value=[Image.new("RGB", (200, 300), "white")],
        ) as convert:
            result = await generate_thumbnail(src, "pdf", "pdf-id", test_settings)

        assert result is not None
        assert convert.call_args.kwargs["size"] == test_settings.THUMBNAIL_MAX_SIZE
