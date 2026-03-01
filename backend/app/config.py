from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/zettelwirtschaft.db"
    UPLOAD_DIR: str = "./data/uploads"
    WATCH_DIR: str = "./data/watch"
    ARCHIVE_DIR: str = "./data/archive"
    EXPORT_DIR: str = ""  # Leer = deaktiviert; absoluter Pfad zu einem externen Zielordner

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
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
    CHROMADB_HOST: str = "localhost"
    CHROMADB_PORT: int = 8000
    EMBEDDING_MODEL: str = "nomic-embed-text"
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 150
    RAG_TOP_K: int = 5

    @property
    def allowed_file_types_list(self) -> list[str]:
        return [ft.strip() for ft in self.ALLOWED_FILE_TYPES.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
