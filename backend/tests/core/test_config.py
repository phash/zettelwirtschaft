"""Settings: Quellen-Prioritaet, PIN-Validierung, abgeleitete Werte."""

from datetime import timezone

import pytest
from pydantic import ValidationError

import app.config
from app.config import Settings


class TestSourcePriority:
    def test_toml_beats_dotenv_and_env_beats_toml(self, tmp_path, monkeypatch):
        """Regression: .env ueberstimmte config.toml (Reihenfolge env > dotenv > toml)."""
        (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\nOCR_LANGUAGES=deu\n", encoding="utf-8")
        toml = tmp_path / "config.toml"
        toml.write_text('LOG_LEVEL = "WARNING"\nMAX_OCR_PAGES = 3\n', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("MAX_OCR_PAGES", raising=False)
        monkeypatch.delenv("OCR_LANGUAGES", raising=False)

        class TomlSettings(Settings):
            model_config = {**Settings.model_config, "toml_file": str(toml)}

        settings = TomlSettings()
        assert settings.LOG_LEVEL == "WARNING"  # TOML > .env
        assert settings.MAX_OCR_PAGES == 3  # nur TOML
        assert settings.OCR_LANGUAGES == "deu"  # nur .env

        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        assert TomlSettings().LOG_LEVEL == "ERROR"  # ENV > TOML


class TestPinValidation:
    @pytest.mark.parametrize("pin", ["", "   ", "123"])
    def test_pin_enabled_requires_pin_code(self, pin):
        with pytest.raises(ValidationError, match="PIN_CODE"):
            Settings(PIN_ENABLED=True, PIN_CODE=pin)

    def test_pin_disabled_allows_empty_code(self):
        assert Settings(PIN_ENABLED=False, PIN_CODE="").PIN_ENABLED is False

    def test_valid_pin(self):
        assert Settings(PIN_ENABLED=True, PIN_CODE="4711").PIN_CODE == "4711"


class TestDerivedValues:
    def test_sqlite_db_path(self, tmp_path):
        db = tmp_path / "x.db"
        assert Settings(DATABASE_URL=f"sqlite+aiosqlite:///{db}").sqlite_db_path == db
        assert Settings(DATABASE_URL="sqlite:///./data/z.db").sqlite_db_path.as_posix() == "data/z.db"

    def test_sqlite_db_path_rejects_other_urls(self):
        with pytest.raises(ValueError):
            Settings(DATABASE_URL="postgresql://x/y").sqlite_db_path

    def test_data_dir_is_parent_of_archive(self, tmp_path):
        assert Settings(ARCHIVE_DIR=str(tmp_path / "archive")).data_dir == tmp_path


class TestDeploymentMode:
    def test_dev(self, monkeypatch):
        monkeypatch.setattr(app.config, "_is_frozen", lambda: False)
        monkeypatch.setattr(app.config, "_in_container", lambda: False)
        assert Settings(FRONTEND_DIST_DIR="").deployment_mode == "dev"

    def test_docker(self, monkeypatch):
        monkeypatch.setattr(app.config, "_is_frozen", lambda: False)
        monkeypatch.setattr(app.config, "_in_container", lambda: True)
        assert Settings(FRONTEND_DIST_DIR="").deployment_mode == "docker"

    def test_native_via_frozen_bundle(self, monkeypatch):
        monkeypatch.setattr(app.config, "_is_frozen", lambda: True)
        monkeypatch.setattr(app.config, "_in_container", lambda: True)
        assert Settings().deployment_mode == "native"

    def test_native_via_frontend_dist(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app.config, "_is_frozen", lambda: False)
        monkeypatch.setattr(app.config, "_in_container", lambda: False)
        assert Settings(FRONTEND_DIST_DIR=str(tmp_path)).deployment_mode == "native"
        # gesetzt, aber nicht vorhanden -> kein Native
        assert Settings(FRONTEND_DIST_DIR=str(tmp_path / "fehlt")).deployment_mode == "dev"

    def test_trusted_proxies_only_in_docker(self, monkeypatch):
        monkeypatch.setattr(app.config, "_is_frozen", lambda: False)
        monkeypatch.setattr(app.config, "_in_container", lambda: False)
        assert Settings().trusted_proxy_networks == []
        monkeypatch.setattr(app.config, "_in_container", lambda: True)
        assert [str(n) for n in Settings().trusted_proxy_networks] == ["172.16.0.0/12"]


class TestEmailSettings:
    @pytest.mark.parametrize("name", ["IMAP_TIMEOUT", "EMAIL_MAX_PER_RUN"])
    def test_non_positive_values_are_rejected(self, name):
        # IMAP_TIMEOUT=0 schaltet den Socket non-blocking, Limit 0 holt nie etwas.
        with pytest.raises(ValidationError, match=name):
            Settings(**{name: 0})

    def test_local_tz_uses_configured_zone(self):
        assert str(Settings(TIMEZONE="Europe/Berlin").local_tz) == "Europe/Berlin"

    def test_invalid_timezone_falls_back_to_utc_without_tzdata(self):
        """Fallback darf nicht selbst ZoneInfo (= tzdata) brauchen."""
        assert Settings(TIMEZONE="Mars/Olympus").local_tz is timezone.utc
