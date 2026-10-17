"""Vectorize-Service: Dokument-Chunking + ChromaDB-Speicherung."""

import asyncio
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import chromadb
import httpx
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.exc import NoInspectionAvailable

from app.config import Settings
from app.core.normalize import enum_value
from app.models.document import Document, DocumentStatus, DocumentTag, Tag
from app.services.embedding_service import embed_batch
from app.services.ollama_client import OllamaError, probe_ollama
from app.services.task_service import TaskConflictError, TaskContext, TaskError, start_task

logger = logging.getLogger("zettelwirtschaft.vectorize")

# Dokumentfelder, die im Metadaten-Chunk stehen (_build_metadata_chunk) — eine
# Aenderung erfordert Re-Vektorisierung, sonst zitiert der Chat alte Werte.
# Ein reiner Ablagebereichs-Wechsel braucht nur update_scope_metadata.
METADATA_CHUNK_FIELDS = frozenset({
    "title", "document_type", "document_date", "amount", "currency", "issuer",
    "summary", "tax_relevant", "tax_category",
})

# Satzende-Muster fuer saubere Chunk-Grenzen
SENTENCE_END_RE = re.compile(r"[.!?]\s|\n")


class VectorizeError(Exception):
    """Vektorisierung fehlgeschlagen (Chroma/Embedding nicht verfuegbar).

    Bestehende Vektoren des Dokuments bleiben dabei erhalten.
    """


def _resolve_chroma_path(settings: Settings) -> Path:
    """Native-Mode: Pfad fuer PersistentClient ableiten.

    1. Wenn CHROMADB_PATH explizit gesetzt: nutzen.
    2. Sonst: <ARCHIVE_DIR-Parent>/chromadb (also data/chromadb bei Default).
    """
    if settings.CHROMADB_PATH:
        return Path(settings.CHROMADB_PATH)
    # ARCHIVE_DIR ist z.B. "./data/archive" — chromadb daneben legen
    archive = Path(settings.ARCHIVE_DIR)
    return archive.parent / "chromadb"


def _client_key(settings: Settings) -> tuple:
    if settings.CHROMADB_MODE == "embedded":
        return ("embedded", str(_resolve_chroma_path(settings).resolve()))
    return ("http", settings.CHROMADB_HOST, settings.CHROMADB_PORT)


# Client- und Collection-Cache: Ein neuer HttpClient kostet mehrere HTTP-
# Roundtrips (Identity, Tenant, Database) plus get_or_create_collection — vorher
# pro Operation, also mehrfach pro Dokument. Die Chroma-Aufrufe laufen in
# Worker-Threads (to_thread), daher der Lock.
_cache_lock = threading.Lock()
_clients: dict[tuple, object] = {}
_collections: dict[tuple, chromadb.Collection] = {}


def _get_chroma_client(settings: Settings):
    """ChromaDB-Client (gecacht). Embedded (Native) oder HTTP (Docker)."""
    key = _client_key(settings)
    with _cache_lock:
        client = _clients.get(key)
        if client is None:
            if settings.CHROMADB_MODE == "embedded":
                path = _resolve_chroma_path(settings)
                path.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(path))
            else:
                client = chromadb.HttpClient(
                    host=settings.CHROMADB_HOST,
                    port=settings.CHROMADB_PORT,
                )
            _clients[key] = client
        return client


def _collection_name(settings: Settings) -> str:
    """Collection-Name pro Embedding-Modell.

    Verschiedene Embedding-Modelle haben unterschiedliche Dimensionen
    (nomic-embed-text=768, bge-m3=1024). Eine separate Collection pro Modell
    verhindert Dimension-Mismatch-Fehler beim Wechsel und erlaubt den Vergleich
    in der Migrationsphase. ChromaDB-Namen erlauben keine ":" oder "/", also
    normalisieren.
    """
    safe = settings.EMBEDDING_MODEL.replace(":", "_").replace("/", "_")
    return f"documents_{safe}"


