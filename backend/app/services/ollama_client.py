"""Gemeinsamer Ollama-HTTP-Zugriff fuer LLM- und Embedding-Aufrufe.

Buendelt, was llm_service und embedding_service vorher getrennt (und
unterschiedlich) geloest haben:

- ein Singleton-``httpx.AsyncClient`` (H-BE-1), Timeouts pro Request;
- ``post_json`` mit einheitlicher Retry-Politik: Verbindungsfehler und 5xx
  werden mit exponentiellem Backoff wiederholt, Lese-Timeouts und 4xx NICHT
  (ein ueberlastetes Modell wuerde sonst ein Vielfaches des Timeouts blockieren,
  ein fehlendes Modell wird durch Wiederholen nicht installiert);
- typisierte Fehler (``OllamaError.kind``), damit Aufrufer "Modell fehlt" von
  "nicht erreichbar" unterscheiden und dem Benutzer die richtige Meldung zeigen;
- Modell-Checks mit exaktem Tag-Abgleich (``probe_ollama``) und Auto-Pull
  (``ensure_configured_models``) fuer OLLAMA_MODEL und EMBEDDING_MODEL;
- Capability-Cache (``/api/show``) fuer den ``think``-Parameter;
- Versionspruefung (``/api/version``) gegen ``OLLAMA_MIN_VERSION``;
- das Ergebnis des letzten LLM- und Embedding-Aufrufs fuer den System-Health.
"""

import asyncio
import enum
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import Settings

logger = logging.getLogger("zettelwirtschaft.ollama")

