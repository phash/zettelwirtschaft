import ipaddress
import logging
import os
import sys
from datetime import timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import model_validator
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

DeploymentMode = Literal["native", "docker", "dev"]

# Marker-Dateien, die Docker bzw. Podman in jedem Container anlegen.
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def _is_frozen() -> bool:
    """True im PyInstaller-Bundle (Native-Windows-Setup)."""
    return bool(getattr(sys, "frozen", False))


def _in_container() -> bool:
    return any(os.path.exists(marker) for marker in _CONTAINER_MARKERS)


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
        # Reihenfolge (frueher gewinnt): init > env > toml > dotenv > secrets.
        # ENV-Vars haben Vorrang vor TOML (wichtig fuer Tests, Docker-Compose,
        # Override im Service-Manager). TOML hat Vorrang vor .env: die explizit
        # per ZETTELWIRTSCHAFT_CONFIG gewaehlte Datei darf nicht von einer
        # zufaellig im Arbeitsverzeichnis liegenden .env ueberstimmt werden.
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if cls.model_config.get("toml_file"):
            sources.append(TomlConfigSettingsSource(settings_cls))
        sources.extend([dotenv_settings, file_secret_settings])
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
    # Netzwerkpfade (\\server\freigabe) als Watch-/Export-Ordner in der Web-UI
    # erlauben (Native/Dev). Standard AUS (FH-01): der Native-Dienst laeuft als
    # LocalSystem — schon die Pfadpruefung wuerde das Maschinenkonto am
    # angegebenen Server anmelden (NTLM-Relay), der Export ginge dorthin.
    # Nur auf einem Host mit vertrauenswuerdiger Freigabe einschalten.
    # Seit 1.7 fuer den normalen Ablauf nicht mehr noetig: in den Einstellungen
    # \\server\freigabe mit Benutzer + Passwort der Freigabe eintragen — das
    # Backend verbindet sich dann selbst (core/network_share.py). Der Schalter
    # bleibt Experten-Override fuer UNC OHNE Zugangsdaten (Computerkonto).
    ALLOW_NETWORK_FOLDERS: bool = False
    # Pruefintervall (Sekunden, 10-600) fuer Netzwerk-Watch-Ordner, falls in
    # der UI nichts gespeichert ist (SMB-Aenderungsbenachrichtigungen sind
    # ueber das Netz unzuverlaessig -> Polling).
    WATCH_POLL_INTERVAL: int = 30
    # Auch lokale Watch-Ordner per Polling statt Dateisystem-Ereignissen
    # ueberwachen (z.B. Docker mit CIFS-Volume: inotify sieht dort nichts).
    WATCH_FORCE_POLLING: bool = False
    # Optional: Pfad zum Frontend-Build (Native: vom Installer gesetzt).
    # Leer = nicht serven (Docker-Pfad ueber nginx).
    FRONTEND_DIST_DIR: str = ""

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Default laut 70-Dokumente-Benchmark: gemma4:e2b-it-qat (Kernfelder 84,7 %,
    # 4,3 GB, CPU ~40 s/Dokument). Die Installer schlagen bei NVIDIA-GPU mit
    # >= 10 GB VRAM gemma4:12b-it-qat vor (90 %, 7,2 GB). gemma4 "denkt"
    # ohne think=false — siehe OLLAMA_THINK. Bestehende Installationen haben
    # den Wert explizit in .env/config.toml (bisheriger Default qwen2.5:7b-instruct).
    OLLAMA_MODEL: str = "gemma4:e2b-it-qat"
    # SEC-DATA-06: Fehlende OLLAMA_MODEL/EMBEDDING_MODEL laedt das Backend beim
    # Start ueber Ollama aus dessen oeffentlicher Registry (registry.ollama.ai,
    # mehrere GB; sichtbar sind dort nur IP + Modellname, keine Dokumente).
    # false = kein automatischer Download (Offline-/Volumentarif-Betrieb); der
    # Systemstatus meldet das fehlende Modell, Download dann per `ollama pull`.
    OLLAMA_AUTO_PULL: bool = True
    OLLAMA_TIMEOUT: int = 300
    OLLAMA_MAX_RETRIES: int = 2
    # Lese-Timeout (Sekunden) fuer einen JSON-Analyse-Aufruf (Dokumentanalyse,
    # Verifier, Reranker, E-Mail-Relevanz). Timeouts werden NICHT wiederholt —
    # ein ueberlastetes Modell wuerde sonst ein Vielfaches davon blockieren.
    OLLAMA_ANALYSIS_TIMEOUT: int = 120
    # Kontextfenster pro Aufruf (Chat + Embedding). Ollamas Default (4096)
    # schneidet lange Belege (Kontoauszuege) stillschweigend ab.
    OLLAMA_NUM_CTX: int = 8192
    # Obergrenze erzeugter Tokens fuer JSON-Antworten (Schutz gegen
    # Endlos-Ausgaben); Freitext-Antworten (Chat) sind nicht begrenzt.
    OLLAMA_NUM_PREDICT: int = 1024
    # Wie lange Ollama das Modell nach einem Aufruf im RAM haelt.
    OLLAMA_KEEP_ALIVE: str = "30m"
    # "Thinking" bei Hybrid-Modellen (gemma4, qwen3 u.a. — gemma4 denkt ohne
    # think=false standardmaessig). Aus = schnelle Antworten; wird an Modelle
    # gesendet, die laut /api/show "thinking" koennen oder deren Capabilities
    # unbekannt sind (lehnt Ollama das Feld ab, wird ohne wiederholt).
    OLLAMA_THINK: bool = False

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

    # Reverse-Proxy-Netze (kommagetrennte CIDRs), deren X-Real-IP-/X-Forwarded-
    # Proto-Header uebernommen werden. Wirkt NUR im Docker-Betrieb (nginx vor dem
    # nur intern erreichbaren Backend); Native/Dev werten die Header nie aus und
    # nehmen immer die Socket-Adresse — sonst koennte jeder LAN-Client seine
    # Lockout-/Rate-Limit-Identitaet frei waehlen. Default: Docker-Adresspool
    # fuer Bridge-/Compose-Netze.
    TRUSTED_PROXIES: str = "172.16.0.0/12"

    # AUTH-02 (DNS-Rebinding-Schutz): zusaetzlich erlaubte Hostnamen im
    # Host-Header (kommagetrennt, z.B. "nas.example.org,*.meine-domain.de").
    # Immer erlaubt sind IP-Adressen, localhost, Namen ohne Punkt (PC-Name),
    # *.local/*.lan/*.home.arpa/*.internal/*.fritz.box sowie der Rechnername.
    # "*" schaltet die Pruefung ab (nicht empfohlen).
    ALLOWED_HOSTS: str = ""

    # AUTH-03: FastAPI-Doku (/docs, /redoc, /openapi.json) ausliefern. Aus per
    # Default — im Native-Betrieb waeren die Seiten sonst ohne PIN im LAN
    # erreichbar und laden Swagger-UI von einem externen CDN.
    ENABLE_API_DOCS: bool = False

    # ChromaDB / RAG
    # Native-Mode: "embedded" laed Chroma als PersistentClient in-process (kein HTTP-Service noetig).
    # Docker-Mode: "http" spricht den separaten chromadb-Container an.
    CHROMADB_MODE: str = "http"  # "embedded" | "http"
    CHROMADB_HOST: str = "localhost"
    CHROMADB_PORT: int = 8000
    # Bei CHROMADB_MODE=embedded: Pfad fuer PersistentClient (Default: <DATA_DIR>/chromadb).
    # Leer = Auto-Default unter ARCHIVE_DIR/.. (data/chromadb).
    CHROMADB_PATH: str = ""
    # Default laut Benchmark: qwen3-embedding:4b (R@1 100 % vs. bge-m3 95,8 %,
    # 2560 dim, 2,5 GB); bei < 12 GB RAM schlagen die Installer
    # qwen3-embedding:0.6b vor. Fragen bekommen bei qwen3-embedding* einen
    # Instruct-Prefix (embedding_service.embed_query), Dokumente nicht.
    # Wechsel = neue Collection (documents_<modell>) -> automatische Neu-
    # Vektorisierung beim Start (vectorize_service.run_initial_vectorize).
    EMBEDDING_MODEL: str = "qwen3-embedding:4b"
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
    # Socket-Timeout (Sekunden) fuer IMAP — gilt fuer Connect und jedes
    # spaetere Lesen. Ohne Timeout haengt ein stummer Server (z.B. SSL aus auf
    # Port 993) den Abruf und den Scheduler fuer immer.
    IMAP_TIMEOUT: int = 30
    # Maximal verarbeitete E-Mails pro Abruf und Konto (aelteste zuerst). Der
    # Rest folgt beim naechsten Scheduler-Tick bzw. naechsten manuellen Abruf.
    EMAIL_MAX_PER_RUN: int = 25

    # Lokale Zeitzone (IANA-Name) — u.a. fuer CRON-Zeitplaene der E-Mail-Konten
    # ("0 8 * * *" = 8 Uhr Ortszeit, inkl. Sommer-/Winterzeit).
    TIMEZONE: str = "Europe/Berlin"

    # Update-Pruefung (opt-in, telemetriefrei). Standardmaessig AUS — die App
    # macht ohne ausdrueckliche Aktivierung keinen externen Aufruf. Der
    # Laufzeit-Schalter liegt in der DB-Einstellung "update_check_enabled"
    # (ueber die Web-UI umlegbar); UPDATE_CHECK_ENABLED ist nur der Default,
    # solange die DB-Einstellung nicht gesetzt ist.
    UPDATE_CHECK_ENABLED: bool = False
    UPDATE_MANIFEST_URL: str = "https://zettelwirtschaft.mr-development.de/latest.json"

    @model_validator(mode="after")
    def _validate_llm_rag_settings(self) -> "Settings":
        # chunk_text braucht Fortschritt pro Schleife: Overlap < halbe Chunk-Groesse.
        if self.RAG_CHUNK_SIZE <= 0:
            raise ValueError("RAG_CHUNK_SIZE muss groesser als 0 sein")
        if not 0 <= self.RAG_CHUNK_OVERLAP < self.RAG_CHUNK_SIZE // 2:
            raise ValueError(
                "RAG_CHUNK_OVERLAP muss zwischen 0 und der halben RAG_CHUNK_SIZE liegen"
            )
        for name in ("CONFIDENCE_THRESHOLD", "LLM_VERIFIER_THRESHOLD"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} muss zwischen 0 und 1 liegen")
        for name in ("OLLAMA_NUM_CTX", "OLLAMA_NUM_PREDICT", "OLLAMA_ANALYSIS_TIMEOUT", "OLLAMA_TIMEOUT"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} muss groesser als 0 sein")
        return self

    @model_validator(mode="after")
    def _validate_email_settings(self) -> "Settings":
        # IMAP_TIMEOUT=0 wuerde den Socket non-blocking schalten, ein Limit 0
        # wuerde nie eine Mail abholen.
        for name in ("IMAP_TIMEOUT", "EMAIL_MAX_PER_RUN"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} muss groesser als 0 sein")
        return self

    @model_validator(mode="after")
    def _validate_pin_settings(self) -> "Settings":
        # Fail-fast: PIN_ENABLED mit leerem/zu kurzem PIN_CODE haette jeden
        # (leeren) PIN akzeptiert und gleichzeitig den Warn-Banner versteckt —
        # das System saehe geschuetzt aus, waere es aber nicht.
        if self.PIN_ENABLED and len(self.PIN_CODE.strip()) < 4:
            raise ValueError(
                "PIN_ENABLED=true erfordert einen PIN_CODE mit mindestens 4 Zeichen "
                "(in .env bzw. config.toml setzen oder PIN_ENABLED=false)"
            )
        for cidr in self._trusted_proxy_cidrs():
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"TRUSTED_PROXIES enthaelt kein gueltiges Netz: {cidr!r}") from exc
        return self

    def _trusted_proxy_cidrs(self) -> list[str]:
        return [c.strip() for c in self.TRUSTED_PROXIES.split(",") if c.strip()]

    @property
    def allowed_file_types_list(self) -> list[str]:
        return [ft.strip() for ft in self.ALLOWED_FILE_TYPES.split(",")]

    @property
    def deployment_mode(self) -> DeploymentMode:
        """Betriebsart — EINZIGE Stelle, an der sie abgeleitet wird.

        - ``native``: Setup.exe-Installation (PyInstaller-Bundle) bzw. Backend
          liefert das Frontend selbst aus (FRONTEND_DIST_DIR gesetzt + vorhanden).
        - ``docker``: Backend laeuft in einem Container hinter nginx.
        - ``dev``: lokaler Entwicklungsstart (uvicorn --reload, Tests).
        """
        if _is_frozen() or (self.FRONTEND_DIST_DIR and Path(self.FRONTEND_DIST_DIR).exists()):
            return "native"
        if _in_container():
            return "docker"
        return "dev"

    @property
    def data_dir(self) -> Path:
        """Datenordner (Elternordner von ARCHIVE_DIR) — Basis fuer backups/,
        .host-mounts.json, .install-path usw."""
        return Path(self.ARCHIVE_DIR).parent

    @property
    def sqlite_db_path(self) -> Path:
        """Dateipfad der SQLite-DB aus DATABASE_URL
        (``sqlite+aiosqlite:///./data/x.db`` -> ``./data/x.db``)."""
        url = self.DATABASE_URL
        for prefix in ("sqlite+aiosqlite:///", "sqlite+pysqlite:///", "sqlite:///"):
            if url.startswith(prefix):
                return Path(url[len(prefix):])
        raise ValueError(f"DATABASE_URL ist keine SQLite-Datei-URL: {url!r}")

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Netze, deren Proxy-Header uebernommen werden — nur im Docker-Betrieb."""
        if self.deployment_mode != "docker":
            return []
        return [ipaddress.ip_network(c, strict=False) for c in self._trusted_proxy_cidrs()]

    @property
    def local_tz(self) -> tzinfo:
        """TIMEZONE als ZoneInfo; ungueltiger Name -> UTC (mit Warnung)."""
        try:
            return ZoneInfo(self.TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError):
            logging.getLogger(__name__).warning(
                "Unbekannte Zeitzone TIMEZONE=%r — verwende UTC", self.TIMEZONE
            )
            # Bewusst nicht ZoneInfo("UTC"): ohne tzdata (Windows) wuerde auch
            # der Fallback scheitern.
            return timezone.utc


@lru_cache
def get_settings() -> Settings:
    return Settings()


def settings_for_app(app) -> Settings:
    """Settings fuer Code ausserhalb der Dependency-Injection (Middleware,
    Rate-Limiter). Respektiert ``app.dependency_overrides`` wie ein
    ``Depends(get_settings)`` — sonst sehen Tests andere Settings als die
    Endpoints. ``app`` darf None sein (reine ASGI-Scopes ohne App)."""
    overrides = getattr(app, "dependency_overrides", None) or {}
    return overrides.get(get_settings, get_settings)()


def settings_for_request(request) -> Settings:
    """Wie ``settings_for_app`` fuer einen Starlette-Request."""
    return settings_for_app(request.app)
