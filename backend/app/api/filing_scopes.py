"""Ablagebereiche (Filing Scopes) API."""

import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.document import Document, DocumentStatus
from app.models.filing_scope import FilingScope, generate_slug
from app.schemas.filing_scope import FilingScopeCreate, FilingScopeResponse, FilingScopeUpdate

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
async def list_filing_scopes(session: AsyncSession = Depends(get_db)):
    """Listet alle Ablagebereiche auf."""
    result = await session.execute(
        select(FilingScope).order_by(FilingScope.is_default.desc(), FilingScope.name)
    )
    scopes = result.scalars().all()
    return [_scope_to_response(s) for s in scopes]


@router.post("/filing-scopes", response_model=FilingScopeResponse, status_code=201)
async def create_filing_scope(
    data: FilingScopeCreate,
    session: AsyncSession = Depends(get_db),
):
    """Erstellt einen neuen Ablagebereich."""
    # Pruefen ob Name schon existiert
    existing = await session.execute(
        select(FilingScope).where(FilingScope.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Ablagebereich '{data.name}' existiert bereits")

    slug = generate_slug(data.name)

    # Slug-Kollision pruefen
    slug_check = await session.execute(
        select(FilingScope).where(FilingScope.slug == slug)
    )
    if slug_check.scalar_one_or_none():
        raise HTTPException(409, f"Slug '{slug}' existiert bereits")

    # Wenn neuer Scope Default sein soll, andere zuruecksetzen
    if data.is_default:
        from sqlalchemy import update
        await session.execute(
            update(FilingScope).values(is_default=False)
        )

    scope = FilingScope(
        name=data.name,
        slug=slug,
        description=data.description,
        keywords=json.dumps(data.keywords, ensure_ascii=False),
        is_default=data.is_default,
        color=data.color,
    )
    session.add(scope)
    await session.flush()
    return _scope_to_response(scope)


@router.patch("/filing-scopes/{scope_id}", response_model=FilingScopeResponse)
async def update_filing_scope(
    scope_id: str,
    data: FilingScopeUpdate,
    session: AsyncSession = Depends(get_db),
):
    """Aktualisiert einen Ablagebereich."""
    result = await session.execute(
        select(FilingScope).where(FilingScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        raise HTTPException(404, "Ablagebereich nicht gefunden")

    if data.name is not None and data.name != scope.name:
        existing = await session.execute(
            select(FilingScope).where(FilingScope.name == data.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, f"Name '{data.name}' existiert bereits")
        scope.name = data.name
        # Slug bleibt stabil - Aenderung wuerde Archiv-Dateipfade brechen

    if data.description is not None:
        scope.description = data.description

    if data.keywords is not None:
        scope.keywords = json.dumps(data.keywords, ensure_ascii=False)

    if data.color is not None:
        scope.color = data.color

    if data.is_default is True:
        from sqlalchemy import update
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


@router.delete("/filing-scopes/{scope_id}")
async def delete_filing_scope(
    scope_id: str,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Loescht einen Ablagebereich. Dokumente + Archiv-Dateien werden auf den
    Default umgehaengt (H-6). Vorher: Dateien blieben unter dem alten Slug-Ordner
    liegen (`data/archive/{old_slug}/...`) und waren ueber Backups inkonsistent.
    """
    result = await session.execute(
        select(FilingScope).where(FilingScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        raise HTTPException(404, "Ablagebereich nicht gefunden")

    if scope.is_default:
        raise HTTPException(400, "Standard-Ablagebereich kann nicht geloescht werden")

    # Sicherstellen dass nicht der letzte
    count_result = await session.execute(
        select(func.count()).select_from(FilingScope)
    )
    total = count_result.scalar() or 0
    if total <= 1:
        raise HTTPException(400, "Der letzte Ablagebereich kann nicht geloescht werden")

    # Default-Scope finden
    default_result = await session.execute(
        select(FilingScope).where(FilingScope.is_default.is_(True))
    )
    default_scope = default_result.scalar_one_or_none()
    if default_scope is None:
        raise HTTPException(500, "Kein Standard-Ablagebereich konfiguriert — Loeschen abgebrochen")

    # 1. Betroffene Dokumente holen (vor UPDATE) damit wir die Dateien physisch
    # umkopieren koennen. file_path zeigt aktuell auf {archive}/{old_slug}/...
    docs_result = await session.execute(
        select(Document).where(Document.filing_scope_id == scope_id)
    )
    docs = docs_result.scalars().all()

    archive_root = Path(settings.ARCHIVE_DIR).resolve()
    old_prefix = archive_root / scope.slug
    new_prefix = archive_root / default_scope.slug

    # 2. Dateien verschieben + file_path aktualisieren
    for doc in docs:
        try:
            old_path = Path(doc.file_path).resolve()
            # Sicherheit: nur verschieben wenn die Datei wirklich unter dem
            # alten Scope-Ordner liegt (verhindert Schaden bei manipuliertem
            # file_path).
            if not old_path.is_file():
                logger.warning("Datei %s existiert nicht, ueberspringe", old_path)
                continue
            try:
                relative = old_path.relative_to(old_prefix)
            except ValueError:
                # Datei lebt nicht unter dem alten Scope-Pfad — nur DB-Update,
                # Datei wird beim naechsten Re-Index/Backup zurechtgerueckt.
                logger.info("Datei %s liegt nicht unter %s — nur DB aktualisiert", old_path, old_prefix)
                continue
            new_path = new_prefix / relative
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))
            doc.file_path = str(new_path)
        except Exception:
            logger.warning("Konnte Datei fuer Dokument %s nicht verschieben", doc.id, exc_info=True)

    # 3. DB-Verknuepfung umhaengen (UPDATE auf alle, falls oben uebersprungen)
    from sqlalchemy import update
    await session.execute(
        update(Document)
        .where(Document.filing_scope_id == scope_id)
        .values(filing_scope_id=default_scope.id)
    )

    await session.delete(scope)
    await session.flush()

    # 4. Leeren alten Scope-Ordner aufraeumen (best-effort)
    try:
        if old_prefix.exists():
            shutil.rmtree(old_prefix, ignore_errors=True)
    except Exception:
        logger.warning("Konnte alten Scope-Ordner nicht entfernen: %s", old_prefix)

    return {"message": f"Ablagebereich '{scope.name}' geloescht ({len(docs)} Dokumente verschoben)"}
