"""Embedding-Service: Erzeugt Textvektoren via Ollama /api/embed."""

import logging

from app.config import Settings
from app.services.ollama_client import OllamaError, OllamaErrorKind, post_json

logger = logging.getLogger("zettelwirtschaft.embedding")

# Texte pro /api/embed-Request. Ein langes PDF hat hunderte Chunks — in einem
# einzigen Request wuerde ein Timeout/Retry immer das ganze Dokument treffen.
EMBED_BATCH_SIZE = 32


# Modellfamilien (Namensanfang, ohne Tag), deren SUCHANFRAGEN mit einer
# Aufgabenbeschreibung eingebettet werden muessen (asymmetrisches Retrieval).
# Dokument-Chunks bleiben ohne Prefix. qwen3-embedding (alle Groessen):
# Benchmark mit 70 Haushaltsbelegen R@1 100 % mit, 95,8 % ohne Prefix.
# bge-m3, nomic-embed-text u.a.: kein Prefix.
QWEN3_EMBEDDING_QUERY_PREFIX = (
    "Instruct: Given a question about a personal household document, "
    "retrieve the document passages that answer the question\nQuery:"
)
_QUERY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("qwen3-embedding", QWEN3_EMBEDDING_QUERY_PREFIX),
)


def _model_family(model: str) -> str:
    """Modellname ohne Registry-/Namespace-Pfad und ohne Tag, kleingeschrieben.

    "qwen3-embedding:4b" -> "qwen3-embedding",
    "registry.local/library/qwen3-embedding:0.6b" -> "qwen3-embedding".
    """
    last_segment = (model or "").strip().rsplit("/", 1)[-1]
    return last_segment.split(":", 1)[0].lower()


def query_prefix(model: str) -> str:
    """Instruct-Prefix fuer Suchanfragen an ``model`` ("" = keiner).

    Einzige Stelle der Regel; gilt nur fuer Anfragen, nie fuer Dokumente.
    """
    family = _model_family(model)
    for prefix_family, prefix in _QUERY_PREFIXES:
        if family.startswith(prefix_family):
            return prefix
    return ""


def format_query(text: str, model: str) -> str:
    """Suchanfrage so, wie sie ``model`` eingebettet bekommen muss."""
    prefix = query_prefix(model)
    return f"{prefix}{text}" if prefix else text


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


async def embed_query(text: str, settings: Settings) -> list[float]:
    """Vektor fuer eine SUCHANFRAGE (RAG/Chat-Retrieval).

    Setzt bei Modellen, die das verlangen (qwen3-embedding*), den Instruct-
    Prefix vor die Anfrage (``query_prefix``). Dokumente werden weiterhin mit
    ``embed_batch`` ohne Prefix eingebettet.

    Raises:
        OllamaError: wie ``embed_batch``.
    """
    return (await embed_batch([format_query(text, settings.EMBEDDING_MODEL)], settings))[0]
