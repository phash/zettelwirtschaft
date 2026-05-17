"""RAG-Service: Retrieval-Augmented Generation Pipeline."""

import asyncio
import logging
from collections import OrderedDict

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.document import Document, DocumentStatus
from app.services.embedding_service import embed_text
from app.services.llm_service import call_llm, call_llm_text, load_prompt_template
from app.services.search_service import _sanitize_fts_query
from app.services.vectorize_service import search_similar_chunks

logger = logging.getLogger("zettelwirtschaft.rag")

# Reciprocal-Rank-Fusion-Konstante. Standardwert aus dem Cormack et al. Paper.
# Niedrigere Werte gewichten Top-Treffer staerker, hoehere Werte glaetten.
RRF_K = 60


def _sanitize_for_prompt(text: str) -> str:
    """B6: Entfernt die Delimiter-Tags des Prompt-Templates aus User-Daten.

    Das RAG-Template wrappt OCR-Chunks zwischen <document_excerpts>...</document_excerpts>
    und die User-Frage zwischen <user_question>...</user_question>. Wuerde ein
    Dokument oder eine User-Eingabe diese Tags selbst enthalten, koennte sie aus
    dem Wrap ausbrechen und neue Instruktionen einschmuggeln. Verhindern wir,
    indem wir die Tags entschaerfen (Spitze Klammern ersetzen).
    """
    if not text:
        return ""
    return (
        text.replace("<document_excerpts>", "&lt;document_excerpts&gt;")
        .replace("</document_excerpts>", "&lt;/document_excerpts&gt;")
        .replace("<user_question>", "&lt;user_question&gt;")
        .replace("</user_question>", "&lt;/user_question&gt;")
    )


async def _fts_top_doc_ids(
    session: AsyncSession,
    query: str,
    limit: int = 15,
    filing_scope_id: str | None = None,
) -> list[str]:
    """FTS5-Suche fuer den Hybrid-Retrieval-Pfad.

    FTS5 ist exzellent fuer Eigennamen, Rechnungsnummern und exakte Begriffe —
    Vector-Search ist exzellent fuer semantische Naehe. Beide werden via RRF
    kombiniert.
    """
    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []
    sql = """
        SELECT documents_fts.doc_id
        FROM documents_fts
        JOIN documents d ON d.id = documents_fts.doc_id
        WHERE documents_fts MATCH :q
          AND d.status != 'DELETED'
    """
    params: dict = {"q": sanitized}
    if filing_scope_id:
        sql += " AND d.filing_scope_id = :scope"
        params["scope"] = filing_scope_id
    sql += " ORDER BY rank LIMIT :lim"
    params["lim"] = limit
    try:
        result = await session.execute(sql_text(sql), params)
        return [str(row[0]) for row in result.all()]
    except Exception:
        logger.warning("FTS5-Hybrid-Pfad fehlgeschlagen", exc_info=True)
        return []


_RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {"type": "number", "minimum": 0, "maximum": 10},
        },
    },
    "required": ["scores"],
}


async def _llm_rerank(
    chunks: list[dict],
    question: str,
    settings: Settings,
    target_k: int,
) -> list[dict]:
    """LLM-basiertes Re-Ranking als optionale Stufe nach Hybrid-Retrieval.

    Sendet alle Kandidaten in EINEM Prompt und laesst das Modell jeden Chunk
    auf einer 0-10-Skala fuer Relevanz zur Frage bewerten. Schema-Mode
    erzwingt strukturierten Output. Kombiniert die LLM-Scores additiv mit dem
    bestehenden RRF-Score und sortiert neu.

    Wird nur aufgerufen wenn `RAG_USE_RERANKER=true` und mehr Kandidaten als
    target_k vorhanden sind.
    """
    if len(chunks) <= target_k:
        return chunks
    snippet_list = "\n".join(
        f"[{i}] {c['text'][:400]}" for i, c in enumerate(chunks)
    )
    prompt = (
        "Bewerte jeden der folgenden Dokumentenausschnitte auf einer Skala von "
        "0 (gar nicht relevant) bis 10 (sehr relevant) fuer die Frage. Antworte "
        "ausschliesslich mit JSON: {\"scores\": [score_0, score_1, ...]} — die "
        "Liste muss exakt so viele Eintraege haben wie es Ausschnitte gibt.\n\n"
        f"Frage: {question}\n\nAusschnitte:\n{snippet_list}"
    )
    try:
        raw = await call_llm(prompt, settings, schema=_RERANK_SCHEMA)
        if not raw:
            return chunks[:target_k]
        import json as _json
        scores = _json.loads(raw).get("scores", [])
        # NEW-007 / H-05: bei Mismatch trotzdem auf target_k stutzen, sonst
        # bekommt das Antwort-LLM 15 statt 5 Chunks (Lost-in-the-Middle).
        if not scores or len(scores) != len(chunks):
            logger.warning(
                "Reranker-Mismatch: %d Chunks, %d Scores — Fallback ohne LLM-Boost",
                len(chunks), len(scores) if scores else 0,
            )
            return chunks[:target_k]
        # H-06: Score-Validation — clamp auf [0, 10], NaN/None -> 0
        for c, s in zip(chunks, scores):
            try:
                f = float(s)
                if f != f or f < 0:  # NaN-Check + negativ
                    f = 0.0
                elif f > 10:
                    f = 10.0
                c["_rrf_score"] = (c.get("_rrf_score") or 0) + (f / 10.0)
            except (TypeError, ValueError):
                pass
        ranked = sorted(chunks, key=lambda c: c.get("_rrf_score", 0), reverse=True)
        return ranked[:target_k]
    except Exception:
        logger.warning("LLM-Reranker fehlgeschlagen, nutze Fallback-RRF", exc_info=True)
        return chunks[:target_k]


