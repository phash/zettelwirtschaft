import asyncio
import logging
from pathlib import Path

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


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


async def _call_ollama(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
    response_format: str | None = None,
    temperature: float = 0.1,
) -> str | None:
    """Gemeinsame Ollama-Aufruflogik mit Retry.

    Args:
        prompt: Der User-Prompt fuer das LLM.
        settings: App-Konfiguration.
        system_prompt: Optionaler System-Prompt.
        response_format: "json" fuer JSON-Output, None fuer Freitext.
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
    label = "JSON" if response_format else "Text"

    for attempt in range(settings.OLLAMA_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.OLLAMA_TIMEOUT)
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()

                data = response.json()
                content = data.get("message", {}).get("content", "")
                if content:
                    logger.info("LLM-%s-Antwort erhalten (%d Zeichen)", label, len(content))
                    return content

                logger.warning("LLM-%s-Antwort leer", label)
                return None

        except httpx.ConnectError:
            if attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning(
                    "Ollama nicht erreichbar (Versuch %d/%d), warte 2s...",
                    attempt + 1, settings.OLLAMA_MAX_RETRIES + 1,
                )
                await asyncio.sleep(2)
            else:
                logger.error("Ollama nicht erreichbar nach %d Versuchen", settings.OLLAMA_MAX_RETRIES + 1)
                return None

        except httpx.TimeoutException:
            if attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning(
                    "Ollama Timeout (Versuch %d/%d), warte 2s...",
                    attempt + 1, settings.OLLAMA_MAX_RETRIES + 1,
                )
                await asyncio.sleep(2)
            else:
                logger.error("Ollama Timeout nach %d Versuchen", settings.OLLAMA_MAX_RETRIES + 1)
                return None

        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning("Ollama HTTP %d, Retry %d/%d...", e.response.status_code, attempt + 1, settings.OLLAMA_MAX_RETRIES)
                await asyncio.sleep(2)
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
) -> str | None:
    """Sendet einen Prompt an Ollama mit JSON-Format-Output."""
    return await _call_ollama(prompt, settings, system_prompt, response_format="json", temperature=0.1)


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
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False
