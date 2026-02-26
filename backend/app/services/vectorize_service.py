"""Vectorize-Service: Dokument-Chunking + ChromaDB-Speicherung."""

import asyncio
import logging
import re

import chromadb

from app.config import Settings
from app.models.document import Document
from app.services.embedding_service import embed_texts

logger = logging.getLogger("zettelwirtschaft.vectorize")

# Satzende-Muster fuer saubere Chunk-Grenzen
SENTENCE_END_RE = re.compile(r"[.!?]\s|\n")


def _get_chroma_client(settings: Settings) -> chromadb.HttpClient:
    """Erstellt einen ChromaDB HTTP-Client."""
    return chromadb.HttpClient(
        host=settings.CHROMADB_HOST,
        port=settings.CHROMADB_PORT,
    )


def _get_collection_sync(settings: Settings) -> chromadb.Collection:
    """Holt oder erstellt die documents-Collection (synchron)."""
    client = _get_chroma_client(settings)
    return client.get_or_create_collection(
        name="documents",
        metadata={"hnsw:space": "cosine"},
    )


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 150,
) -> list[str]:
    """Teilt einen Text in Chunks mit Overlap an Satzgrenzen.

    Args:
        text: Der zu teilende Text.
        chunk_size: Maximale Zeichenzahl pro Chunk.
        overlap: Ueberlappung in Zeichen.

    Returns:
        Liste von Text-Chunks.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:].strip())
            break

        # Satzgrenze suchen (rueckwaerts vom Ende des Chunks)
        search_region = text[start:end]
        best_break = None
        for match in SENTENCE_END_RE.finditer(search_region):
            best_break = match.end()

        if best_break and best_break > chunk_size // 2:
            end = start + best_break
        # Sonst an chunk_size abschneiden

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Naechster Start mit Overlap
        start = end - overlap
        if start <= (end - chunk_size):
            start = end  # Vermeide Endlosschleife

    return chunks


def _build_metadata_chunk(doc: Document) -> str:
    """Erstellt einen Metadaten-Chunk fuer quantitative Fragen."""
    parts = []
    if doc.title:
        parts.append(f"Titel: {doc.title}")
    if doc.document_type:
        parts.append(f"Typ: {doc.document_type.value if hasattr(doc.document_type, 'value') else doc.document_type}")
    if doc.issuer:
        parts.append(f"Aussteller: {doc.issuer}")
    if doc.amount is not None:
        currency = doc.currency or "EUR"
        parts.append(f"Betrag: {doc.amount} {currency}")
    if doc.document_date:
        parts.append(f"Datum: {doc.document_date.isoformat()}")
    if doc.summary:
        parts.append(f"Zusammenfassung: {doc.summary}")
    if doc.tax_relevant:
        parts.append("Steuerrelevant: Ja")
        if doc.tax_category:
            parts.append(f"Steuerkategorie: {doc.tax_category}")
    try:
        if doc.tags:
            tag_names = [t.name for t in doc.tags]
            parts.append(f"Tags: {', '.join(tag_names)}")
    except Exception:
        pass  # Tags nicht verfuegbar (z.B. MissingGreenlet in async-Kontext)
    return "\n".join(parts)


def _check_chromadb_reachable(settings: Settings) -> bool:
    """Schneller Erreichbarkeitscheck fuer ChromaDB (synchron)."""
    import httpx as _httpx
    try:
        resp = _httpx.get(
            f"http://{settings.CHROMADB_HOST}:{settings.CHROMADB_PORT}/api/v2/heartbeat",
            timeout=2.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _chroma_delete_existing(settings: Settings, doc_id: str) -> None:
    """Loescht bestehende Chunks eines Dokuments (synchron, fuer to_thread)."""
    collection = _get_collection_sync(settings)
    existing = collection.get(where={"doc_id": doc_id})
    if existing and existing["ids"]:
        collection.delete(ids=existing["ids"])


def _chroma_add(settings: Settings, ids, embeddings, documents, metadatas) -> None:
    """Fuegt Chunks zu ChromaDB hinzu (synchron, fuer to_thread)."""
    collection = _get_collection_sync(settings)
    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )


async def vectorize_document(doc: Document, settings: Settings) -> int:
    """Vektorisiert ein Dokument und speichert es in ChromaDB.

    Args:
        doc: Das zu vektorisierende Dokument.
        settings: App-Konfiguration.

    Returns:
        Anzahl gespeicherter Chunks.
    """
    doc_id = str(doc.id)

    # Schneller Erreichbarkeitscheck (vermeidet teure Fehler im Thread)
    reachable = await asyncio.to_thread(_check_chromadb_reachable, settings)
    if not reachable:
        logger.debug("ChromaDB nicht erreichbar, ueberspringe Vektorisierung fuer %s", doc_id)
        return 0

    # Bestehende Chunks fuer dieses Dokument loeschen (Re-Indexierung)
    try:
        await asyncio.to_thread(_chroma_delete_existing, settings, doc_id)
    except Exception:
        logger.warning("Konnte alte Chunks nicht loeschen fuer %s", doc_id, exc_info=True)

    # Text-Chunks erzeugen
    text_chunks = chunk_text(
        doc.ocr_text or "",
        chunk_size=settings.RAG_CHUNK_SIZE,
        overlap=settings.RAG_CHUNK_OVERLAP,
    )

    # Metadaten-Chunk hinzufuegen
    meta_chunk = _build_metadata_chunk(doc)
    if meta_chunk:
        text_chunks.insert(0, meta_chunk)

    if not text_chunks:
        logger.info("Keine Chunks fuer Dokument %s", doc_id)
        return 0

    # Embeddings erzeugen
    embeddings = await embed_texts(text_chunks, settings)
    if not embeddings:
        logger.warning("Embedding fehlgeschlagen fuer Dokument %s", doc_id)
        return 0

    # In ChromaDB speichern
    ids = [f"{doc_id}_chunk_{i}" for i in range(len(text_chunks))]
    metadatas = []
    for i, chunk in enumerate(text_chunks):
        meta = {
            "doc_id": doc_id,
            "chunk_type": "metadata" if i == 0 and meta_chunk else "text",
            "chunk_index": i,
        }
        if doc.title:
            meta["title"] = doc.title
        if doc.document_type:
            meta["document_type"] = doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type)
        if doc.issuer:
            meta["issuer"] = doc.issuer
        if doc.amount is not None:
            meta["amount"] = float(doc.amount)
        if doc.document_date:
            meta["document_date"] = doc.document_date.isoformat()
        metadatas.append(meta)

    try:
        await asyncio.to_thread(_chroma_add, settings, ids, embeddings, text_chunks, metadatas)
        logger.info(
            "Dokument %s vektorisiert: %d Chunks",
            doc_id,
            len(text_chunks),
        )
        return len(text_chunks)
    except Exception:
        logger.exception("ChromaDB-Speicherung fehlgeschlagen fuer %s", doc_id)
        return 0


async def delete_document_vectors(doc_id: str, settings: Settings) -> None:
    """Loescht alle Vektoren eines Dokuments aus ChromaDB."""
    try:
        await asyncio.to_thread(_chroma_delete_existing, settings, doc_id)
        logger.info("Vektoren geloescht fuer Dokument %s", doc_id)
    except Exception:
        logger.warning("Konnte Vektoren nicht loeschen fuer %s", doc_id, exc_info=True)


def search_similar_chunks(
    query_embedding: list[float],
    settings: Settings,
    top_k: int | None = None,
    filing_scope_id: str | None = None,
) -> list[dict]:
    """Sucht aehnliche Chunks in ChromaDB.

    Args:
        query_embedding: Embedding-Vektor der Suchanfrage.
        settings: App-Konfiguration.
        top_k: Anzahl Ergebnisse (Default: settings.RAG_TOP_K).
        filing_scope_id: Optionaler Scope-Filter (wird derzeit nicht in ChromaDB-Metadaten gespeichert,
                         Filterung erfolgt im RAG-Service auf Dokumentebene).

    Returns:
        Liste von Dicts mit id, doc_id, text, distance, metadata.
    """
    k = top_k or settings.RAG_TOP_K

    try:
        collection = _get_collection_sync(settings)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
        )
    except Exception:
        logger.exception("ChromaDB-Suche fehlgeschlagen")
        return []

    if not results or not results["ids"] or not results["ids"][0]:
        return []

    chunks = []
    for i, chunk_id in enumerate(results["ids"][0]):
        chunk = {
            "id": chunk_id,
            "doc_id": results["metadatas"][0][i].get("doc_id", ""),
            "text": results["documents"][0][i] if results["documents"] else "",
            "distance": results["distances"][0][i] if results["distances"] else 0,
            "metadata": results["metadatas"][0][i],
        }
        chunks.append(chunk)

    return chunks


def get_collection_count(settings: Settings) -> int:
    """Gibt die Anzahl der Eintraege in der ChromaDB-Collection zurueck."""
    try:
        collection = _get_collection_sync(settings)
        return collection.count()
    except Exception:
        return 0
