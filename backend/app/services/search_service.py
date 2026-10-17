import logging
import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_types import UTCDateTime

logger = logging.getLogger(__name__)


# Sentinels fuer Treffer-Markierungen im highlight-Feld. Bewusst kein HTML:
# das Frontend splittet an STX/ETX und rendert <mark>-Knoten selbst (kein
# v-html, damit OCR-Text nie als Markup interpretiert wird).
HIGHLIGHT_START = "\x02"
HIGHLIGHT_END = "\x03"

# Entspricht der Tokenisierung von unicode61: Buchstaben und Ziffern bilden
# Tokens, alles andere (Unterstrich, Bindestrich, Punkt, Komma, ...) trennt.
_TOKEN_RE = re.compile(r"[^\W_]+")
_PHRASE_RE = re.compile(r'"([^"]*)"')

# Dokumentfelder im FTS-Index (neben ocr_text und Tags) — eine Aenderung
# erfordert reindex_document_fts.
FTS_DOCUMENT_FIELDS = frozenset({"title", "issuer", "summary"})

# Liefert pro nicht geloeschtem Dokument die Spalten des FTS-Index.
# Gemeinsame Quelle fuer Komplett-Rebuild und Einzel-Reindex, damit Tags
# ueberall identisch (Tag-Namen aus der DB, leerzeichengetrennt) landen.
_FTS_DOC_SELECT = """
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
"""

_FTS_INSERT = "INSERT INTO documents_fts(doc_id, title, ocr_text, issuer, summary, tags)"


class InvalidSearchQueryError(ValueError):
    """Die Suchanfrage konnte von FTS5 nicht ausgewertet werden."""


# Fehlermeldungen, mit denen SQLite einen ungueltigen FTS5-MATCH-Ausdruck
# ablehnt. Andere OperationalErrors (z.B. "database is locked") sind keine
# Benutzerfehler und werden unveraendert weitergereicht.
_FTS_QUERY_ERROR_MARKERS = ("fts5", "unterminated string", "no such column", "unknown special query")


def _is_fts_query_error(exc: OperationalError) -> bool:
    message = str(exc.orig).lower()
    return any(marker in message for marker in _FTS_QUERY_ERROR_MARKERS)


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


async def rebuild_fts_index(session: AsyncSession) -> int:
    """Baut den FTS-Index komplett neu auf aus der documents-Tabelle."""
    await ensure_fts_table(session)
    await session.execute(text("DELETE FROM documents_fts"))
    result = await session.execute(text(f"{_FTS_INSERT} {_FTS_DOC_SELECT}"))
    count = result.rowcount
    await session.commit()
    logger.info("FTS-Index neu aufgebaut (%d Dokumente)", count)
    return count


