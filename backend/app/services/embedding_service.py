"""Embedding-Service: Erzeugt Textvektoren via Ollama /api/embed."""

import logging

from app.config import Settings
from app.services.ollama_client import OllamaError, OllamaErrorKind, post_json

logger = logging.getLogger("zettelwirtschaft.embedding")

# Texte pro /api/embed-Request. Ein langes PDF hat hunderte Chunks — in einem
# einzigen Request wuerde ein Timeout/Retry immer das ganze Dokument treffen.
EMBED_BATCH_SIZE = 32


async def embed_batch(texts: list[str], settings: Settings) -> list[list[float]]:
    """Vektoren fuer alle ``texts`` (in Batches), gleiche Reihenfolge und Anzahl.

    Retry-Politik wie beim LLM (shared ``ollama_client.post_json``):
    Verbindungsfehler und 5xx werden wiederholt.

    Raises:
        OllamaError: Ollama nicht erreichbar, Modell fehlt, Serverfehler oder
            Anzahl der Vektoren passt nicht zur Anzahl der Texte.
    """
    if not texts:
        return []

    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start:start + EMBED_BATCH_SIZE]
        payload = {
            "model": settings.EMBEDDING_MODEL,
            "input": batch,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {"num_ctx": settings.OLLAMA_NUM_CTX},
        }
        data = await post_json(
            "/api/embed", payload, settings,
            timeout=settings.OLLAMA_TIMEOUT, purpose="embedding",
        )
        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(batch):
            raise OllamaError(
                OllamaErrorKind.INVALID_RESPONSE,
                f"{len(embeddings)} Vektoren fuer {len(batch)} Texte",
                settings.EMBEDDING_MODEL,
            )
        vectors.extend(embeddings)

    logger.info(
        "Embeddings erzeugt: %d Texte, Dimension %d",
        len(vectors), len(vectors[0]) if vectors else 0,
    )
    return vectors


async def embed_texts(
    texts: list[str], settings: Settings
) -> list[list[float]] | None:
    """Best-Effort-Variante von ``embed_batch``: ``None`` bei Fehler."""
    try:
        return await embed_batch(texts, settings)
    except OllamaError:
        return None


async def embed_text(text: str, settings: Settings) -> list[float] | None:
    """Embedding-Vektor fuer einen einzelnen Text, ``None`` bei Fehler."""
    result = await embed_texts([text], settings)
    return result[0] if result else None