def _get_collection_sync(settings: Settings) -> chromadb.Collection:
    """Holt oder erstellt die documents-Collection (synchron, gecacht)."""
    key = (_client_key(settings), _collection_name(settings))
    with _cache_lock:
        collection = _collections.get(key)
    if collection is not None:
        return collection
    collection = _get_chroma_client(settings).get_or_create_collection(
        name=_collection_name(settings),
        metadata={"hnsw:space": "cosine"},
    )
    with _cache_lock:
        _collections[key] = collection
    return collection


def _drop_cached(settings: Settings) -> None:
    """Cache nach einem Chroma-Fehler verwerfen (Server neu gestartet,
    Collection geloescht ...) — der naechste Aufruf baut neu auf."""
    key = _client_key(settings)
    with _cache_lock:
        _clients.pop(key, None)
        for cache_key in [k for k in _collections if k[0] == key]:
            _collections.pop(cache_key, None)


def _with_collection(settings: Settings, operation: Callable[[chromadb.Collection], object]):
    """Fuehrt ``operation(collection)`` aus; bei Fehler wird der Cache verworfen."""
    try:
        return operation(_get_collection_sync(settings))
    except Exception:
        _drop_cached(settings)
        raise


def reset_chroma_cache() -> None:
    """Nur fuer Tests."""
    with _cache_lock:
        _clients.clear()
        _collections.clear()


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 150,
) -> list[str]:
    """Teilt einen Text in Chunks mit Overlap an Satzgrenzen.

    Args:
        text: Der zu teilende Text.
        chunk_size: Maximale Zeichenzahl pro Chunk (> 0).
        overlap: Ueberlappung in Zeichen; wird auf < chunk_size // 2 begrenzt,
            damit jede Runde Fortschritt macht.

    Returns:
        Liste von Text-Chunks.
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    chunk_size = max(1, chunk_size)
    overlap = min(max(0, overlap), max(0, chunk_size // 2 - 1))

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

        # Naechster Start mit Overlap — muss echt vorwaerts gehen, sonst
        # Endlosschleife (Chunk-Ende > chunk_size // 2 > overlap garantiert das).
        next_start = end - overlap
        start = next_start if next_start > start else end

    return chunks


def _build_metadata_chunk(doc: Document, tag_names: list[str] | None = None) -> str:
    """Erstellt einen Metadaten-Chunk fuer quantitative Fragen."""
    parts = []
    if doc.title:
        parts.append(f"Titel: {doc.title}")
    if doc.document_type:
        parts.append(f"Typ: {enum_value(doc.document_type)}")
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
            parts.append(f"Steuerkategorie: {enum_value(doc.tax_category)}")
    if tag_names:
        parts.append(f"Tags: {', '.join(tag_names)}")
    return "\n".join(parts)


async def _load_tag_names(doc: Document, session_factory=None) -> list[str]:
    """Tag-Namen eines Dokuments, ohne Lazy-Load im async-Kontext.

    Ein frisch archiviertes Dokument bekommt seine Tags ueber die Junction-
    Table; ``doc.tags`` ist dann nicht geladen und ein Zugriff wuerde einen
    synchronen Lazy-Load ausloesen (MissingGreenlet). In dem Fall werden die
    Namen in einer eigenen, kurzen Session gelesen.
    """
    try:
        state = sa_inspect(doc)
    except NoInspectionAvailable:  # z.B. Test-Doubles
        return [t.name for t in (getattr(doc, "tags", None) or [])]
    if "tags" not in state.unloaded:
        return [t.name for t in doc.tags]

    if session_factory is None:
        from app.database import async_session_factory as session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(Tag.name)
            .join(DocumentTag, DocumentTag.tag_id == Tag.id)
            .where(DocumentTag.document_id == str(doc.id))
            .order_by(Tag.name)
        )
        return [row[0] for row in result.all()]


# T17: Erreichbarkeitscheck mit TTL-Cache. Bei initial-Vectorize ueber tausende
# Dokumente lief vorher pro Doc ein 2-Sekunden-HTTP-Probe-Roundtrip — TTL
# kappt das auf einmal pro 30s, async statt to_thread-Block.
# R-05 (Re-Review): negative Antworten nur kurz cachen, damit ein gerade
# gestartetes ChromaDB beim Initial-Vectorize nicht 30s lang als "down"
# behandelt wird.
_reachable_cache: tuple[float, bool] | None = None
_REACHABLE_TTL_POSITIVE = 30.0
_REACHABLE_TTL_NEGATIVE = 3.0


async def _check_chromadb_reachable_async(settings: Settings) -> bool:
    """Asynchroner Erreichbarkeitscheck fuer ChromaDB mit TTL-Cache.

    Native (CHROMADB_MODE=embedded): immer True — Chroma laeuft in-process.
    """
    if settings.CHROMADB_MODE == "embedded":
        return True

    global _reachable_cache
    now = time.monotonic()
    if _reachable_cache is not None:
        cached_at, cached_ok = _reachable_cache
        ttl = _REACHABLE_TTL_POSITIVE if cached_ok else _REACHABLE_TTL_NEGATIVE
        if (now - cached_at) < ttl:
            return cached_ok

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"http://{settings.CHROMADB_HOST}:{settings.CHROMADB_PORT}/api/v2/heartbeat"
            )
            ok = resp.status_code == 200
    except Exception:
        ok = False

    _reachable_cache = (now, ok)
    return ok


def _chroma_delete_existing(settings: Settings, doc_id: str) -> None:
    """Loescht alle Chunks eines Dokuments (synchron, fuer to_thread).

    Nutzt direkt collection.delete(where=...) statt vorab alle IDs + Embeddings
    zu laden — bei grossen Dokumenten (10000+ Chunks) spart das mehrere MB
    Memory-Spike pro Re-Index.
    """
    _with_collection(settings, lambda c: c.delete(where={"doc_id": doc_id}))


def _chroma_replace(settings: Settings, doc_id: str, ids, embeddings, documents, metadatas) -> None:
    """Ersetzt die Chunks eines Dokuments (synchron, fuer to_thread).

    Upsert mit deterministischen IDs, danach nur die ueberzaehligen alten
    Chunks (chunk_index >= neue Anzahl) loeschen. So gibt es keinen Moment,
    in dem das Dokument gar keine Vektoren hat.
    """
    def _op(collection):
        collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        collection.delete(where={"$and": [{"doc_id": doc_id}, {"chunk_index": {"$gte": len(ids)}}]})

    _with_collection(settings, _op)


async def vectorize_document(doc: Document, settings: Settings, session_factory=None) -> int:
    """Vektorisiert ein Dokument und speichert es in ChromaDB.

    Erst werden alle Embeddings erzeugt, dann die Chunks ersetzt — faellt
    Ollama aus, bleiben die bisherigen Vektoren erhalten.

    Args:
        doc: Das zu vektorisierende Dokument.
        settings: App-Konfiguration.
        session_factory: Fuer das Nachladen der Tags (Default: App-Datenbank).

    Returns:
        Anzahl gespeicherter Chunks (0 = Dokument ohne Inhalt).

    Raises:
        VectorizeError: ChromaDB oder Embedding nicht verfuegbar.
    """
    doc_id = str(doc.id)

    # T17: Async-Reachable-Check mit TTL-Cache statt to_thread pro Aufruf.
    if not await _check_chromadb_reachable_async(settings):
        raise VectorizeError("ChromaDB nicht erreichbar")

    text_chunks = chunk_text(
        doc.ocr_text or "",
        chunk_size=settings.RAG_CHUNK_SIZE,
        overlap=settings.RAG_CHUNK_OVERLAP,
    )
    meta_chunk = _build_metadata_chunk(doc, await _load_tag_names(doc, session_factory))
    if meta_chunk:
        text_chunks.insert(0, meta_chunk)

    if not text_chunks:
        logger.info("Keine Chunks fuer Dokument %s", doc_id)
        await asyncio.to_thread(_chroma_delete_existing, settings, doc_id)
        return 0

    try:
        embeddings = await embed_batch(text_chunks, settings)
    except OllamaError as e:
        raise VectorizeError(f"Embedding fehlgeschlagen: {e}") from e

    ids = [f"{doc_id}_chunk_{i}" for i in range(len(text_chunks))]
    metadatas = []
    for i in range(len(text_chunks)):
        # Nur Felder, die gelesen werden: doc_id (Zuordnung/Filter),
        # chunk_index (Ersetzen ueberzaehliger Chunks), chunk_type (Metadaten-
        # Chunk erkennbar) und filing_scope_id (serverseitiger Scope-Filter,
        # H-13). Titel/Betrag/Datum stehen im Metadaten-Chunk-Text — als
        # Metadaten wuerden sie bei jeder Bearbeitung veralten.
        meta = {
            "doc_id": doc_id,
            "chunk_type": "metadata" if i == 0 and meta_chunk else "text",
            "chunk_index": i,
        }
        if doc.filing_scope_id:
            meta["filing_scope_id"] = str(doc.filing_scope_id)
        metadatas.append(meta)

    try:
        await asyncio.to_thread(_chroma_replace, settings, doc_id, ids, embeddings, text_chunks, metadatas)
    except Exception as e:
        logger.exception("ChromaDB-Speicherung fehlgeschlagen fuer %s", doc_id)
        raise VectorizeError(f"ChromaDB-Speicherung fehlgeschlagen: {type(e).__name__}") from e

    logger.info("Dokument %s vektorisiert: %d Chunks", doc_id, len(text_chunks))
    return len(text_chunks)


async def revectorize_document_safe(doc_id: str, settings: Settings, session_factory) -> int | None:
    """Nach einer Bearbeitung (PATCH, Tags, Review-Antwort) neu vektorisieren.

    Eigene Session, NACH dem Commit des Aufrufers aufrufen. Geloeschte
    Dokumente werden uebersprungen. Best-Effort: Fehler werden geloggt.

    Returns:
        Anzahl Chunks, oder None bei Fehler / unbekanntem / geloeschtem Dokument.
    """
    try:
        async with session_factory() as session:
            doc = (await session.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
            if doc is None or doc.status == DocumentStatus.DELETED:
                return None
        # Session ist zu, bevor Embedding/Chroma laufen (keine offene
        # Transaktion). Spalten und Tags (lazy="selectin") sind geladen.
        return await vectorize_document(doc, settings, session_factory)
    except VectorizeError as e:
        logger.warning("Re-Vektorisierung fuer %s fehlgeschlagen: %s", doc_id, e)
    except Exception:
        logger.warning("Re-Vektorisierung fuer %s fehlgeschlagen", doc_id, exc_info=True)
    return None


def _chroma_update_scope(settings: Settings, doc_ids: list[str], scope_id: str | None) -> int:
    def _op(collection):
        found = collection.get(where={"doc_id": {"$in": doc_ids}}, include=["metadatas"])
        ids = found.get("ids") or []
        if not ids:
            return 0
        # None entfernt den Schluessel (Chroma-Update-Semantik).
        metadatas = [{**(m or {}), "filing_scope_id": scope_id} for m in found["metadatas"]]
        collection.update(ids=ids, metadatas=metadatas)
        return len(ids)

    return _with_collection(settings, _op)


async def update_scope_metadata(doc_ids: list[str], new_scope_id: str | None, settings: Settings) -> int:
    """Setzt filing_scope_id aller Chunks der Dokumente neu (ohne Re-Embedding).

    Fuer Ablagebereichs-Wechsel und -Loeschung; ``None`` entfernt die Zuordnung.
    Best-Effort: bei Fehler 0 (geloggt).
    """
    if not doc_ids:
        return 0
    if not await _check_chromadb_reachable_async(settings):
        logger.warning("ChromaDB nicht erreichbar — Scope-Metadaten nicht aktualisiert")
        return 0
    try:
        count = await asyncio.to_thread(
            _chroma_update_scope, settings, [str(d) for d in doc_ids], new_scope_id,
        )
    except Exception:
        logger.warning("Scope-Metadaten in ChromaDB nicht aktualisiert", exc_info=True)
        return 0
    logger.info("Scope-Metadaten aktualisiert: %d Chunks, %d Dokumente", count, len(doc_ids))
    return count


async def delete_document_vectors(doc_id: str, settings: Settings) -> None:
    """Loescht alle Vektoren eines Dokuments aus ChromaDB."""
    try:
        await asyncio.to_thread(_chroma_delete_existing, settings, doc_id)
        logger.info("Vektoren geloescht fuer Dokument %s", doc_id)
    except Exception:
        logger.warning("Konnte Vektoren nicht loeschen fuer %s", doc_id, exc_info=True)


def _to_chunks(results) -> list[dict]:
    if not (results and results.get("ids") and results["ids"][0]):
        return []
    chunks = []
    for i, chunk_id in enumerate(results["ids"][0]):
        metadata = results["metadatas"][0][i] or {}
        chunks.append({
            "id": chunk_id,
            "doc_id": metadata.get("doc_id", ""),
            "text": results["documents"][0][i] if results.get("documents") else "",
            "distance": results["distances"][0][i] if results.get("distances") else 0,
            "metadata": metadata,
        })
    return chunks


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
        filing_scope_id: Wenn gesetzt, serverseitig in ChromaDB filtern (H-13).
            Setzt voraus dass Chunks mit `filing_scope_id` in den Metadaten
            indexiert wurden — nach Migration auf bge-m3 gegeben.

    Returns:
        Liste von Dicts mit id, doc_id, text, distance, metadata.
    """
    k = top_k or settings.RAG_TOP_K
    where_filter = {"filing_scope_id": filing_scope_id} if filing_scope_id else None

    def _do_query(collection, where):
        # B7: Im Fallback ohne Filter mehr Kandidaten holen, damit der
        # DB-seitige Nachfilter (rag_service) nicht hungert.
        n = k * 3 if where is None and filing_scope_id else k
        return collection.query(query_embeddings=[query_embedding], n_results=n, where=where)

    try:
        collection = _get_collection_sync(settings)
        chunks = _to_chunks(_do_query(collection, where_filter))
        # B7: Fallback wenn Scope-Filter leer zurueckkommt.
        # Alte Chunks (vor Migration 005) haben kein filing_scope_id-Metadata —
        # ChromaDB filtert sie serverseitig weg. Retry ohne Filter; der Caller
        # macht DB-seitig einen zusaetzlichen Scope-Check ueber das Document-Join.
        if not chunks and filing_scope_id:
            logger.info(
                "ChromaDB-Suche mit Scope-Filter leer — Fallback ohne Filter "
                "(eventuell alte Chunks ohne filing_scope_id-Metadata)"
            )
            chunks = _to_chunks(_do_query(collection, None))
        return chunks
    except Exception:
        _drop_cached(settings)
        logger.exception("ChromaDB-Suche fehlgeschlagen")
        return []


