from pathlib import Path

import pytest

from app.config import Settings
from app.services.file_validation_service import FileValidationError, validate_file


class TestValidateFile:
    def test_valid_pdf(self, sample_pdf: Path, test_settings: Settings):
        validate_file(sample_pdf, "rechnung.pdf", sample_pdf.stat().st_size, test_settings)

    def test_valid_jpg(self, sample_jpg: Path, test_settings: Settings):
        validate_file(sample_jpg, "foto.jpg", sample_jpg.stat().st_size, test_settings)

    def test_invalid_extension(self, sample_invalid_file: Path, test_settings: Settings):
        with pytest.raises(FileValidationError, match="nicht erlaubt"):
            validate_file(
                sample_invalid_file,
                "malware.exe",
                sample_invalid_file.stat().st_size,
                test_settings,
            )

    def test_file_too_large(self, sample_pdf: Path, test_settings: Settings):
        test_settings.MAX_UPLOAD_SIZE_MB = 0  # 0 MB = nichts erlaubt
        with pytest.raises(FileValidationError, match="zu gross"):
            validate_file(sample_pdf, "big.pdf", 999_999_999, test_settings)

    def test_magic_bytes_mismatch(self, sample_invalid_file: Path, tmp_path: Path, test_settings: Settings):
        # Datei mit .pdf-Extension aber EXE-Inhalt
        fake_pdf = tmp_path / "fake.pdf"
        fake_pdf.write_bytes(b"MZ\x90\x00not a pdf")
        with pytest.raises(FileValidationError, match="Dateiinhalt"):
            validate_file(fake_pdf, "fake.pdf", fake_pdf.stat().st_size, test_settings)
