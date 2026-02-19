import asyncio
import logging
from pathlib import Path

import httpx

from app.config import Settings

logger = logging.getLogger("zettelwirtschaft.llm")

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


async def call_llm(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
) -> str | None:
    """Sendet einen Prompt an Ollama und gibt die Antwort zurueck.

    Nutzt die /api/chat Schnittstelle mit format: "json" fuer strukturierte Ausgabe.
    Bei Verbindungsfehlern wird automatisch wiederholt (OLLAMA_MAX_RETRIES).

    Args:
        prompt: Der User-Prompt fuer das LLM.
        settings: App-Konfiguration.
        system_prompt: Optionaler System-Prompt.

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
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1,
        },
    }

    url = f"{settings.OLLAMA_BASE_URL}/api/chat"

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
                    logger.info("LLM-Antwort erhalten (%d Zeichen)", len(content))
                    return content

                logger.warning("LLM-Antwort leer")
                return None

        except httpx.ConnectError:
            if attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning(
                    "Ollama nicht erreichbar (Versuch %d/%d), warte 2s...",
                    attempt + 1,
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                await asyncio.sleep(2)
            else:
                logger.error(
                    "Ollama nicht erreichbar nach %d Versuchen",
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                return None

        except httpx.TimeoutException:
            if attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning(
                    "Ollama Timeout (Versuch %d/%d), warte 2s...",
                    attempt + 1,
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                await asyncio.sleep(2)
            else:
                logger.error(
                    "Ollama Timeout nach %d Versuchen",
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                return None

        except httpx.HTTPStatusError as e:
            logger.error("Ollama HTTP-Fehler: %s", e)
            return None

        except Exception:
            logger.exception("Unerwarteter Fehler bei LLM-Aufruf")
            return None

    return None


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