def best_chunks_for_documents(
    query_embedding: list[float],
    settings: Settings,
    doc_ids: list[str],
) -> list[dict]:
    """Der zur Frage aehnlichste Chunk je Dokument (fuer FTS-only-Treffer im RAG).

    Reihenfolge wie ``doc_ids``; Dokumente ohne Vektoren fehlen.
    """
    if not doc_ids:
        return []
    try:
        collection = _get_collection_sync(settings)
        chunks: list[dict] = []
        for doc_id in doc_ids:
            found = _to_chunks(collection.query(
                query_embeddings=[query_embedding], n_results=1, where={"doc_id": doc_id},
            ))
            chunks.extend(found[:1])
        return chunks
    except Exception:
        _drop_cached(settings)
        logger.warning("ChromaDB-Suche fuer FTS-Treffer fehlgeschlagen", exc_info=True)
        return []


def get_collection_count(settings: Settings) -> int | None:
    """Anzahl der Eintraege in der ChromaDB-Collection, ``None`` bei Fehler.

    Synchron (Chroma-Client) — aus async-Code ueber ``asyncio.to_thread`` aufrufen.
    """
    try:
        return _with_collection(settings, lambda c: c.count())
    except Exception:
        logger.warning("ChromaDB-Count fehlgeschlagen", exc_info=True)
        return None


