"""Archivdateien zwischen Ablagebereich-Ordnern verschieben — rollback-sicher.

Archivstruktur: ``{ARCHIVE_DIR}/{scope_slug}/{jahr}/{monat}/{typ}/{datei}``.
Wechselt ein Dokument den Ablagebereich, wandert die Datei in den Ordner des
neuen Bereichs. Ablauf wie bei der Archivierung (copy + delete statt move):

1. :func:`copy_files` kopiert alle Dateien an ihr Ziel. Schlaegt eine Kopie
   fehl, werden die bisherigen Kopien entfernt und :class:`RelocationError`
   geworfen — Dateien und DB bleiben unveraendert.
2. Der Aufrufer setzt ``file_path`` und committet. Scheitert der Commit,
   raeumt :func:`discard_copies` die Kopien wieder weg.
3. Erst nach dem Commit loescht :func:`remove_sources` die Originale und
   entfernt leer gewordene Ordner (nur leere — nie ``rmtree``).
"""

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.filing_scope import FilingScope

logger = logging.getLogger(__name__)

RELOCATION_FAILED_MESSAGE = (
    "Die Dokumentdateien konnten nicht in den neuen Ablagebereich verschoben "
    "werden. Es wurde nichts geändert."
)


class RelocationError(Exception):
    """Mindestens eine Datei konnte nicht kopiert werden (nichts geaendert)."""


@dataclass
class FileMove:
    document_id: str
    source: Path
    target: Path


def is_strict_subdir(path: Path, root: Path) -> bool:
    """True, wenn ``path`` echt unterhalb von ``root`` liegt (nicht gleich)."""
    return path != root and path.is_relative_to(root)


async def known_scope_slugs(session: AsyncSession) -> set[str]:
    result = await session.execute(select(FilingScope.slug))
    return {row[0] for row in result.all() if row[0]}


def plan_move(
    doc: Document,
    target_slug: str,
    archive_root: Path,
    known_slugs: set[str],
) -> FileMove | None:
    """Berechnet Quelle/Ziel fuer die Datei eines Dokuments.

    ``None`` heisst: nichts zu verschieben (Datei fehlt, liegt ausserhalb des
    Archivs oder schon im richtigen Ordner). Die erste Pfadkomponente wird
    als Scope-Ordner erkannt, wenn sie ein bekannter Slug ist — so funktioniert
    das auch fuer Dateien, die nach einem frueheren Scope-Wechsel noch unter
    einem anderen Slug liegen. Legacy-Dateien ohne Scope-Ordner
    (``archive/2024/...``) behalten ihren relativen Pfad unter dem Ziel-Slug.
    """
    if not target_slug:
        return None
    archive_root = archive_root.resolve()
    source = Path(doc.file_path).resolve()
    if not is_strict_subdir(source, archive_root):
        logger.info("Datei %s liegt nicht im Archiv — nicht verschoben", source)
        return None
    if not source.is_file():
        logger.warning("Datei %s fuer Dokument %s fehlt — nicht verschoben", source, doc.id)
        return None

    relative = source.relative_to(archive_root)
    parts = relative.parts
    if len(parts) > 1 and parts[0] in known_slugs:
        parts = parts[1:]
    target = archive_root.joinpath(target_slug, *parts)
    if target == source:
        return None
    if target.exists():
        # Kollision (sollte dank UUID-Prefix nie vorkommen) — nie ueberschreiben.
        target = target.with_name(f"{target.stem}_{doc.id[:8]}{target.suffix}")
    return FileMove(document_id=doc.id, source=source, target=target)


def _copy_sync(move: FileMove) -> None:
    move.target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(move.source, move.target)


def _unlink_sync(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Konnte %s nicht loeschen", path, exc_info=True)


async def copy_files(moves: list[FileMove]) -> None:
    """Kopiert alle Dateien; bei einem Fehler werden alle Kopien entfernt."""
    done: list[FileMove] = []
    for move in moves:
        try:
            await asyncio.to_thread(_copy_sync, move)
        except OSError as exc:
            logger.error(
                "Kopieren %s -> %s fehlgeschlagen: %s", move.source, move.target, exc,
            )
            await discard_copies(done)
            raise RelocationError(RELOCATION_FAILED_MESSAGE) from exc
        done.append(move)


async def discard_copies(moves: list[FileMove]) -> None:
    """Entfernt die Kopien (DB-Commit gescheitert oder Abbruch)."""
    for move in moves:
        await asyncio.to_thread(_unlink_sync, move.target)


def prune_empty_dirs(directory: Path, stop_at: Path) -> None:
    """Entfernt ``directory`` samt leerer Unterordner (bottom-up, nur leere).

    ``stop_at`` (das Archiv-Wurzelverzeichnis) wird nie entfernt. Ordner mit
    Dateien bleiben stehen — auch solche, die keinem Dokument mehr gehoeren.
    """
    directory = directory.resolve()
    stop_at = stop_at.resolve()
    if not is_strict_subdir(directory, stop_at) or not directory.is_dir():
        return
    for current, _dirs, _files in os.walk(directory, topdown=False):
        try:
            os.rmdir(current)
        except OSError:
            pass  # nicht leer (oder gesperrt) — stehen lassen


def _prune_parents(path: Path, stop_at: Path) -> None:
    """Entfernt leer gewordene Elternordner von ``path`` bis unter ``stop_at``."""
    parent = path.parent
    while is_strict_subdir(parent, stop_at):
        try:
            os.rmdir(parent)
        except OSError:
            return
        parent = parent.parent


async def remove_sources(moves: list[FileMove], archive_root: Path) -> None:
    """Loescht die Originale nach erfolgreichem Commit (best effort)."""
    archive_root = archive_root.resolve()
    for move in moves:
        await asyncio.to_thread(_unlink_sync, move.source)
        await asyncio.to_thread(_prune_parents, move.source, archive_root)


async def relocate_document_file(
    session: AsyncSession,
    doc: Document,
    new_scope: FilingScope,
    archive_dir: str,
) -> None:
    """Weist ``doc`` dem Bereich ``new_scope`` zu, verschiebt die Datei und committet.

    Raises:
        RelocationError: Datei nicht kopierbar — nichts wurde geaendert.
    """
    archive_root = Path(archive_dir).resolve()
    move = plan_move(doc, new_scope.slug, archive_root, await known_scope_slugs(session))
    moves = [move] if move else []
    await copy_files(moves)
    doc.filing_scope_id = new_scope.id
    if move:
        doc.file_path = str(move.target)
    try:
        await session.commit()
    except Exception:
        await discard_copies(moves)
        raise
    await remove_sources(moves, archive_root)
