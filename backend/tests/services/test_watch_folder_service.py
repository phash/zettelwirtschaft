from pathlib import Path

from app.config import Settings
from app.services.watch_folder_service import _move_to_rejected


class TestMoveToRejected:
    def test_moves_file(self, tmp_path: Path, test_settings: Settings):
        test_file = tmp_path / "bad.exe"
        test_file.write_bytes(b"bad content")

        _move_to_rejected(test_file, test_settings)

        rejected_dir = Path(test_settings.WATCH_DIR) / "rejected"
        assert (rejected_dir / "bad.exe").exists()
        assert not test_file.exists()

    def test_creates_rejected_dir(self, tmp_path: Path, test_settings: Settings):
        rejected_dir = Path(test_settings.WATCH_DIR) / "rejected"
        assert not rejected_dir.exists()

        test_file = tmp_path / "bad.txt"
        test_file.write_bytes(b"bad")

        _move_to_rejected(test_file, test_settings)

        assert rejected_dir.exists()
