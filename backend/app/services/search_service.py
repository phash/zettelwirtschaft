import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("zettelwirtschaft.search")


async def ensure_fts_table(session: AsyncSession) -> None:
    """Erstellt die FTS5-Tabelle falls nicht vorhanden."""
    await session.execute(text("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            doc_id UNINDEXED,
            title,
            ocr_text,
            issuer,
            summary,
            tags,
            tokenize='unicode61 remove_diacritics 2'
        )
    """))
    await session.commit()


async def rebuild_fts_index(session: AsyncSession) -> None:
    """Baut den FTS-Index komplett neu auf aus der documents-Tabelle."""
    await ensure_fts_table(session)
    # Clear existing index
    await session.execute(text("DELETE FROM documents_fts"))
    # Rebuild from documents table
    await session.execute(text("""
        INSERT INTO documents_fts(doc_id, title, ocr_text, issuer, summary, tags)
        SELECT
            d.id,
            COALESCE(d.title, ''),
            COALESCE(d.ocr_text, ''),
            COALESCE(d.issuer, ''),
            COALESCE(d.summary, ''),
            COALESCE(
                (SELECT GROUP_CONCAT(t.name, ' ')
                 FROM document_tags dt JOIN tags t ON dt.tag_id = t.id
                 WHERE dt.document_id = d.id),
                ''
            )
        FROM documents d
        WHERE d.status != 'DELETED'
    """))
    await session.commit()
    logger.info("FTS-Index neu aufgebaut")


async def index_document(
    session: AsyncSession,
    doc_id: str,
    title: str,
    ocr_text: str,
    issuer: str | None,
    summary: str | None,
    tags: str,
) -> None:
    """Fuegt ein einzelnes Dokument zum FTS-Index hinzu oder aktualisiert es."""
    # Remove old entry if exists
    await session.execute(
        text("DELETE FROM documents_fts WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )
    # Insert new entry
    await session.execute(
        text("""
            INSERT INTO documents_fts(doc_id, title, ocr_text, issuer, summary, tags)
            VALUES (:doc_id, :title, :ocr_text, :issuer, :summary, :tags)
        """),
        {
            "doc_id": doc_id,
            "title": title or "",
            "ocr_text": ocr_text or "",
            "issuer": issuer or "",
            "summary": summary or "",
            "tags": tags or "",
        },
    )


def _sanitize_fts_query(query: str) -> str:
    """Bereinigt eine Suchanfrage fuer FTS5.

    - Behaelt Anfuehrungszeichen fuer Phrase-Suche bei
    - Fuegt * fuer Prefix-Suche an Woerter an (sofern nicht bereits vorhanden)
    - Entfernt gefaehrliche Zeichen
    """
    if not query or not query.strip():
        return ""

    query = query.strip()

    # If query contains quotes, treat as phrase search
    if '"' in query:
        return re.sub(r'[^\w\s"*äöüÄÖÜß-]', "", query)

    # Split into words, add prefix search
    words = query.split()
    sanitized = []
    for word in words:
        word = re.sub(r"[^\w*äöüÄÖÜß-]", "", word)
        if word:
            if not word.endswith("*"):
                word += "*"
            sanitized.append(word)

    return " ".join(sanitized)


async def search_documents(
    session: AsyncSession,
    query: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    issuer: str | None = None,
    tax_relevant: bool | None = None,
    tax_year: int | None = None,
    tax_category: str | None = None,
    tags: str | None = None,
    status: str | None = None,
    filing_scope_id: str | None = None,
    sort_by: str = "relevance",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    """Fuehrt eine kombinierte Volltextsuche + Metadatenfilter durch."""
    use_fts = bool(query and query.strip())
    fts_query = _sanitize_fts_query(query) if use_fts else ""

    # Build WHERE clauses
    conditions = ["d.status != 'DELETED'"]
    params: dict = {}

    if document_type:
        types = [t.strip() for t in document_type.split(",")]
        placeholders = [f":dtype_{i}" for i in range(len(types))]
        conditions.append(f"d.document_type IN ({', '.join(placeholders)})")
        for i, t in enumerate(types):
            params[f"dtype_{i}"] = t

    if date_from:
        conditions.append("d.document_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("d.document_date <= :date_to")
        params["date_to"] = date_to
    if amount_min is not None:
        conditions.append("d.amount >= :amount_min")
        params["amount_min"] = amount_min
    if amount_max is not None:
        conditions.append("d.amount <= :amount_max")
        params["amount_max"] = amount_max
    if issuer:
        conditions.append("d.issuer LIKE :issuer_filter")
        params["issuer_filter"] = f"%{issuer}%"
    if tax_relevant is not None:
        conditions.append("d.tax_relevant = :tax_relevant")
        params["tax_relevant"] = tax_relevant
    if tax_year is not None:
        conditions.append("d.tax_year = :tax_year")
        params["tax_year"] = tax_year
    if tax_category:
        conditions.append("d.tax_category = :tax_category")
        params["tax_category"] = tax_category
    if status:
        conditions.append("d.status = :status_filter")
        params["status_filter"] = status
    if filing_scope_id:
        conditions.append("d.filing_scope_id = :filing_scope_id")
        params["filing_scope_id"] = filing_scope_id
    if tags:
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        for i, tag_name in enumerate(tag_list):
            conditions.append(f"""
                EXISTS (
                    SELECT 1 FROM document_tags dt
                    JOIN tags t ON dt.tag_id = t.id
                    WHERE dt.document_id = d.id AND t.name = :tag_{i}
                )
            """)
            params[f"tag_{i}"] = tag_name

    where_clause = " AND ".join(conditions)

    if use_fts and fts_query:
        params["fts_query"] = fts_query

        base_query = f"""
            FROM documents d
            JOIN documents_fts fts ON d.id = fts.doc_id
            WHERE fts.documents_fts MATCH :fts_query
            AND {where_clause}
        """

        # Count
        count_sql = f"SELECT COUNT(*) {base_query}"
        count_result = await session.execute(text(count_sql), params)
        total = count_result.scalar() or 0

        # Sort
        if sort_by == "relevance":
            order = "fts.rank"
        elif sort_by == "date":
            order = f"d.document_date {'ASC' if sort_order == 'asc' else 'DESC'}"
        elif sort_by == "amount":
            order = f"d.amount {'ASC' if sort_order == 'asc' else 'DESC'}"
        elif sort_by == "title":
            order = f"d.title {'ASC' if sort_order == 'asc' else 'DESC'}"
        else:
            order = f"d.created_at {'ASC' if sort_order == 'asc' else 'DESC'}"

        offset = (page - 1) * page_size
        params["limit"] = page_size
        params["offset"] = offset

        results_sql = f"""
            SELECT d.id, d.title, d.document_type, d.document_date,
                   d.amount, d.currency, d.issuer, d.thumbnail_path,
                   d.tax_relevant, d.ai_confidence, d.created_at,
                   snippet(documents_fts, 2, '<mark>', '</mark>', '...', 32) as highlight,
                   -fts.rank as relevance_score
            {base_query}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
        """

        results = await session.execute(text(results_sql), params)
        rows = results.fetchall()

        facets = await _compute_facets(session, base_query, params)

    else:
        base_query = f"FROM documents d WHERE {where_clause}"

        count_sql = f"SELECT COUNT(*) {base_query}"
        count_result = await session.execute(text(count_sql), params)
        total = count_result.scalar() or 0

        if sort_by == "date":
            order = f"d.document_date {'ASC' if sort_order == 'asc' else 'DESC'}"
        elif sort_by == "amount":
            order = f"d.amount {'ASC' if sort_order == 'asc' else 'DESC'}"
        elif sort_by == "title":
            order = f"d.title {'ASC' if sort_order == 'asc' else 'DESC'}"
        else:
            order = f"d.created_at {'ASC' if sort_order == 'asc' else 'DESC'}"

        offset = (page - 1) * page_size
        params["limit"] = page_size
        params["offset"] = offset

        results_sql = f"""
            SELECT d.id, d.title, d.document_type, d.document_date,
                   d.amount, d.currency, d.issuer, d.thumbnail_path,
                   d.tax_relevant, d.ai_confidence, d.created_at,
                   '' as highlight,
                   0.0 as relevance_score
            {base_query}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
        """

        results = await session.execute(text(results_sql), params)
        rows = results.fetchall()

        facets = await _compute_facets(session, base_query, params)

    # Fetch tags for results
    doc_ids = [row[0] for row in rows]
    tags_map = await _fetch_tags_for_docs(session, doc_ids)

    items = []
    for row in rows:
        doc_id = row[0]
        items.append({
            "id": doc_id,
            "title": row[1] or "",
            "document_type": row[2] or "SONSTIGES",
            "document_date": row[3],
            "amount": float(row[4]) if row[4] is not None else None,
            "currency": row[5] or "EUR",
            "issuer": row[6],
            "thumbnail_path": row[7],
            "tags": tags_map.get(doc_id, []),
            "tax_relevant": bool(row[8]),
            "ai_confidence": float(row[9]) if row[9] is not None else 0.0,
            "created_at": row[10],
            "highlight": row[11] or "",
            "relevance_score": float(row[12]) if row[12] is not None else 0.0,
        })

    return {
        "results": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "facets": facets,
    }


async def _compute_facets(
    session: AsyncSession,
    base_query: str,
    params: dict,
) -> dict:
    """Berechnet Facetten fuer die Suchergebnisse."""
    facets = {"document_types": {}, "years": {}, "top_issuers": {}, "filing_scopes": {}}

    type_sql = f"""
        SELECT d.document_type, COUNT(*) as cnt
        {base_query}
        GROUP BY d.document_type
        ORDER BY cnt DESC
    """
    type_result = await session.execute(text(type_sql), params)
    facets["document_types"] = {
        row[0]: row[1] for row in type_result.fetchall() if row[0]
    }

    year_sql = f"""
        SELECT CAST(strftime('%Y', d.document_date) AS TEXT) as year, COUNT(*) as cnt
        {base_query} AND d.document_date IS NOT NULL
        GROUP BY year
        ORDER BY year DESC
    """
    year_result = await session.execute(text(year_sql), params)
    facets["years"] = {
        row[0]: row[1] for row in year_result.fetchall() if row[0]
    }

    issuer_sql = f"""
        SELECT d.issuer, COUNT(*) as cnt
        {base_query} AND d.issuer IS NOT NULL AND d.issuer != ''
        GROUP BY d.issuer
        ORDER BY cnt DESC
        LIMIT 10
    """
    issuer_result = await session.execute(text(issuer_sql), params)
    facets["top_issuers"] = {
        row[0]: row[1] for row in issuer_result.fetchall() if row[0]
    }

    scope_sql = f"""
        SELECT fs.name, COUNT(*) as cnt
        {base_query} AND d.filing_scope_id IS NOT NULL
        {'AND' if 'JOIN' not in base_query.upper() or 'filing_scopes' not in base_query else 'AND'}
        1=1
        GROUP BY d.filing_scope_id
    """
    # Simpler approach: join filing_scopes
    scope_sql = f"""
        SELECT fs.name, COUNT(*) as cnt
        FROM documents d
        LEFT JOIN filing_scopes fs ON d.filing_scope_id = fs.id
        WHERE d.id IN (SELECT d.id {base_query})
        AND fs.name IS NOT NULL
        GROUP BY fs.name
        ORDER BY cnt DESC
    """
    try:
        scope_result = await session.execute(text(scope_sql), params)
        facets["filing_scopes"] = {
            row[0]: row[1] for row in scope_result.fetchall() if row[0]
        }
    except Exception:
        pass  # Graceful degradation if filing_scopes table doesn't exist yet

    return facets


async def _fetch_tags_for_docs(
    session: AsyncSession,
    doc_ids: list[str],
) -> dict[str, list[str]]:
    """Holt die Tags fuer eine Liste von Dokumenten."""
    if not doc_ids:
        return {}

    placeholders = ", ".join(f":id_{i}" for i in range(len(doc_ids)))
    params = {f"id_{i}": doc_id for i, doc_id in enumerate(doc_ids)}

    result = await session.execute(
        text(f"""
            SELECT dt.document_id, t.name
            FROM document_tags dt
            JOIN tags t ON dt.tag_id = t.id
            WHERE dt.document_id IN ({placeholders})
        """),
        params,
    )

    tags_map: dict[str, list[str]] = {}
    for row in result.fetchall():
        doc_id = row[0]
        if doc_id not in tags_map:
            tags_map[doc_id] = []
        tags_map[doc_id].append(row[1])

    return tags_map


async def suggest(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[str]:
    """Liefert Autocomplete-Vorschlaege basierend auf Ausstellern und Tags."""
    if not query or len(query) < 2:
        return []

    suggestions: list[str] = []
    pattern = f"%{query}%"

    issuer_result = await session.execute(
        text("""
            SELECT DISTINCT issuer FROM documents
            WHERE issuer LIKE :pattern AND status != 'DELETED'
            ORDER BY issuer
            LIMIT :limit
        """),
        {"pattern": pattern, "limit": limit},
    )
    for row in issuer_result.fetchall():
        if row[0] and row[0] not in suggestions:
            suggestions.append(row[0])

    if len(suggestions) < limit:
        tag_result = await session.execute(
            text("""
                SELECT name FROM tags
                WHERE name LIKE :pattern
                ORDER BY name
                LIMIT :limit
            """),
            {"pattern": pattern, "limit": limit - len(suggestions)},
        )
        for row in tag_result.fetchall():
            if row[0] and row[0] not in suggestions:
                suggestions.append(row[0])

    if len(suggestions) < limit:
        title_result = await session.execute(
            text("""
                SELECT DISTINCT title FROM documents
                WHERE title LIKE :pattern AND status != 'DELETED'
                ORDER BY title
                LIMIT :limit
            """),
            {"pattern": pattern, "limit": limit - len(suggestions)},
        )
        for row in title_result.fetchall():
            if row[0] and row[0] not in suggestions:
                suggestions.append(row[0])

    return suggestions[:limit]
