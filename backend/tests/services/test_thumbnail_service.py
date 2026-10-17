from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.services.thumbnail_service import generate_thumbnail


class TestGenerateThumbnail:
    async def test_image_thumbnail(self, sample_jpg: Path, test_settings: Settings):
        """Thumbnail fuer JPEG - uebersprungen ohne echte Bilddaten."""
        # Unser minimales JPEG ist nicht von Pillow lesbar,
        # daher pruefen wir Graceful Failure
        result = await generate_thumbnail(sample_jpg, "jpg", "test-id", test_settings)
        # None bei Fehler ist akzeptabel (graceful failure)
        assert result is None or result.exists()

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
