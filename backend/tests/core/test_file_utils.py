from pathlib import Path

from app.core.file_utils import (
    generate_stored_filename,
    get_file_extension,
    sanitize_filename,
    validate_magic_bytes,
)


class TestValidateMagicBytes:
    def test_valid_pdf(self, sample_pdf: Path):
        assert validate_magic_bytes(sample_pdf, "pdf") is True

    def test_valid_jpg(self, sample_jpg: Path):
        assert validate_magic_bytes(sample_jpg, "jpg") is True
        assert validate_magic_bytes(sample_jpg, "jpeg") is True

    def test_valid_png(self, sample_png: Path):
        assert validate_magic_bytes(sample_png, "png") is True

    def test_wrong_extension(self, sample_pdf: Path):
        assert validate_magic_bytes(sample_pdf, "jpg") is False

    def test_unknown_extension(self, sample_pdf: Path):
        assert validate_magic_bytes(sample_pdf, "exe") is False

    def test_nonexistent_file(self, tmp_path: Path):
        assert validate_magic_bytes(tmp_path / "nope.pdf", "pdf") is False

    def test_extension_case_insensitive(self, sample_pdf: Path):
        assert validate_magic_bytes(sample_pdf, "PDF") is True
        assert validate_magic_bytes(sample_pdf, ".pdf") is True


class TestSanitizeFilename:
    def test_normal_filename(self):
        assert sanitize_filename("rechnung_2024.pdf") == "rechnung_2024.pdf"

    def test_special_characters(self):
        result = sanitize_filename('rech<>:"/\\|?*nung.pdf')
        assert "<" not in result
        assert ">" not in result
        assert result.endswith(".pdf")

    def test_path_traversal(self):
        result = sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert result == "passwd"

    def test_empty_after_sanitize(self):
        assert sanitize_filename("...") == "unnamed"

    def test_multiple_underscores_collapsed(self):
        result = sanitize_filename("a___b.pdf")
        assert result == "a_b.pdf"


class TestGenerateStoredFilename:
    def test_has_uuid_prefix(self):
        result = generate_stored_filename("test.pdf")
        parts = result.split("_", 1)
        assert len(parts) == 2
        assert len(parts[0]) == 12  # hex UUID prefix

    def test_preserves_sanitized_name(self):
        result = generate_stored_filename("rechnung.pdf")
        assert result.endswith("rechnung.pdf")

    def test_unique(self):
        a = generate_stored_filename("test.pdf")
        b = generate_stored_filename("test.pdf")
        assert a != b


class TestGetFileExtension:
    def test_pdf(self):
        assert get_file_extension("test.pdf") == "pdf"

    def test_uppercase(self):
        assert get_file_extension("test.PDF") == "pdf"

    def test_jpg(self):
        assert get_file_extension("foto.JPG") == "jpg"

    def test_no_extension(self):
        assert get_file_extension("noext") == ""

    def test_dot_prefix(self):
        assert get_file_extension(".hidden") == ""
