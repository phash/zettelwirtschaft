"""Ablagebereiche (Filing Scopes) API."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.document import Document
from app.models.filing_scope import FilingScope
from app.schemas.filing_scope import FilingScopeCreate, FilingScopeResponse, FilingScopeUpdate
from app.services import vectorize_service
from app.services.file_relocation import (
    FileMove,
    RelocationError,
    copy_files,
    discard_copies,
    is_strict_subdir,
    known_scope_slugs,
    plan_move,
    prune_empty_dirs,
    remove_sources,
)
from app.services.filing_scope_service import (
    SCOPE_NAME_EXISTS_MESSAGE,
    create_scope,
    find_scope_by_name,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["filing-scopes"])


def _scope_to_response(scope: FilingScope) -> dict:
    """Konvertiert FilingScope zu Response-Dict mit geparsten Keywords."""
    return {
        "id": scope.id,
        "name": scope.name,
        "slug": scope.slug,
        "description": scope.description,
        "keywords": scope.parsed_keywords,
        "is_default": scope.is_default,
        "color": scope.color,
        "created_at": scope.created_at,
    }


@router.get("/filing-scopes", response_model=list[FilingScopeResponse])
async def list_filing_scopes(session: AsyncSession = Depends(get_db, scope="function")):
    """Listet alle Ablagebereiche auf."""
    result = await session.execute(
        select(FilingScope).order_by(FilingScope.is_default.desc(), FilingScope.name)
    )
    scopes = result.scalars().all()
    return [_scope_to_response(s) for s in scopes]


@router.post("/filing-scopes", response_model=FilingScopeResponse, status_code=201)
async def create_filing_scope(
    data: FilingScopeCreate,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Erstellt einen neuen Ablagebereich."""
    if await find_scope_by_name(session, data.name):
        raise HTTPException(409, SCOPE_NAME_EXISTS_MESSAGE)

    scope = await create_scope(
        session,
        data.name,
        description=data.description,
        keywords=data.keywords,
        is_default=data.is_default,
        color=data.color,
    )
    return _scope_to_response(scope)