async def vectorize_all_documents(
    session_factory,
    settings: Settings,
    on_progress: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Vektorisiert alle nicht geloeschten Dokumente nacheinander.

    ``on_progress`` (async) bekommt nach jedem Dokument den Zwischenstand.

    Returns:
        {"total", "processed", "chunks", "failed"} — ``failed`` zaehlt
        Dokumente, deren Vektorisierung fehlgeschlagen ist.
    """
    async with session_factory() as session:
        docs = (await session.execute(
            select(Document).where(Document.status != DocumentStatus.DELETED)
        )).scalars().all()

    status = {"total": len(docs), "processed": 0, "chunks": 0, "failed": 0}
    if on_progress:
        await on_progress(dict(status))
    for doc in docs:
        try:
            status["chunks"] += await vectorize_document(doc, settings, session_factory)
        except Exception as e:
            status["failed"] += 1
            logger.warning("Vektorisierung fehlgeschlagen fuer Dokument %s: %s", doc.id, e)
        status["processed"] += 1
        if on_progress:
            await on_progress(dict(status))
    return status


# Vektor-Rebuild als Hintergrund-Vorgang (gemeinsame Task-Infrastruktur wie
# Re-Analyse, E-Mail-Abruf und Backup): manueller Rebuild (Wartung) und die
# initiale Vektorisierung beim Start belegen denselben (kind, key) — es laeuft
# nie mehr als ein Rebuild gleichzeitig.
REBUILD_TASK_KIND = "rebuild_vectors"
REBUILD_TASK_KEY = "global"
REBUILD_CONFLICT_MESSAGE = "Der Vektor-Index wird bereits neu aufgebaut."
NOTHING_EMBEDDED_MESSAGE = (
    "Es wurden keine Abschnitte eingebettet. Ist das Embedding-Modell in Ollama verfügbar?"
)


async def rebuild_vectors_work(ctx: TaskContext, settings: Settings) -> dict:
    """``work``-Funktion des Vorgangs ``rebuild_vectors``.

    Ergebnis wie :func:`vectorize_all_documents`. Wurde bei vorhandenen
    Dokumenten gar nichts eingebettet (Embedding-Modell fehlt, Ollama aus),
    endet der Vorgang als FAILED statt als scheinbarer Erfolg.
    """
    async def progress(status: dict) -> None:
        total, processed = status["total"], status["processed"]
        await ctx.report_progress(
            int(processed * 100 / total) if total else None,
            f"{processed} von {total} Dokumenten verarbeitet",
        )

    result = await vectorize_all_documents(ctx.session_factory, settings, on_progress=progress)
    logger.info(
        "Vektor-Rebuild abgeschlossen: %d Dokumente, %d Chunks, %d fehlgeschlagen",
        result["total"], result["chunks"], result["failed"],
    )
    if result["total"] and result["chunks"] == 0:
        raise TaskError(NOTHING_EMBEDDED_MESSAGE)
    return result


async def start_vector_rebuild(session_factory, settings: Settings):
    """Startet den Vektor-Rebuild. Raises TaskConflictError, wenn schon einer laeuft."""
    async def work(ctx: TaskContext) -> dict:
        return await rebuild_vectors_work(ctx, settings)

    return await start_task(
        session_factory, REBUILD_TASK_KIND, work,
        key=REBUILD_TASK_KEY, conflict_message=REBUILD_CONFLICT_MESSAGE,
    )


# Abstand zwischen zwei Pruefungen, ob das Embedding-Modell bereitsteht.
MODEL_WAIT_POLL_SECONDS = 30.0


async def wait_for_embedding_model(settings: Settings, poll: float = MODEL_WAIT_POLL_SECONDS) -> None:
    """Wartet (ohne Obergrenze), bis Ollama erreichbar und EMBEDDING_MODEL installiert ist.

    Nach einem Modellwechsel laedt ``ensure_configured_models`` das neue Modell
    parallel herunter (Minuten); Native startet Ollama oft erst nach dem
    Backend. Ein Rebuild in dieser Zeit wuerde jedes Dokument fehlschlagen
    lassen — und sobald danach ein neues Dokument vektorisiert ist, ist die
    Collection nicht mehr leer und der Rebuild wuerde nie nachgeholt.
    Abbruch nur ueber Task-Cancel beim Shutdown.
    """
    logged = False
    while True:
        probe = await probe_ollama(settings, required=[settings.EMBEDDING_MODEL])
        if probe.ok:
            return
        if not logged:
            logger.info(
                "Initiale Vektorisierung wartet auf Embedding-Modell '%s': %s",
                settings.EMBEDDING_MODEL, probe.message(),
            )
            logged = True
        await asyncio.sleep(poll)


async def run_initial_vectorize(
    session_factory,
    settings: Settings,
    *,
    delay: float = 5.0,
    count_attempts: int = 3,
    model_poll: float = MODEL_WAIT_POLL_SECONDS,
):
    """Startup-Hintergrundtask: Index aufbauen, wenn die Collection leer ist.

    - Wartet ``delay`` Sekunden (ChromaDB-Container startet ggf. parallel) und
      fragt den Count bis zu ``count_attempts``-mal ab (to_thread, nicht im
      Event-Loop). Ist Chroma nicht erreichbar, wird NICHT vektorisiert —
      sonst wuerde jeder Neustart mit kaputtem Chroma alle Dokumente sinnlos
      durch das Embedding-Modell schicken.
    - Ist die Collection leer (neue Installation oder neues Embedding-Modell
      -> neue Collection), wird zuerst auf das Embedding-Modell gewartet
      (``wait_for_embedding_model``: Auto-Pull laeuft parallel). Danach wird
      der Count NICHT erneut geprueft — in der Wartezeit archivierte Dokumente
      fuellen die Collection, die Altbestaende fehlen aber trotzdem.
    - Startet den Rebuild als Hintergrund-Vorgang ``rebuild_vectors`` (in der
      Wartung sichtbar, gleicher Schutz vor parallelem manuellem Rebuild).

    Returns:
        Den gestarteten Vorgang (BackgroundTask) oder None, wenn nichts zu tun war.
    """
    if delay:
        await asyncio.sleep(delay)

    count = None
    for attempt in range(count_attempts):
        count = await asyncio.to_thread(get_collection_count, settings)
        if count is not None:
            break
        if attempt < count_attempts - 1:
            await asyncio.sleep(delay or 1.0)
    if count is None:
        logger.warning("ChromaDB nicht erreichbar — initiale Vektorisierung uebersprungen")
        return None
    if count > 0:
        logger.info("ChromaDB enthaelt %d Eintraege, ueberspringe initiale Vektorisierung", count)
        return None
    await wait_for_embedding_model(settings, poll=model_poll)
    try:
        task = await start_vector_rebuild(session_factory, settings)
    except TaskConflictError:
        logger.info("Vektor-Rebuild laeuft bereits — initiale Vektorisierung uebersprungen")
        return None
    logger.info("Initiale Vektorisierung gestartet (Vorgang %s)", task.id)
    return task