def _apply_rrf(
    chunks: list[dict],
    fts_doc_ids: list[str],
    target_k: int,
) -> list[dict]:
    """Reciprocal Rank Fusion zwischen Vector-Chunks und FTS5-Doc-Ranking.

    Score(chunk) = 1/(k + vec_rank) + 1/(k + fts_rank_of_its_doc)
    Boost laesst Chunks aus Dokumenten, die FTS5 hoch gewichtet, ueber
    semantisch nahen aber thematisch fernen Chunks emporsteigen.
    """
    if not fts_doc_ids:
        return chunks[:target_k]
    fts_rank = {doc_id: idx for idx, doc_id in enumerate(fts_doc_ids)}
    for vec_rank, chunk in enumerate(chunks):
        score = 1 / (RRF_K + vec_rank)
        fr = fts_rank.get(chunk["doc_id"])
        if fr is not None:
            score += 1 / (RRF_K + fr)
        chunk["_rrf_score"] = score
    return sorted(chunks, key=lambda c: c["_rrf_score"], reverse=True)[:target_k]


async def ask_question(
    question: str,
    settings: Settings,
    session: AsyncSession,
    filing_scope_id: str | None = None,
) -> dict:
    """Beantwortet eine Frage mittels RAG-Pipeline.

    1. Frage embedden
    2. Aehnliche Chunks suchen
    3. Kontext + Frage an LLM
    4. Antwort + Quellen zurueckgeben

    Args:
        question: Die Benutzerfrage.
        settings: App-Konfiguration.
        session: Datenbank-Session.
        filing_scope_id: Optionaler Ablagebereich-Filter.

    Returns:
        Dict mit answer, sources, chunks_found.
    """
    # 1. Frage embedden
    query_embedding = await embed_text(question, settings)
    if not query_embedding:
        return {
            "answer": "Der Embedding-Service ist derzeit nicht verfuegbar. Bitte versuche es spaeter erneut.",
            "sources": [],
            "chunks_found": 0,
        }

    # 2. Hybrid Retrieval: Vector + FTS5 mit Reciprocal Rank Fusion.
    # Vector-Side oversamplt (top_k * 3), damit RRF aus mehr Kandidaten waehlen
    # kann; FTS5-Side liefert Doc-Ids als Boost-Signal.
    fetch_k = settings.RAG_TOP_K * 3
    vector_task = asyncio.to_thread(
        lambda: search_similar_chunks(
            query_embedding, settings,
            top_k=fetch_k,
            filing_scope_id=filing_scope_id,
        )
    )
    fts_task = _fts_top_doc_ids(session, question, limit=15, filing_scope_id=filing_scope_id)
    vector_chunks, fts_doc_ids = await asyncio.gather(vector_task, fts_task)

    # Reranker-Aware: wenn LLM-Reranker aktiv ist, hier mehr Kandidaten halten
    # damit der Reranker eine echte Auswahl hat. Sonst direkt auf top_k stutzen.
    rrf_target = settings.RAG_TOP_K * 2 if settings.RAG_USE_RERANKER else settings.RAG_TOP_K
    if vector_chunks and fts_doc_ids:
        chunks = _apply_rrf(vector_chunks, fts_doc_ids, target_k=rrf_target)
        logger.info(
            "Hybrid-Retrieval: %d vec + %d fts -> %d nach RRF",
            len(vector_chunks), len(fts_doc_ids), len(chunks),
        )
    else:
        chunks = (vector_chunks or [])[:rrf_target]

    # Optionales LLM-basiertes Re-Ranking (RAG_USE_RERANKER=true)
    if settings.RAG_USE_RERANKER and len(chunks) > settings.RAG_TOP_K:
        before = len(chunks)
        chunks = await _llm_rerank(chunks, question, settings, target_k=settings.RAG_TOP_K)
        logger.info("LLM-Reranker: %d -> %d Chunks", before, len(chunks))

    if not chunks:
        msg = (
            "Keine relevanten Dokumente im ausgewaehlten Ablagebereich gefunden."
            if filing_scope_id
            else "Keine relevanten Dokumente gefunden. Stelle sicher, dass der Vektor-Index aufgebaut wurde."
        )
        return {
            "answer": msg,
            "sources": [],
            "chunks_found": 0,
        }

    # 3. Dokument-Infos laden — DELETED-Filter haengt nur an der DB,
    # daher hier zusaetzlich pruefen und Chunks zu geloeschten Docs verwerfen
    # (deckt Geister-Chunks ab, falls delete_document_vectors gefehlschlagen ist).
    # B7: Bei aktivem Scope-Filter auch DB-seitig filtern. Notwendig fuer den
    # ChromaDB-Fallback-Pfad ohne Metadata-Filter (alte Chunks vor Migration 005).
    doc_ids = list(OrderedDict.fromkeys(c["doc_id"] for c in chunks))
    query = select(Document).where(
        Document.id.in_(doc_ids),
        Document.status != DocumentStatus.DELETED,
    )
    if filing_scope_id:
        query = query.where(Document.filing_scope_id == filing_scope_id)
    result = await session.execute(query)
    docs_by_id = {str(d.id): d for d in result.scalars().all()}

    filtered_chunks = [c for c in chunks if c["doc_id"] in docs_by_id]
    if not filtered_chunks:
        return {
            "answer": "Keine relevanten Dokumente gefunden.",
            "sources": [],
            "chunks_found": 0,
        }

    # 4. Kontext aufbauen
    # B6: Chunk-Inhalte werden sanitiert, damit OCR-Text mit Tag-Injection
    # (</document_excerpts>...<user_question>...</user_question>...) nicht aus
    # dem Template-Wrap ausbrechen und Anweisungen an das LLM einschmuggeln kann.
    context_parts = []
    source_doc_ids = OrderedDict()
    for i, chunk in enumerate(filtered_chunks):
        doc = docs_by_id.get(chunk["doc_id"])
        doc_label = f"Dok. {len(source_doc_ids) + 1}" if chunk["doc_id"] not in source_doc_ids else f"Dok. {list(source_doc_ids.keys()).index(chunk['doc_id']) + 1}"
        if chunk["doc_id"] not in source_doc_ids:
            source_doc_ids[chunk["doc_id"]] = doc
        safe_text = _sanitize_for_prompt(chunk["text"])
        context_parts.append(f"[{doc_label}] {safe_text}")

    context = "\n\n---\n\n".join(context_parts)
    # User-Frage sanitieren (gleicher Mechanismus)
    safe_question = _sanitize_for_prompt(question)

    # 5. Prompt aufbauen und LLM aufrufen
    try:
        prompt_template = load_prompt_template("rag_answer.txt")
    except FileNotFoundError:
        prompt_template = (
            "Beantworte die folgende Frage basierend auf den Dokumentenausschnitten.\n\n"
            "Dokumentenausschnitte:\n{context}\n\nFrage: {question}"
        )

    prompt = prompt_template.replace("{context}", context).replace("{question}", safe_question)
    answer = await call_llm_text(prompt, settings)

    if not answer:
        return {
            "answer": "Der KI-Assistent konnte keine Antwort generieren. Bitte versuche es spaeter erneut.",
            "sources": [],
            "chunks_found": len(filtered_chunks),
        }

    # 6. Quellen aufbereiten
    sources = []
    for doc_id, doc in source_doc_ids.items():
        if doc:
            sources.append({
                "document_id": doc_id,
                "title": doc.title,
                "document_type": doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type),
                "document_date": doc.document_date.isoformat() if doc.document_date else None,
                "issuer": doc.issuer,
            })

    return {
        "answer": answer,
        "sources": sources,
        "chunks_found": len(filtered_chunks),
    }
