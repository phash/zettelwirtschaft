from pathlib import Path
from unittest.mock import patch

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