# Kurze Timeouts fuer Verbindungsaufbau und Pool: ein nicht laufendes Ollama
# soll in Sekunden auffallen, nicht erst nach dem Lese-Timeout.
_CONNECT_TIMEOUT = 5.0
_PROBE_TIMEOUT = 5.0
# Modell-Download: Lese-Timeout gilt pro gestreamter Fortschrittszeile.
_PULL_READ_TIMEOUT = 600.0

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Singleton-Client (H-BE-1). Timeouts werden pro Request gesetzt (R-03)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def aclose_client() -> None:
    """Sauberes Schliessen beim Shutdown — vom lifespan-Hook aus aufrufbar."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


def backoff_seconds(attempt: int) -> float:
    """H-BE-8: Exponentielles Backoff 2s, 4s, 8s, ... Cap bei 30s."""
    return min(2.0 * (2 ** attempt), 30.0)


class OllamaErrorKind(str, enum.Enum):
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    MODEL_MISSING = "model_missing"
    SERVER_ERROR = "server_error"
    BAD_REQUEST = "bad_request"
    INVALID_RESPONSE = "invalid_response"


_USER_MESSAGES = {
    OllamaErrorKind.UNREACHABLE: "Der KI-Dienst (Ollama) ist nicht erreichbar.",
    OllamaErrorKind.TIMEOUT: "Der KI-Dienst (Ollama) hat nicht rechtzeitig geantwortet.",
    OllamaErrorKind.MODEL_MISSING: "Das KI-Modell „{model}“ ist in Ollama nicht installiert.",
    OllamaErrorKind.SERVER_ERROR: "Der KI-Dienst (Ollama) hat einen internen Fehler gemeldet.",
    OllamaErrorKind.BAD_REQUEST: "Der KI-Dienst (Ollama) hat die Anfrage abgelehnt.",
    OllamaErrorKind.INVALID_RESPONSE: "Der KI-Dienst (Ollama) hat eine unbrauchbare Antwort geliefert.",
}


class OllamaError(Exception):
    """Fehlgeschlagener Ollama-Aufruf mit Fehlerart und Ollamas Fehlertext."""

    def __init__(self, kind: OllamaErrorKind, detail: str = "", model: str | None = None):
        self.kind = kind
        self.detail = detail
        self.model = model
        super().__init__(f"{kind.value}: {detail}" if detail else kind.value)

    @property
    def user_message(self) -> str:
        """Deutsche, fuer Endanwender verstaendliche Meldung."""
        return _USER_MESSAGES[self.kind].format(model=self.model or "?")


# ---------------------------------------------------------------------------
# Letzter Aufruf pro Zweck ("llm" / "embedding") — fuer den System-Health.
# Ein laufendes Ollama mit kaputtem Modell (500 bei jedem Chat) beantwortet
# /api/tags weiterhin mit 200; erst der letzte echte Aufruf zeigt das Problem.
# ---------------------------------------------------------------------------


@dataclass
class CallOutcome:
    at: datetime
    ok: bool
    model: str
    error_kind: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "ok": self.ok,
            "model": self.model,
            "error_kind": self.error_kind,
            "error": self.error,
        }


_last_outcomes: dict[str, CallOutcome] = {}


def _record_outcome(purpose: str, model: str, error: OllamaError | None) -> None:
    _last_outcomes[purpose] = CallOutcome(
        at=datetime.now(timezone.utc),
        ok=error is None,
        model=model,
        error_kind=error.kind.value if error else None,
        error=error.user_message if error else None,
    )


def get_last_outcomes() -> dict[str, dict]:
    """Ergebnis des letzten LLM- und Embedding-Aufrufs, z.B. fuer /api/system/health."""
    return {purpose: outcome.to_dict() for purpose, outcome in _last_outcomes.items()}


def reset_last_outcomes() -> None:
    """Nur fuer Tests."""
    _last_outcomes.clear()


def _error_detail(response: httpx.Response) -> str:
    """Ollamas Fehlertext ({"error": "..."}) oder der Rohtext der Antwort."""
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except (ValueError, json.JSONDecodeError):
        pass
    return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"


def _classify_status(response: httpx.Response, model: str | None) -> OllamaError:
    detail = _error_detail(response)
    status = response.status_code
    if status == 404 and "not found" in detail.lower():
        return OllamaError(OllamaErrorKind.MODEL_MISSING, detail, model)
    if status >= 500:
        return OllamaError(OllamaErrorKind.SERVER_ERROR, f"HTTP {status}: {detail}", model)
    return OllamaError(OllamaErrorKind.BAD_REQUEST, f"HTTP {status}: {detail}", model)


async def post_json(
    path: str,
    payload: dict,
    settings: Settings,
    *,
    timeout: float,
    purpose: str,
) -> dict:
    """POST an Ollama mit einheitlicher Retry-Politik.

    Wiederholt (bis OLLAMA_MAX_RETRIES, Backoff 2/4/8 s): Verbindungsfehler,
    Connect-Timeouts und HTTP 5xx. Nicht wiederholt: Lese-Timeouts, 4xx
    (inkl. "Modell nicht gefunden") und unlesbare Antworten.

    Raises:
        OllamaError: mit passender ``kind``. Das Ergebnis (Erfolg/Fehler) wird
            fuer ``get_last_outcomes`` unter ``purpose`` vermerkt.
    """
    model = payload.get("model")
    url = f"{settings.OLLAMA_BASE_URL}{path}"
    request_timeout = httpx.Timeout(timeout, connect=_CONNECT_TIMEOUT, pool=_CONNECT_TIMEOUT)
    max_retries = settings.OLLAMA_MAX_RETRIES
    client = _get_client()

    error: OllamaError | None = None
    for attempt in range(max_retries + 1):
        retryable = False
        try:
            response = await client.post(url, json=payload, timeout=request_timeout)
            if response.status_code >= 400:
                error = _classify_status(response, model)
                retryable = error.kind == OllamaErrorKind.SERVER_ERROR
            else:
                try:
                    data = response.json()
                except (ValueError, json.JSONDecodeError):
                    error = OllamaError(OllamaErrorKind.INVALID_RESPONSE, "Antwort ist kein JSON", model)
                else:
                    if isinstance(data, dict):
                        _record_outcome(purpose, model or "", None)
                        return data
                    error = OllamaError(OllamaErrorKind.INVALID_RESPONSE, "Antwort ist kein JSON-Objekt", model)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            error = OllamaError(OllamaErrorKind.UNREACHABLE, type(e).__name__, model)
            retryable = True
        except httpx.TimeoutException as e:
            error = OllamaError(OllamaErrorKind.TIMEOUT, f"{type(e).__name__} nach {timeout:.0f}s", model)
        except httpx.HTTPError as e:
            error = OllamaError(OllamaErrorKind.UNREACHABLE, f"{type(e).__name__}: {e}", model)
            retryable = True

        if retryable and attempt < max_retries:
            wait = backoff_seconds(attempt)
            logger.warning(
                "Ollama %s %s (Versuch %d/%d), warte %.1fs ...",
                path, error, attempt + 1, max_retries + 1, wait,
            )
            await asyncio.sleep(wait)
            continue
        break

    assert error is not None
    logger.error("Ollama %s fehlgeschlagen (Modell %s): %s", path, model, error)
    _record_outcome(purpose, model or "", error)
    raise error


# ---------------------------------------------------------------------------
# Modell-Verfuegbarkeit
# ---------------------------------------------------------------------------


def normalize_model_name(name: str) -> str:
    """Ollama-Modellname mit explizitem Tag: "bge-m3" -> "bge-m3:latest".

    Nur das letzte Pfadsegment traegt das Tag ("registry:5000/foo" -> ":latest").
    """
    name = (name or "").strip()
    last_segment = name.rsplit("/", 1)[-1]
    if name and ":" not in last_segment:
        return f"{name}:latest"
    return name


def model_installed(name: str, installed: list[str]) -> bool:
    """Exakter Tag-Abgleich. "qwen2.5:7b" passt NICHT auf "qwen2.5:7b-instruct"."""
    target = normalize_model_name(name)
    return any(normalize_model_name(n) == target for n in installed)


def configured_models(settings: Settings) -> list[str]:
    """Die Modelle, die die App braucht (LLM + Embedding), ohne Duplikate."""
    return list(dict.fromkeys(m for m in (settings.OLLAMA_MODEL, settings.EMBEDDING_MODEL) if m))


async def list_installed_models(settings: Settings) -> list[str]:
    """Modellnamen aus /api/tags. Raises OllamaError."""
    client = _get_client()
    try:
        response = await client.get(
            f"{settings.OLLAMA_BASE_URL}/api/tags",
            timeout=httpx.Timeout(_PROBE_TIMEOUT),
        )
    except httpx.TimeoutException as e:
        raise OllamaError(OllamaErrorKind.TIMEOUT, type(e).__name__) from e
    except httpx.HTTPError as e:
        raise OllamaError(OllamaErrorKind.UNREACHABLE, type(e).__name__) from e
    if response.status_code != 200:
        raise _classify_status(response, None)
    try:
        models = response.json().get("models", []) or []
    except (ValueError, AttributeError) as e:
        raise OllamaError(OllamaErrorKind.INVALID_RESPONSE, "Modellliste unlesbar") from e
    names: list[str] = []
    for m in models:
        for key in ("name", "model"):
            value = m.get(key) if isinstance(m, dict) else None
            if value and value not in names:
                names.append(value)
    return names


@dataclass
class OllamaProbe:
    reachable: bool
    models: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.reachable and not self.missing

    def message(self) -> str | None:
        """Deutsche Statusmeldung, ``None`` wenn alles in Ordnung ist."""
        if not self.reachable:
            return self.error or _USER_MESSAGES[OllamaErrorKind.UNREACHABLE]
        if self.missing:
            loading = [m for m in self.missing if m in _pulling]
            absent = [m for m in self.missing if m not in _pulling]
            parts = []
            if absent:
                noun = "Modell" if len(absent) == 1 else "Modelle"
                parts.append(f"{noun} {', '.join(absent)} nicht installiert")
            if loading:
                noun = "Modell" if len(loading) == 1 else "Modelle"
                verb = "wird" if len(loading) == 1 else "werden"
                parts.append(f"{noun} {', '.join(loading)} {verb} heruntergeladen")
            return "; ".join(parts)
        return None


async def probe_ollama(settings: Settings, required: list[str] | None = None) -> OllamaProbe:
    """Erreichbarkeit + Abgleich der benoetigten Modelle (exakter Tag).

    ``required`` defaultet auf OLLAMA_MODEL und EMBEDDING_MODEL.
    """
    needed = configured_models(settings) if required is None else required
    try:
        models = await list_installed_models(settings)
    except OllamaError as e:
        return OllamaProbe(reachable=False, error=e.user_message)
    missing = [m for m in needed if not model_installed(m, models)]
    return OllamaProbe(reachable=True, models=models, missing=missing)


# ---------------------------------------------------------------------------
# Ollama-Version
# ---------------------------------------------------------------------------

# Mindestversion fuer die Default-Modelle. gemma4 gibt es ab Ollama 0.20.0,
# gemma4:12b ab 0.30.3 (GitHub-Release-Notes v0.20.0 / v0.30.3); fuer die
# QAT-Tags (gemma4:*-it-qat) und qwen3-embedding nennt keine Release-Note eine
# Version. Getestet (70-Dokumente-Benchmark + Live-Smoke) mit 0.40.5 — das
# ist auch die im docker-compose.yml gepinnte Version.
# Gleicher Wert in scripts/model-tiers.ps1 und setup-native.nsi
# (tests/test_docs_consistency.py prueft das).
OLLAMA_MIN_VERSION = "0.30.3"
OLLAMA_TESTED_VERSION = "0.40.5"


def parse_version(version: str | None) -> tuple[int, int, int] | None:
    """ "0.40.5" -> (0, 40, 5); "0.40.6-rc0" -> (0, 40, 6); sonst ``None``.

    Entwickler-Builds melden "0.0.0" — das ist keine echte Version, ``None``.
    """
    if not version:
        return None
    core = version.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    numbers = tuple(int(p) for p in parts) + (0,) * (3 - len(parts))
    if numbers == (0, 0, 0):
        return None
    return numbers  # type: ignore[return-value]


def version_warning(version: str | None, minimum: str = OLLAMA_MIN_VERSION) -> str | None:
    """Deutsche Warnung, wenn ``version`` aelter als ``minimum`` ist.

    Unbekannte/unlesbare Versionen ergeben keine Warnung (kein Fehlalarm bei
    Entwickler-Builds oder Ollama-Varianten ohne /api/version).
    """
    current, required = parse_version(version), parse_version(minimum)
    if current is None or required is None or current >= required:
        return None
    return f"Ollama {version} ist älter als {minimum} — bitte aktualisieren"


async def get_ollama_version(settings: Settings) -> str | None:
    """Version laut /api/version, ``None`` wenn nicht ermittelbar."""
    client = _get_client()
    try:
        response = await client.get(
            f"{settings.OLLAMA_BASE_URL}/api/version",
            timeout=httpx.Timeout(_PROBE_TIMEOUT),
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


# ---------------------------------------------------------------------------
# Gemeinsamer Gesundheitszustand fuer /api/health und /api/system/health.
# M-2 (Re-Review): TTL-Cache — Docker-Healthcheck (alle 30 s) + Frontend-Polling
# wuerden Ollama sonst mit /api/tags-Aufrufen beschaeftigen.
# ---------------------------------------------------------------------------

HEALTH_TTL_SECONDS = 5.0


@dataclass
class OllamaHealth:
    status: str  # "ok" | "warning" — nie "error": ohne KI laeuft die App weiter
    message: str | None
    probe: OllamaProbe
    version: str | None = None

    def configured_models(self, settings: Settings) -> list[dict]:
        """Benoetigte Modelle mit Verfuegbarkeit (exakter Tag) fuer die UI."""
        entries = [("llm", settings.OLLAMA_MODEL), ("embedding", settings.EMBEDDING_MODEL)]
        return [
            {
                "name": name,
                "purpose": purpose,
                "available": self.probe.reachable and model_installed(name, self.probe.models),
                "downloading": name in _pulling,
            }
            for purpose, name in entries
            if name
        ]


_health_cache: tuple[float, OllamaHealth] | None = None


async def ollama_health(settings: Settings) -> OllamaHealth:
    """Erreichbarkeit, benoetigte Modelle (exakter Tag) und letzter LLM-Aufruf.

    Ein Ollama, dessen Modell bei jedem Aufruf mit 500 abbricht, beantwortet
    /api/tags weiterhin — deshalb zaehlt auch das Ergebnis des letzten
    Aufrufs. Eine zu alte Ollama-Version (``OLLAMA_MIN_VERSION``) wird
    zusaetzlich gemeldet. Alle Probleme sind "warning": ein Container-Neustart
    hilft nicht, Dokumente landen bis dahin mit klarer Rueckfrage in der Pruefung.
    """
    global _health_cache
    now = time.monotonic()
    if _health_cache is not None and (now - _health_cache[0]) < HEALTH_TTL_SECONDS:
        return _health_cache[1]

    probe = await probe_ollama(settings)
    version = await get_ollama_version(settings) if probe.reachable else None
    message = probe.message()
    if message is None:
        last_llm = _last_outcomes.get("llm")
        if last_llm and not last_llm.ok and last_llm.model == settings.OLLAMA_MODEL:
            message = f"Letzter KI-Aufruf fehlgeschlagen: {last_llm.error}"
    outdated = version_warning(version)
    if outdated:
        message = f"{outdated}; {message}" if message else outdated
    health = OllamaHealth(
        status="ok" if message is None else "warning", message=message, probe=probe, version=version,
    )
    _health_cache = (now, health)
    return health


def reset_health_cache() -> None:
    """Nur fuer Tests."""
    global _health_cache
    _health_cache = None


# Modelle, deren Download gerade laeuft (fuer die Health-Meldung).
_pulling: set[str] = set()


async def pull_model(settings: Settings, name: str) -> bool:
    """Laedt ein Modell per /api/pull (gestreamt, Fortschritt im Log)."""
    _pulling.add(name)
    try:
        return await _pull_model(settings, name)
    finally:
        _pulling.discard(name)


async def _pull_model(settings: Settings, name: str) -> bool:
    client = _get_client()
    timeout = httpx.Timeout(_PULL_READ_TIMEOUT, connect=_CONNECT_TIMEOUT, pool=_CONNECT_TIMEOUT)
    logger.info("Lade Ollama-Modell '%s' herunter ...", name)
    last_logged = 0.0
    last_percent = -10
    try:
        async with client.stream(
            "POST",
            f"{settings.OLLAMA_BASE_URL}/api/pull",
            # "name" fuer aeltere Ollama-Versionen, "model" fuer neuere.
            json={"model": name, "name": name, "stream": True},
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                await response.aread()
                logger.error("Pull von '%s' fehlgeschlagen: %s", name, _error_detail(response))
                return False
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    logger.error("Pull von '%s' fehlgeschlagen: %s", name, event["error"])
                    return False
                status = event.get("status", "")
                if status == "success":
                    logger.info("Ollama-Modell '%s' bereit", name)
                    return True
                total, completed = event.get("total"), event.get("completed")
                if total and completed is not None:
                    percent = int(completed * 100 / total)
                    now = time.monotonic()
                    if percent >= last_percent + 10 or now - last_logged > 30:
                        logger.info("Pull '%s': %s %d%%", name, status, percent)
                        last_percent, last_logged = percent, now
    except httpx.HTTPError as e:
        logger.error("Pull von '%s' fehlgeschlagen: %s", name, type(e).__name__)
        return False
    logger.error("Pull von '%s' ohne Erfolgsmeldung beendet", name)
    return False


async def ensure_model(settings: Settings, name: str) -> bool:
    """Stellt sicher, dass ``name`` installiert ist (exakter Tag), sonst Pull."""
    probe = await probe_ollama(settings, required=[name])
    if not probe.reachable:
        logger.warning("Ollama nicht erreichbar — Modell '%s' kann nicht geprueft werden", name)
        return False
    if not probe.missing:
        logger.info("Ollama-Modell '%s' bereits vorhanden", name)
        return True
    if not await pull_model(settings, name):
        return False
    # Capabilities des neu geladenen Modells frisch abfragen.
    _capabilities_cache.pop((settings.OLLAMA_BASE_URL, name), None)
    return True


# Native: der Backend-Dienst startet beim Booten, Ollama (Tray-App bzw. Dienst)
# oft erst Sekunden bis Minuten spaeter — ohne Warten liefe der Auto-Pull dann
# nie. Docker wartet ueber depends_on/service_healthy ohnehin auf Ollama.
STARTUP_WAIT_SECONDS = 600.0
_STARTUP_POLL_SECONDS = 15.0


async def wait_until_reachable(settings: Settings, timeout: float) -> bool:
    """Wartet bis zu ``timeout`` Sekunden darauf, dass Ollama antwortet.

    Nur "nicht erreichbar"/Timeout zaehlt als "noch nicht da"; antwortet Ollama
    mit einem anderen Fehler, laeuft es — die Modell-Pruefung meldet den Rest.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            await list_installed_models(settings)
            return True
        except OllamaError as e:
            if e.kind not in (OllamaErrorKind.UNREACHABLE, OllamaErrorKind.TIMEOUT):
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_STARTUP_POLL_SECONDS, remaining))


