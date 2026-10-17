"""Gemeinsame Logik fuer Ablagebereiche (API + Review-Antwort "NEU: ...")."""

import json
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.filing_scope import FilingScope, generate_slug

logger = logging.getLogger(__name__)

SCOPE_NAME_EXISTS_MESSAGE = "Ein Ablagebereich mit diesem Namen existiert bereits."


async def find_scope_by_name(session: AsyncSession, name: str) -> FilingScope | None:
    """Sucht einen Ablagebereich per Namen, Gross-/Kleinschreibung egal.

    Vergleich in Python statt per SQL ``lower()`` — SQLite senkt nur ASCII,
    'Ärzte' und 'ärzte' waeren sonst verschieden. Die Tabelle ist klein.
    """
    wanted = name.strip().casefold()
    result = await session.execute(select(FilingScope))
    for scope in result.scalars().all():
        if scope.name.strip().casefold() == wanted:
            return scope
    return None


async def scopes_for_analysis(session: AsyncSession) -> list[dict]:
    """Ablagebereiche als Plain-Dicts fuer analyze_document/_match_filing_scope."""
    result = await session.execute(select(FilingScope))
    return [
        {
            "id": s.id,
            "name": s.name,
            "slug": s.slug,
            "keywords": s.parsed_keywords,
            "is_default": s.is_default,
        }
        for s in result.scalars().all()
    ]


async def unique_slug(session: AsyncSession, name: str) -> str:
    """Slug fuer einen neuen Bereich; bei Kollision mit Zaehler-Suffix.

    'Privat!' und 'Privat' ergeben beide 'privat' — statt eines technischen
    "Slug existiert"-Fehlers bekommt der zweite Bereich 'privat-2'.
    """
    base = generate_slug(name)
    result = await session.execute(select(FilingScope.slug))
    taken = {row[0] for row in result.all()}
    slug = base
    counter = 2
    while slug in taken:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


async def create_scope(
    session: AsyncSession,
    name: str,
    *,
    description: str | None = None,
    keywords: list[str] | None = None,
    is_default: bool = False,
    color: str | None = None,
) -> FilingScope:
    """Legt einen Ablagebereich an (Name-Duplikate prueft der Aufrufer)."""
    if is_default:
        await session.execute(update(FilingScope).values(is_default=False))
    scope = FilingScope(
        name=name,
        slug=await unique_slug(session, name),
        description=description,
        keywords=json.dumps(keywords or [], ensure_ascii=False),
        is_default=is_default,
        color=color,
    )
    session.add(scope)
    await session.flush()
    logger.info("Ablagebereich angelegt: '%s' (Slug: %s)", scope.name, scope.slug)
    return scope