async def remove_document_from_index(session: AsyncSession, doc_id: str) -> None:
    """Entfernt ein Dokument aus dem FTS-Index. Kein Commit.

    doc_id ist UNINDEXED, das DELETE scannt daher die komplette FTS-Tabelle
    (O(n) pro Aufruf). Fuer Haushalts-Archive (einige tausend Dokumente)
    unkritisch; ein stabiler Integer-rowid waere die Optimierung, falls das
    je relevant wird.
    """
    await session.execute(
        text("DELETE FROM documents_fts WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )


async def reindex_document_fts(session: AsyncSession, doc_id: str) -> None:
    """Baut den FTS-Eintrag eines Dokuments aus dem aktuellen DB-Stand neu auf.

    Einziger Einstiegspunkt fuer Einzel-Reindex (Archivierung, Bearbeitung,
    Tag-Aenderung, Re-Analyse, Loeschen): Titel, OCR-Text, Aussteller,
    Zusammenfassung und Tags kommen aus der DB — dieselbe Logik wie
    rebuild_fts_index. Geloeschte Dokumente werden nur entfernt.
    Ausstehende ORM-Aenderungen werden vorher geflusht. Kein Commit.
    """
    await session.flush()
    await remove_document_from_index(session, doc_id)
    await session.execute(
        text(f"{_FTS_INSERT} {_FTS_DOC_SELECT} AND d.id = :doc_id"),
        {"doc_id": doc_id},
    )


def _fts_term(tokens: list[str], prefix: bool) -> str:
    """Formt Tokens zu einem gequoteten FTS5-Term (Phrase bei mehreren Tokens).

    Tokens bestehen nur aus Buchstaben/Ziffern, enthalten also nie
    Anfuehrungszeichen — Escaping ist nicht noetig.
    """
    term = '"' + " ".join(tokens) + '"'
    return term + "*" if prefix else term


def fts_tokens(text: str) -> list[str]:
    """Zerlegt Text wie die unicode61-Tokenisierung der FTS5-Tabelle
    (Buchstaben/Ziffern) — gemeinsam fuer Suche und RAG-Retrieval."""
    return _TOKEN_RE.findall(text)


def fts_prefix_term(tokens: list[str]) -> str:
    """Gequotete Prefix-Phrase fuer FTS5-MATCH (``["RE", "2024"]`` -> ``"RE 2024"*``)."""
    return _fts_term(tokens, prefix=True)


def _words_to_terms(fragment: str) -> list[str | None]:
    """Zerlegt Text ausserhalb von Phrasen in Prefix-Terme; None markiert OR."""
    result: list[str | None] = []
    for word in fragment.replace('"', " ").split():
        if word == "OR":
            result.append(None)
        elif word == "AND":
            continue
        else:
            tokens = _TOKEN_RE.findall(word)
            if tokens:
                result.append(_fts_term(tokens, prefix=True))
    return result


def build_fts_query(query: str | None) -> str:
    """Uebersetzt eine Benutzer-Suchanfrage in einen sicheren FTS5-MATCH-Ausdruck.

    - Ausgeglichene "..."-Phrasen werden als exakte Phrase gesucht,
      ueberzaehlige Anfuehrungszeichen verworfen.
    - Jedes uebrige Wort wird wie von unicode61 in Tokens zerlegt und als
      gequotete Prefix-Phrase gesucht: ``RE-2024-0815`` -> ``"RE 2024 0815"*``,
      ``01.02.2024`` -> ``"01 02 2024"*``, ``119,00`` -> ``"119 00"*``.
    - Durch das Quoting erreicht keine FTS5-Syntax den MATCH: ``title:``,
      ``NOT``, ``NEAR``, ``-`` oder ``*`` sind normaler Text bzw. Trenner.
    - Einziger Operator: grossgeschriebenes ``OR`` verknuepft benachbarte
      Begriffe (``Telekom Strom OR Gas`` = Telekom UND (Strom ODER Gas)).
      Grossgeschriebenes ``AND`` ist der Standard und wird ignoriert.
    - Liefert ``""``, wenn kein suchbarer Begriff uebrig bleibt.
    """
    if not query or not query.strip():
        return ""

    # Begriffe und OR-Marker (None) in Eingabereihenfolge
    items: list[str | None] = []
    pos = 0
    for match in _PHRASE_RE.finditer(query):
        items.extend(_words_to_terms(query[pos:match.start()]))
        tokens = _TOKEN_RE.findall(match.group(1))
        if tokens:
            items.append(_fts_term(tokens, prefix=False))
        pos = match.end()
    items.extend(_words_to_terms(query[pos:]))

    # Durch OR verbundene Begriffe gruppieren, Gruppen per AND verknuepfen.
    # Explizites AND ist noetig: FTS5 erlaubt kein implizites AND nach ")".
    groups: list[list[str]] = []
    pending_or = False
    for item in items:
        if item is None:
            pending_or = bool(groups)
        elif pending_or:
            groups[-1].append(item)
            pending_or = False
        else:
            groups.append([item])

    clauses = [g[0] if len(g) == 1 else "(" + " OR ".join(g) + ")" for g in groups]
    return " AND ".join(clauses)


def _like_pattern(value: str) -> str:
    """Teilstring-Muster fuer LIKE ... ESCAPE '\\' (Benutzer-%/_ sind literal)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Filter-Dimensionen, deren Facette disjunktiv berechnet wird: der eigene
# Filter bleibt dabei unberuecksichtigt, damit weitere Werte waehlbar bleiben.
_DIM_TYPE = "document_type"
_DIM_SCOPE = "filing_scope"

_SORT_COLUMNS = {"date": "d.document_date", "amount": "d.amount", "title": "d.title"}


def _from_where(use_fts: bool, conditions: list[str], join: str = "") -> str:
    sql = "FROM documents d"
    if use_fts:
        sql += " JOIN documents_fts fts ON d.id = fts.doc_id"
    sql += join
    where = (["fts.documents_fts MATCH :fts_query"] if use_fts else []) + conditions
    return f"{sql} WHERE {' AND '.join(where)}"


def _order_clause(sort_by: str, sort_order: str, use_fts: bool) -> str:
    if sort_by == "relevance" and use_fts:
        return "fts.rank"
    direction = "ASC" if sort_order == "asc" else "DESC"
    return f"{_SORT_COLUMNS.get(sort_by, 'd.created_at')} {direction}"


def _empty_facets() -> dict:
    return {"document_types": {}, "years": {}, "top_issuers": {}, "filing_scopes": []}


async def search_documents(
    session: AsyncSession,
    query: str | None = None,
    document_types: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
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
    """Fuehrt eine kombinierte Volltextsuche + Metadatenfilter durch.

    Raises:
        InvalidSearchQueryError: FTS5 lehnt den MATCH-Ausdruck ab
            (Defense in depth — build_fts_query erzeugt nur gueltige Syntax).
    """
    use_fts = bool(query and query.strip())
    fts_query = build_fts_query(query) if use_fts else ""
    if use_fts and not fts_query:
        # Nur Satzzeichen/Operatoren eingegeben: kein Treffer statt "alles".
        return {
            "results": [], "total": 0, "page": page, "page_size": page_size,
            "facets": _empty_facets(),
        }

    # (Dimension, SQL) — Dimension nur fuer disjunktive Facetten gesetzt
    conditions: list[tuple[str | None, str]] = [(None, "d.status != 'DELETED'")]
    params: dict = {}
    if use_fts:
        params["fts_query"] = fts_query

    if document_types:
        placeholders = [f":dtype_{i}" for i in range(len(document_types))]
        conditions.append((_DIM_TYPE, f"d.document_type IN ({', '.join(placeholders)})"))
        for i, t in enumerate(document_types):
            params[f"dtype_{i}"] = t
    if date_from:
        conditions.append((None, "d.document_date >= :date_from"))
        params["date_from"] = date_from.isoformat()
    if date_to:
        conditions.append((None, "d.document_date <= :date_to"))
        params["date_to"] = date_to.isoformat()
    if amount_min is not None:
        conditions.append((None, "d.amount >= :amount_min"))
        params["amount_min"] = amount_min
    if amount_max is not None:
        conditions.append((None, "d.amount <= :amount_max"))
        params["amount_max"] = amount_max
    if issuer:
        conditions.append((None, "d.issuer LIKE :issuer_filter ESCAPE '\\'"))
        params["issuer_filter"] = _like_pattern(issuer)
    if tax_relevant is not None:
        conditions.append((None, "d.tax_relevant = :tax_relevant"))
        params["tax_relevant"] = tax_relevant
    if tax_year is not None:
        conditions.append((None, "d.tax_year = :tax_year"))
        params["tax_year"] = tax_year
    if tax_category:
        conditions.append((None, "d.tax_category = :tax_category"))
        params["tax_category"] = tax_category
    if status:
        conditions.append((None, "d.status = :status_filter"))
        params["status_filter"] = status
    if filing_scope_id:
        conditions.append((_DIM_SCOPE, "d.filing_scope_id = :filing_scope_id"))
        params["filing_scope_id"] = filing_scope_id
    if tags:
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        for i, tag_name in enumerate(tag_list):
            conditions.append((None, f"""
                EXISTS (
                    SELECT 1 FROM document_tags dt
                    JOIN tags t ON dt.tag_id = t.id
                    WHERE dt.document_id = d.id AND t.name = :tag_{i}
                )
            """))
            params[f"tag_{i}"] = tag_name

    base_query = _from_where(use_fts, [sql for _, sql in conditions])
    if use_fts:
        # Spalte -1: snippet waehlt die Spalte mit dem besten Treffer
        # (Titel/Aussteller statt immer OCR-Text).
        highlight_sql = "snippet(documents_fts, -1, char(2), char(3), '…', 32)"
        score_sql = "-fts.rank"
    else:
        highlight_sql = "''"
        score_sql = "0.0"

    results_sql = f"""
        SELECT d.id, d.title, d.document_type, d.document_date,
               d.amount, d.currency, d.issuer,
               d.tax_relevant, d.ai_confidence, d.created_at,
               {highlight_sql} AS highlight,
               {score_sql} AS relevance_score
        {base_query}
        ORDER BY {_order_clause(sort_by, sort_order, use_fts)}
        LIMIT :limit OFFSET :offset
    """
    page_params = {**params, "limit": page_size, "offset": (page - 1) * page_size}

    try:
        total = (await session.execute(text(f"SELECT COUNT(*) {base_query}"), params)).scalar() or 0
        # Rohes SQL umgeht die Spaltentypen: created_at explizit als UTC typisieren
        # (sonst naiver String ohne Offset, anders als alle anderen API-Zeitstempel).
        rows = (await session.execute(
            text(results_sql).columns(created_at=UTCDateTime()), page_params,
        )).fetchall()
        facets = await _compute_facets(session, use_fts, conditions, params)
    except OperationalError as exc:
        if use_fts and _is_fts_query_error(exc):
            logger.warning("Ungueltiger FTS5-Ausdruck %r (Eingabe %r): %s", fts_query, query, exc.orig)
            raise InvalidSearchQueryError("Ungültige Suchanfrage") from exc
        raise

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
            "amount": row[4],
            "currency": row[5] or "EUR",
            "issuer": row[6],
            "tags": tags_map.get(doc_id, []),
            "tax_relevant": bool(row[7]),
            "ai_confidence": float(row[8]) if row[8] is not None else 0.0,
            "created_at": row[9],
            "highlight": row[10] or "",
            "relevance_score": float(row[11]) if row[11] is not None else 0.0,
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
    use_fts: bool,
    conditions: list[tuple[str | None, str]],
    params: dict,
) -> dict:
    """Berechnet Facetten fuer die Suchergebnisse.

    Dokumenttyp- und Ablagebereich-Facette sind disjunktiv: sie ignorieren den
    eigenen Filter (aber alle anderen), damit nach Auswahl eines Typs die
    uebrigen Typen samt Zaehler sichtbar bleiben.
    """
    def where(exclude: str | None = None, extra: list[str] | None = None, join: str = "") -> str:
        conds = [sql for dim, sql in conditions if exclude is None or dim != exclude]
        return _from_where(use_fts, conds + (extra or []), join)

    facets = _empty_facets()

    type_result = await session.execute(text(f"""
        SELECT d.document_type, COUNT(*) AS cnt
        {where(exclude=_DIM_TYPE)}
        GROUP BY d.document_type
        ORDER BY cnt DESC
    """), params)
    facets["document_types"] = {row[0]: row[1] for row in type_result.fetchall() if row[0]}

    year_result = await session.execute(text(f"""
        SELECT CAST(strftime('%Y', d.document_date) AS TEXT) AS year, COUNT(*) AS cnt
        {where(extra=["d.document_date IS NOT NULL"])}
        GROUP BY year
        ORDER BY year DESC
    """), params)
    facets["years"] = {row[0]: row[1] for row in year_result.fetchall() if row[0]}

    issuer_result = await session.execute(text(f"""
        SELECT d.issuer, COUNT(*) AS cnt
        {where(extra=["d.issuer IS NOT NULL", "d.issuer != ''"])}
        GROUP BY d.issuer
        ORDER BY cnt DESC
        LIMIT 10
    """), params)
    facets["top_issuers"] = {row[0]: row[1] for row in issuer_result.fetchall() if row[0]}

    # Nach id gruppiert und mit id geliefert — der Filter erwartet die id,
    # Namen sind nur Anzeige.
    scope_result = await session.execute(text(f"""
        SELECT fs.id, fs.name, COUNT(*) AS cnt
        {where(exclude=_DIM_SCOPE, join=" JOIN filing_scopes fs ON d.filing_scope_id = fs.id")}
        GROUP BY fs.id, fs.name
        ORDER BY cnt DESC, fs.name
    """), params)
    facets["filing_scopes"] = [
        {"id": row[0], "name": row[1], "count": row[2]} for row in scope_result.fetchall()
    ]

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
    """Liefert Autocomplete-Vorschlaege basierend auf Ausstellern, Tags und Titeln."""
    if not query or len(query) < 2:
        return []

    suggestions: list[str] = []
    pattern = _like_pattern(query)

    issuer_result = await session.execute(
        text("""
            SELECT DISTINCT issuer FROM documents
            WHERE issuer LIKE :pattern ESCAPE '\\' AND status != 'DELETED'
            ORDER BY issuer
            LIMIT :limit
        """),
        {"pattern": pattern, "limit": limit},
    )
    for row in issuer_result.fetchall():
        if row[0] and row[0] not in suggestions:
            suggestions.append(row[0])

    if len(suggestions) < limit:
        # Nur Tags, die an mindestens einem nicht geloeschten Dokument haengen —
        # verwaiste Tags wuerden zu Suchen ohne Treffer fuehren.
        tag_result = await session.execute(
            text("""
                SELECT DISTINCT t.name FROM tags t
                JOIN document_tags dt ON dt.tag_id = t.id
                JOIN documents d ON d.id = dt.document_id
                WHERE t.name LIKE :pattern ESCAPE '\\' AND d.status != 'DELETED'
                ORDER BY t.name
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
                WHERE title LIKE :pattern ESCAPE '\\' AND status != 'DELETED'
                ORDER BY title
                LIMIT :limit
            """),
            {"pattern": pattern, "limit": limit - len(suggestions)},
        )
        for row in title_result.fetchall():
            if row[0] and row[0] not in suggestions:
                suggestions.append(row[0])

    return suggestions[:limit]