async def ensure_configured_models(
    settings: Settings, *, wait_seconds: float = STARTUP_WAIT_SECONDS,
) -> dict[str, bool]:
    """Startup-Hintergrundtask: Embedding- und Analyse-Modell sicherstellen.

    Wartet zuerst bis zu ``wait_seconds`` auf Ollama (siehe STARTUP_WAIT_SECONDS).
    Das (kleinere) Embedding-Modell zuerst, damit Chat/Suche frueher bereit sind.
    """
    order = list(dict.fromkeys(m for m in (settings.EMBEDDING_MODEL, settings.OLLAMA_MODEL) if m))
    if not await wait_until_reachable(settings, wait_seconds):
        logger.warning(
            "Ollama nach %.0fs nicht erreichbar — Modelle %s werden nicht geprueft",
            wait_seconds, ", ".join(order),
        )
        return {name: False for name in order}
    results: dict[str, bool] = {}
    for name in order:
        try:
            results[name] = await ensure_model(settings, name)
        except Exception:
            logger.warning("Modell-Check fuer '%s' fehlgeschlagen", name, exc_info=True)
            results[name] = False
    return results


# ---------------------------------------------------------------------------
# Capabilities (/api/show) — fuer den think-Parameter
# ---------------------------------------------------------------------------

# (base_url, model) -> Capabilities. Nur erfolgreiche Antworten werden gecacht;
# aeltere Ollama-Versionen ohne "capabilities"-Feld -> leeres frozenset.
_capabilities_cache: dict[tuple[str, str], frozenset[str]] = {}


async def model_capabilities(settings: Settings, model: str) -> frozenset[str] | None:
    """Capabilities eines Modells laut /api/show, ``None`` wenn unbekannt."""
    key = (settings.OLLAMA_BASE_URL, model)
    if key in _capabilities_cache:
        return _capabilities_cache[key]
    client = _get_client()
    try:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/show",
            json={"model": model},
            timeout=httpx.Timeout(_PROBE_TIMEOUT),
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    caps = data.get("capabilities") if isinstance(data, dict) else None
    result = frozenset(str(c) for c in caps) if isinstance(caps, list) else frozenset()
    _capabilities_cache[key] = result
    return result


def forget_capability(settings: Settings, model: str, capability: str) -> None:
    """Markiert eine Capability als nicht unterstuetzt (z.B. nach 400 auf "think")."""
    key = (settings.OLLAMA_BASE_URL, model)
    _capabilities_cache[key] = _capabilities_cache.get(key, frozenset()) - {capability}


def reset_capabilities_cache() -> None:
    """Nur fuer Tests."""
    _capabilities_cache.clear()
