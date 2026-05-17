import asyncio
import logging
from pathlib import Path

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# H-BE-1: Singleton-Client statt pro-Aufruf neuem AsyncClient. Spart TCP +
# TLS-Handshakes (qwen2.5+verifier+reranker = bis zu 4 Calls pro Dokument).
# Lazy-Init mit aktuellem Timeout. Beim ersten Aufruf mit neuem Timeout-Wert
# wird der Client neu gebaut.
_client: httpx.AsyncClient | None = None
_client_timeout: float | None = None


def _get_client(timeout: float) -> httpx.AsyncClient:
    global _client, _client_timeout
    if _client is None or _client_timeout != timeout:
        if _client is not None:
            # Alten Client schliessen, nicht via Event-Loop sondern best-effort.
            try:
                _client._transport.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        _client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        _client_timeout = timeout
    return _client


async def aclose_client() -> None:
    """Sauberes Schliessen beim Shutdown — vom lifespan-Hook aus aufrufbar."""
    global _client, _client_timeout
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None
            _client_timeout = None


def load_prompt_template(name: str) -> str:
    """Laedt ein Prompt-Template aus dem prompts-Verzeichnis.

    Args:
        name: Dateiname ohne Pfad (z.B. "analyze_document.txt").

    Returns:
        Inhalt der Template-Datei.

    Raises:
        FileNotFoundError: Wenn das Template nicht existiert.
    """
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt-Template nicht gefunden: {path}")
    return path.read_text(encoding="utf-8")


def _backoff_seconds(attempt: int) -> float:
    """H-BE-8: Exponentielles Backoff 2s, 4s, 8s, ... Cap bei 30s."""
    return min(2.0 * (2 ** attempt), 30.0)


async def _call_ollama(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
    response_format: str | dict | None = None,
    temperature: float = 0.1,
) -> str | None:
    """Gemeinsame Ollama-Aufruflogik mit Retry.

    Args:
        prompt: Der User-Prompt fuer das LLM.
        settings: App-Konfiguration.
        system_prompt: Optionaler System-Prompt.
        response_format: "json" fuer JSON-Output, dict fuer JSON-Schema-constrained
            Generation (Ollama 0.5+), None fuer Freitext.
        temperature: LLM-Temperature (0.1 fuer JSON, 0.3 fuer Text).

    Returns:
        Die LLM-Antwort als String, oder None bei Fehler.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if response_format:
        payload["format"] = response_format

    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    label = "Schema" if isinstance(response_format, dict) else ("JSON" if response_format else "Text")

    client = _get_client(settings.OLLAMA_TIMEOUT)
    max_retries = settings.OLLAMA_MAX_RETRIES

    # H-BE-8: einheitlicher Retry-Loop fuer ConnectError/TimeoutException/5xx.
    last_error: str = ""
    for attempt in range(max_retries + 1):
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            content = data.get("message", {}).get("content", "")
            if content:
                logger.info(
                    "LLM-%s-Antwort erhalten (%d Zeichen, Modell %s)",
                    label, len(content), settings.OLLAMA_MODEL,
                )
                return content

            logger.warning("LLM-%s-Antwort leer", label)
            return None

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_error = type(e).__name__
            if attempt < max_retries:
                wait = _backoff_seconds(attempt)
                logger.warning(
                    "Ollama %s (Versuch %d/%d), warte %.1fs...",
                    last_error, attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("Ollama %s nach %d Versuchen", last_error, max_retries + 1)
                return None

        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < max_retries:
                wait = _backoff_seconds(attempt)
                logger.warning(
                    "Ollama HTTP %d, Retry %d/%d, warte %.1fs...",
                    e.response.status_code, attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
                continue
            logger.error("Ollama HTTP-Fehler: %s", e)
            return None

        except Exception:
            logger.exception("Unerwarteter Fehler bei LLM-Aufruf")
            return None

    return None


async def call_llm(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
    schema: dict | None = None,
) -> str | None:
    """Sendet einen Prompt an Ollama mit JSON-Format-Output.

    Wenn `schema` uebergeben wird, nutzt Ollama JSON-Schema-constrained Generation
    (Ollama >= 0.5). Das Modell ist dann garantiert konform zum Schema; Fallback-
    Parsing-Pfade werden ueberfluessig.
    """
    fmt: str | dict = schema if schema is not None else "json"
    return await _call_ollama(prompt, settings, system_prompt, response_format=fmt, temperature=0.1)


async def call_llm_text(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
) -> str | None:
    """Sendet einen Prompt an Ollama fuer natuerlichsprachige Freitext-Antworten."""
    return await _call_ollama(prompt, settings, system_prompt, response_format=None, temperature=0.3)


async def check_ollama_available(settings: Settings) -> bool:
    """Prueft ob Ollama erreichbar ist.

    Returns:
        True wenn Ollama antwortet, False sonst.
    """
    try:
        # Separater Client mit kurzem Timeout — Health-Probe darf nicht den
        # 300s-Hauptclient blockieren.
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False
