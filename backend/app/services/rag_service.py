"""RAG-Service: Retrieval-Augmented Generation Pipeline."""

import asyncio
import logging
from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.document import Document, DocumentStatus
from app.services.embedding_service import embed_text
from app.services.llm_service import call_llm_text, load_prompt_template
from app.services.vectorize_service import search_similar_chunks

logger = logging.getLogger("zettelwirtschaft.rag")


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

    # 2. Aehnliche Chunks suchen (mehr holen fuer Scope-Filterung)
    fetch_k = settings.RAG_TOP_K * 3 if filing_scope_id else settings.RAG_TOP_K
    chunks = await asyncio.to_thread(
        lambda: search_similar_chunks(query_embedding, settings, top_k=fetch_k)
    )

    if not chunks:
        return {
            "answer": "Keine relevanten Dokumente gefunden. Stelle sicher, dass der Vektor-Index aufgebaut wurde.",
            "sources": [],
            "chunks_found": 0,
        }

    # 3. Scope-Filterung und Dokument-Infos laden
    # Sammle eindeutige doc_ids
    doc_ids = list(OrderedDict.fromkeys(c["doc_id"] for c in chunks))

    # Dokumente laden
    result = await session.execute(
        select(Document).where(
            Document.id.in_(doc_ids),
            Document.status != DocumentStatus.DELETED,
        )
    )
    docs_by_id = {str(d.id): d for d in result.scalars().all()}

    # Scope-Filter anwenden und Chunks filtern
    filtered_chunks = []
    for chunk in chunks:
        doc = docs_by_id.get(chunk["doc_id"])
        if not doc:
            continue
        if filing_scope_id and str(doc.filing_scope_id) != filing_scope_id:
            continue
        filtered_chunks.append(chunk)
        if len(filtered_chunks) >= settings.RAG_TOP_K:
            break

    if not filtered_chunks:
        return {
            "answer": "Keine relevanten Dokumente im ausgewaehlten Ablagebereich gefunden.",
            "sources": [],
            "chunks_found": 0,
        }

    # 4. Kontext aufbauen
    context_parts = []
    source_doc_ids = OrderedDict()
    for i, chunk in enumerate(filtered_chunks):
        doc = docs_by_id.get(chunk["doc_id"])
        doc_label = f"Dok. {len(source_doc_ids) + 1}" if chunk["doc_id"] not in source_doc_ids else f"Dok. {list(source_doc_ids.keys()).index(chunk['doc_id']) + 1}"
        if chunk["doc_id"] not in source_doc_ids:
            source_doc_ids[chunk["doc_id"]] = doc
        context_parts.append(f"[{doc_label}] {chunk['text']}")

    context = "\n\n---\n\n".join(context_parts)

    # 5. Prompt aufbauen und LLM aufrufen
    try:
        prompt_template = load_prompt_template("rag_answer.txt")
    except FileNotFoundError:
        prompt_template = (
            "Beantworte die folgende Frage basierend auf den Dokumentenausschnitten.\n\n"
            "Dokumentenausschnitte:\n{context}\n\nFrage: {question}"
        )

    prompt = prompt_template.replace("{context}", context).replace("{question}", question)
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
