"""Embedding-Service: Erzeugt Textvektoren via Ollama /api/embed."""

import asyncio
import logging

import httpx

from app.config import Settings

logger = logging.getLogger("zettelwirtschaft.embedding")


async def embed_text(text: str, settings: Settings) -> list[float] | None:
    """Erzeugt einen Embedding-Vektor fuer einen einzelnen Text.

    Args:
        text: Der zu vektorisierende Text.
        settings: App-Konfiguration.

    Returns:
        Liste von Floats (Embedding-Vektor), oder None bei Fehler.
    """
    result = await embed_texts([text], settings)
    if result and len(result) > 0:
        return result[0]
    return None


async def embed_texts(
    texts: list[str], settings: Settings
) -> list[list[float]] | None:
    """Erzeugt Embedding-Vektoren fuer mehrere Texte (Batch).

    Args:
        texts: Liste der zu vektorisierenden Texte.
        settings: App-Konfiguration.

    Returns:
        Liste von Embedding-Vektoren, oder None bei Fehler.
    """
    if not texts:
        return []

    url = f"{settings.OLLAMA_BASE_URL}/api/embed"
    payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": texts,
    }

    for attempt in range(settings.OLLAMA_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.OLLAMA_TIMEOUT)
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()

                data = response.json()
                embeddings = data.get("embeddings", [])
                if embeddings:
                    logger.info(
                        "Embeddings erzeugt: %d Texte, Dimension %d",
                        len(embeddings),
                        len(embeddings[0]),
                    )
                    return embeddings

                logger.warning("Embedding-Antwort leer")
                return None

        except httpx.ConnectError:
            if attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning(
                    "Ollama nicht erreichbar fuer Embedding (Versuch %d/%d), warte 2s...",
                    attempt + 1,
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                await asyncio.sleep(2)
            else:
                logger.error(
                    "Ollama nicht erreichbar nach %d Versuchen (Embedding)",
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                return None

        except httpx.TimeoutException:
            if attempt < settings.OLLAMA_MAX_RETRIES:
                logger.warning(
                    "Ollama Timeout bei Embedding (Versuch %d/%d), warte 2s...",
                    attempt + 1,
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                await asyncio.sleep(2)
            else:
                logger.error(
                    "Ollama Timeout nach %d Versuchen (Embedding)",
                    settings.OLLAMA_MAX_RETRIES + 1,
                )
                return None

        except httpx.HTTPStatusError as e:
            logger.error("Ollama HTTP-Fehler bei Embedding: %s", e)
            return None

        except Exception:
            logger.exception("Unerwarteter Fehler bei Embedding")
            return None

    return None


async def ensure_embedding_model(settings: Settings) -> bool:
    """Stellt sicher, dass das Embedding-Modell in Ollama vorhanden ist.

    Fuehrt 'ollama pull' aus, falls noetig.

    Returns:
        True wenn Modell verfuegbar, False sonst.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code != 200:
                return False

            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]

            # Check ob Modell schon vorhanden (mit und ohne :latest Tag)
            target = settings.EMBEDDING_MODEL
            if any(n == target or n == f"{target}:latest" or n.startswith(f"{target}:") for n in model_names):
                logger.info("Embedding-Modell '%s' bereits vorhanden", target)
                return True

            # Modell pullen
            logger.info("Pulling Embedding-Modell '%s'...", target)
            pull_resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/pull",
                json={"name": target, "stream": False},
                timeout=httpx.Timeout(600.0),  # Modell-Download kann dauern
            )
            if pull_resp.status_code == 200:
                logger.info("Embedding-Modell '%s' erfolgreich gepullt", target)
                return True
            else:
                logger.error(
                    "Pull von '%s' fehlgeschlagen: HTTP %d",
                    target,
                    pull_resp.status_code,
                )
                return False

    except Exception:
        logger.exception("Fehler beim Sicherstellen des Embedding-Modells")
        return False
