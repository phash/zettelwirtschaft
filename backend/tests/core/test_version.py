import sys

from app.core import version as v


class TestReadAppVersion:
    def test_version_file_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "VERSION").write_text("1.6.0\n", encoding="utf-8")
        assert v.read_app_version() == "1.6.0"

    def test_data_version_fallback(self, tmp_path, monkeypatch):
        """Docker: Backend-Image hat keine VERSION, Installer schreibt data/.version."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / ".version").write_text("1.5.0", encoding="utf-8")
        assert v.read_app_version() == "1.5.0"

    def test_empty_file_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "VERSION").write_text("  \n", encoding="utf-8")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / ".version").write_text("1.5.0", encoding="utf-8")
        assert v.read_app_version() == "1.5.0"

    def test_unknown_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert v.read_app_version() == v.UNKNOWN_VERSION

    def test_native_frozen_reads_install_root(self, tmp_path, monkeypatch):
        """Native: CWD = <install>/backend (NSSM AppDirectory), VERSION liegt in <install>/."""
        install = tmp_path / "install"
        backend = install / "backend"
        backend.mkdir(parents=True)
        (install / "VERSION").write_text("1.6.0", encoding="utf-8")
        monkeypatch.chdir(backend)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(backend / "zettelwirtschaft-backend.exe"))
        assert v.read_app_version() == "1.6.0"
