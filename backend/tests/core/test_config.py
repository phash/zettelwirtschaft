"""Settings: Quellen-Prioritaet, PIN-Validierung, abgeleitete Werte."""

from datetime import timezone

import pytest
from pydantic import ValidationError

import app.config
from app.config import Settings
from tests.credentials import TEST_PIN


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

    @pytest.mark.parametrize("pin", [TEST_PIN, TEST_PIN[:4]], ids=["test-pin", "min-length-4"])
    def test_valid_pin(self, pin):
        assert Settings(PIN_ENABLED=True, PIN_CODE=pin).PIN_CODE == pin


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


class TestSplitSettings:
    @pytest.mark.parametrize("name", ["SPLIT_MAX_PAGES", "SPLIT_MAX_PARTS"])
    def test_limits_above_the_confirm_body_caps_are_rejected(self, name):
        """Der Bestaetigen-Request begrenzt Seiten je Teil/Ausschluesse und
        Teile (schemas/split.py). Lagen die Grenzen darueber, liesse sich ein
        erlaubter Stapel nicht mehr bestaetigen (422)."""
        from app.schemas.split import SPLIT_PAGES_HARD_LIMIT, SPLIT_PARTS_HARD_LIMIT

        limit = SPLIT_PAGES_HARD_LIMIT if name == "SPLIT_MAX_PAGES" else SPLIT_PARTS_HARD_LIMIT
        assert getattr(Settings(**{name: limit}), name) == limit
        with pytest.raises(ValidationError, match=name):
            Settings(**{name: limit + 1})


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


class TestModelDefaults:
    """Defaults aus dem 70-Dokumente-Benchmark (v1.6). Bestehende Installationen
    haben die Modelle explizit in .env/config.toml und wechseln nicht."""

    def test_llm_default(self):
        assert Settings.model_fields["OLLAMA_MODEL"].default == "gemma4:e2b-it-qat"

    def test_embedding_default(self):
        assert Settings.model_fields["EMBEDDING_MODEL"].default == "qwen3-embedding:4b"

    def test_think_disabled_by_default(self):
        assert Settings.model_fields["OLLAMA_THINK"].default is False

    def test_explicit_values_win(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
        s = Settings(_env_file=None)
        assert (s.OLLAMA_MODEL, s.EMBEDDING_MODEL) == ("qwen2.5:7b-instruct", "bge-m3")
