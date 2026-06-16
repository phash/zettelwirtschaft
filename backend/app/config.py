import os
from functools import lru_cache

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


# Native-Windows-Setup: ein optionaler TOML-Pfad ueber ZETTELWIRTSCHAFT_CONFIG.
# Wenn die Datei existiert, hat TOML Vorrang vor .env (aber Env-Vars selbst
# ueberschreiben TOML — z.B. fuer Tests + Docker-Compose).
# Wenn nicht gesetzt oder Datei fehlt, bleibt der bestehende .env-Pfad aktiv.
_TOML_PATH = os.environ.get("ZETTELWIRTSCHAFT_CONFIG", "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file=_TOML_PATH if (_TOML_PATH and os.path.exists(_TOML_PATH)) else None,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Reihenfolge: init > env > dotenv > toml > secrets.
        # ENV-Vars haben Vorrang vor TOML (wichtig fuer Tests, Docker-Compose,
        # Override im Service-Manager). TOML hat Vorrang vor .env, weil Native
        # ohne .env laeuft.
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        if cls.model_config.get("toml_file"):
            sources.append(TomlConfigSettingsSource(settings_cls))
        sources.append(file_secret_settings)
        return tuple(sources)

    # Native-Mode HTTP-Bind (in Docker wird uvicorn ueber entrypoint.sh mit
    # 0.0.0.0:8000 gestartet; in Native ueber app.entrypoint mit diesen Werten).
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8080

    # Native-Mode HTTPS (optional). Wenn BEIDE Pfade gesetzt sind und existieren,
    # startet uvicorn mit TLS — noetig fuer Smartphone-Scan (Kamera-API) und
    # PWA-Service-Worker im LAN (Secure Context). Zertifikat erzeugen mit
    # ssl/generate-self-signed-cert.py. Im Docker-Pfad uebernimmt das nginx.
    SERVER_SSL_CERTFILE: str = ""
    SERVER_SSL_KEYFILE: str = ""

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/zettelwirtschaft.db"
    UPLOAD_DIR: str = "./data/uploads"
    WATCH_DIR: str = "./data/watch"
    ARCHIVE_DIR: str = "./data/archive"
    EXPORT_DIR: str = ""  # Leer = deaktiviert; absoluter Pfad zu einem externen Zielordner
    # Optional: Pfad zum Frontend-Build (Native: vom Installer gesetzt).
    # Leer = nicht serven (Docker-Pfad ueber nginx).
    FRONTEND_DIST_DIR: str = ""

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b-instruct"
    OLLAMA_TIMEOUT: int = 300
    OLLAMA_MAX_RETRIES: int = 2

    OCR_LANGUAGES: str = "deu+eng"
    CONFIDENCE_THRESHOLD: float = 0.7
    MAX_OCR_PAGES: int = 10

    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_FILE_TYPES: str = "pdf,jpg,jpeg,png,tiff,bmp"
    THUMBNAIL_DIR: str = "./data/thumbnails"
    THUMBNAIL_MAX_SIZE: int = 300
    QUEUE_POLL_INTERVAL: int = 5
    MAX_RETRIES: int = 3
    LOG_LEVEL: str = "INFO"

    PIN_ENABLED: bool = False
    PIN_CODE: str = ""
    PIN_SESSION_TIMEOUT_MINUTES: int = 1440

    # ChromaDB / RAG
    # Native-Mode: "embedded" laed Chroma als PersistentClient in-process (kein HTTP-Service noetig).
    # Docker-Mode: "http" spricht den separaten chromadb-Container an.
    CHROMADB_MODE: str = "http"  # "embedded" | "http"
    CHROMADB_HOST: str = "localhost"
    CHROMADB_PORT: int = 8000
    # Bei CHROMADB_MODE=embedded: Pfad fuer PersistentClient (Default: <DATA_DIR>/chromadb).
    # Leer = Auto-Default unter ARCHIVE_DIR/.. (data/chromadb).
    CHROMADB_PATH: str = ""
    EMBEDDING_MODEL: str = "bge-m3"
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 150
    RAG_TOP_K: int = 5
    # Optionaler LLM-basierter Re-Ranker zwischen Hybrid-Retrieval und Antwort-
    # Generation. Standardmaessig aus, weil ein zusaetzlicher LLM-Call die
    # Latenz pro Frage verdoppelt — fuer Genauigkeits-fokussierte Setups
    # (groessere Dokumentmenge, wichtig dass die richtigen Chunks angefuehrt
    # werden) lohnt es sich.
    RAG_USE_RERANKER: bool = False
    # Verifier-Pass: bei niedriger Konfidenz im Erstdurchlauf wird die
    # extrahierte JSON-Antwort vom LLM nochmal validiert.
    LLM_USE_VERIFIER: bool = False
    LLM_VERIFIER_THRESHOLD: float = 0.5

    # E-Mail
    EMAIL_ENCRYPTION_KEY: str = ""

    # Update-Pruefung (opt-in, telemetriefrei). Standardmaessig AUS — die App
    # macht ohne ausdrueckliche Aktivierung keinen externen Aufruf. Der
    # Laufzeit-Schalter liegt in der DB-Einstellung "update_check_enabled"
    # (ueber die Web-UI umlegbar); UPDATE_CHECK_ENABLED ist nur der Default,
    # solange die DB-Einstellung nicht gesetzt ist.
    UPDATE_CHECK_ENABLED: bool = False
    UPDATE_MANIFEST_URL: str = "https://zettelwirtschaft.mr-development.de/latest.json"

    @property
    def allowed_file_types_list(self) -> list[str]:
        return [ft.strip() for ft in self.ALLOWED_FILE_TYPES.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