@router.patch("/filing-scopes/{scope_id}", response_model=FilingScopeResponse)
async def update_filing_scope(
    scope_id: str,
    data: FilingScopeUpdate,
    session: AsyncSession = Depends(get_db, scope="function"),
):
    """Aktualisiert einen Ablagebereich."""
    result = await session.execute(
        select(FilingScope).where(FilingScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        raise HTTPException(404, "Ablagebereich nicht gefunden")

    if data.name is not None and data.name != scope.name:
        existing = await find_scope_by_name(session, data.name)
        if existing and existing.id != scope.id:
            raise HTTPException(409, SCOPE_NAME_EXISTS_MESSAGE)
        scope.name = data.name
        # Slug bleibt stabil - Aenderung wuerde Archiv-Dateipfade brechen

    if data.description is not None:
        scope.description = data.description

    if data.keywords is not None:
        scope.keywords = json.dumps(data.keywords, ensure_ascii=False)

    if data.color is not None:
        scope.color = data.color

    if data.is_default is True:
        await session.execute(
            update(FilingScope).values(is_default=False)
        )
        scope.is_default = True
    elif data.is_default is False:
        # Verhindern dass kein Default uebrig bleibt
        count_result = await session.execute(
            select(func.count()).select_from(FilingScope).where(
                FilingScope.is_default.is_(True),
                FilingScope.id != scope_id,
            )
        )
        other_defaults = count_result.scalar() or 0
        if other_defaults == 0:
            raise HTTPException(400, "Es muss mindestens ein Standard-Ablagebereich existieren")
        scope.is_default = False

    await session.flush()
    return _scope_to_response(scope)


@router.delete("/filing-scopes/{scope_id}", status_code=204)
async def delete_filing_scope(
    scope_id: str,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Loescht einen Ablagebereich. Dokumente + Archiv-Dateien wandern in den
    Standard-Bereich (H-6).

    Rollback-sicher: erst alle Dateien kopieren, dann DB committen, dann
    Originale loeschen. Kann eine Datei nicht kopiert werden, bricht der
    Vorgang ohne Aenderung ab. Der alte Slug-Ordner wird nie rekursiv
    geloescht — nur leer gewordene Ordner werden entfernt. (Vorher: rmtree
    loeschte Dateien von Dokumenten, die per PATCH/Review in einen anderen
    Bereich gewechselt waren, und bei leerem Slug das ganze Archiv.)
    """
    result = await session.execute(
        select(FilingScope).where(FilingScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        raise HTTPException(404, "Ablagebereich nicht gefunden")

    if scope.is_default:
        raise HTTPException(400, "Standard-Ablagebereich kann nicht gelöscht werden")

    # Sicherstellen dass nicht der letzte
    count_result = await session.execute(
        select(func.count()).select_from(FilingScope)
    )
    total = count_result.scalar() or 0
    if total <= 1:
        raise HTTPException(400, "Der letzte Ablagebereich kann nicht gelöscht werden")

    # Default-Scope finden
    default_result = await session.execute(
        select(FilingScope).where(FilingScope.is_default.is_(True))
    )
    default_scope = default_result.scalar_one_or_none()
    if default_scope is None:
        raise HTTPException(500, "Kein Standard-Ablagebereich konfiguriert — Löschen abgebrochen")

    archive_root = Path(settings.ARCHIVE_DIR).resolve()
    old_prefix = (archive_root / scope.slug).resolve() if scope.slug else archive_root
    # Leerer/kaputter Slug (Altbestand): Scope-Ordner waere das Archiv selbst
    # -> keinerlei Datei-Operationen, nur DB umhaengen.
    file_ops = is_strict_subdir(old_prefix, archive_root)

    # 1. Betroffen: Dokumente des Bereichs UND Dateien, die noch unter dem
    # alten Slug-Ordner liegen (Dokument hat per PATCH/Review den Bereich
    # gewechselt, die Datei aber nicht).
    conditions = [Document.filing_scope_id == scope_id]
    if file_ops:
        conditions.append(Document.file_path.contains(scope.slug))
    docs_result = await session.execute(select(Document).where(or_(*conditions)))
    docs = docs_result.scalars().all()
    reassigned_ids = [d.id for d in docs if d.filing_scope_id == scope_id]

    moves: list[FileMove] = []
    if file_ops:
        slugs = await known_scope_slugs(session)
        scope_slugs = {s.id: s.slug for s in (await session.execute(select(FilingScope))).scalars()}
        for doc in docs:
            source = Path(doc.file_path).resolve()
            if doc.filing_scope_id != scope_id and not source.is_relative_to(old_prefix):
                continue  # Treffer der Substring-Vorauswahl, liegt woanders
            target_slug = (
                default_scope.slug
                if doc.filing_scope_id in (scope_id, None)
                else scope_slugs.get(doc.filing_scope_id, default_scope.slug)
            )
            move = plan_move(doc, target_slug, archive_root, slugs)
            if move:
                moves.append(move)

    # 2. Alle Dateien kopieren — bei Fehler bleibt alles unveraendert.
    try:
        await copy_files(moves)
    except RelocationError as exc:
        raise HTTPException(500, f"{exc} Der Ablagebereich wurde nicht gelöscht.")

    # 3. DB umhaengen + committen
    new_paths = {m.document_id: str(m.target) for m in moves}
    for doc in docs:
        if doc.id in new_paths:
            doc.file_path = new_paths[doc.id]
    await session.execute(
        update(Document)
        .where(Document.filing_scope_id == scope_id)
        .values(filing_scope_id=default_scope.id)
    )
    await session.delete(scope)
    try:
        await session.commit()
    except Exception:
        await discard_copies(moves)
        raise

    # 4. Originale loeschen + leer gewordene Ordner entfernen (nur leere)
    await remove_sources(moves, archive_root)
    if file_ops:
        await asyncio.to_thread(prune_empty_dirs, old_prefix, archive_root)

    logger.info(
        "Ablagebereich '%s' gelöscht: %d Dokumente umgehängt, %d Dateien verschoben",
        scope.name, len(reassigned_ids), len(moves),
    )

    # 5. Chroma-Metadaten (filing_scope_id) nachziehen — sonst verschwinden
    # die Dokumente aus dem scope-gefilterten Chat. Best effort, post-commit
    # (der Helfer loggt Fehler selbst).
    if reassigned_ids:
        await vectorize_service.update_scope_metadata(reassigned_ids, default_scope.id, settings)

    return Response(status_code=204)
