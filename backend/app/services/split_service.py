"""Lebenszyklus aufzuteilender Stapel-PDFs (Spezifikation 12).

Zustaende: siehe ``SplitBatchStatus``. Jeder Statuswechsel laeuft ueber
``transition`` (optimistisches Locking: ``UPDATE ... WHERE status=:alt AND
version=:v``) — Doppelklick und zwei Geraete gleichzeitig fuehren zu genau
einer Aenderung, der zweite Aufruf bekommt ``SplitConflictError`` (HTTP 409).

Dateiablage (``Settings.split_dir``):

- ``<id>/source.pdf`` — Stapeldatei, aus UPLOAD_DIR per Kopieren -> Commit ->
  Loeschen uebernommen (gleiche Rollback-Sicherheit wie die Archivierung).
- ``<id>/teil-01.pdf`` ... — Teildateien; der Worker archiviert sie wie jede
  andere Job-Datei (kopieren, nach dem Commit loeschen).
- ``THUMBNAIL_DIR/split/<id>/p001.png`` (Raster) und ``v001.jpg`` (Grossansicht).

Die Funktionen committen selbst, wo ein Statuswechsel und Dateioperationen
zusammengehoeren (Dateien erst nach dem Commit loeschen).
"""

import asyncio
import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.file_utils import generate_stored_filename, is_pdf_type
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, Tag
from app.models.email_account import EmailAccount
from app.models.processing_job import (
    JOB_ERROR_DUPLICATE,
    JOB_ERROR_SPLIT_SOURCE_MISSING,
    JobSource,
    JobStatus,
    ProcessingJob,
    SplitMode,
    SplitRole,
)
from app.models.split_batch import (
    ACTIVE_BATCH_STATUSES,
    DUPLICATE_BATCH_STATUSES,
    SplitBatch,
    SplitBatchStatus,
    SplitOrigin,
)
from app.services import pdf_split_service
from app.services.archive_service import SplitProvenance, file_sha256, find_active_duplicate
from app.services.pdf_split_service import PdfSplitError
from app.services.settings_service import get_db_setting, set_db_setting
from app.services.upload_service import remove_job_file

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SOURCE_NAME = "source.pdf"
_PART_FILE_RE = re.compile(r"^teil-\d{2,3}\.pdf(\.tmp)?$")
# Teil-Jobs in diesem Zustand sind fertig (DONE / DONE_WITH_ERRORS).
_PART_TERMINAL = (JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED)
_SOURCE_PAGES_MAX = 40  # Document.source_pages
# Erreicht ein Stapel einen dieser Zustaende, werden die Kopfzeilen-Hashes in
# ``pages`` auf [] gekuerzt (werden nur fuer die Erkennung gebraucht).
_TRIM_HEADER_STATUSES = frozenset({
    SplitBatchStatus.DONE,
    SplitBatchStatus.DONE_WITH_ERRORS,
    SplitBatchStatus.KEPT_WHOLE,
    SplitBatchStatus.DISCARDED,
    SplitBatchStatus.UNDONE,
    SplitBatchStatus.FAILED,
})
# Benutzer-Aenderungen an einem Teil sperren "Aufteilung rueckgaengig" — alle
# diese AuditLog-Eintraege schreibt nur die API (Benutzeraktionen).
_USER_EDIT_ACTIONS = (
    AuditAction.UPDATED,
    AuditAction.TAG_ADDED,
    AuditAction.TAG_REMOVED,
    AuditAction.REVIEWED,
)
# Aufbewahrung (SPLIT_KEEP_SOURCE_DAYS): danach loescht der taegliche
# Wartungslauf Stapeldatei, Teil-Reste und Vorschaubilder.
_CLEANUP_STATUSES = (
    SplitBatchStatus.DONE,
    SplitBatchStatus.KEPT_WHOLE,
    SplitBatchStatus.FAILED,
    SplitBatchStatus.DISCARDED,
    SplitBatchStatus.UNDONE,
)
CLEANUP_INTERVAL_SECONDS = 24 * 3600


class SplitError(Exception):
    """Basisklasse; ``message`` ist ein deutscher Text fuer die Oberflaeche."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SplitNotFoundError(SplitError):
    status_code = 404


class SplitConflictError(SplitError):
    status_code = 409

    def __init__(self, message: str = "Der Stapel wurde inzwischen geändert. Bitte neu laden."):
        super().__init__(message)


class SplitValidationError(SplitError):
    status_code = 422


class SplitSourceMissingError(SplitError):
    status_code = 409
    code = JOB_ERROR_SPLIT_SOURCE_MISSING

    def __init__(self):
        super().__init__(
            "Die Stapeldatei ist nicht mehr vorhanden – der Stapel kann nur noch verworfen werden."
        )


# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------


def batch_dir(settings: Settings, batch_id: str) -> Path:
    return settings.split_dir / batch_id


def batch_thumbnail_dir(settings: Settings, batch_id: str) -> Path:
    return settings.split_thumbnail_dir / batch_id


def part_path(settings: Settings, batch_id: str, index: int) -> Path:
    return batch_dir(settings, batch_id) / f"teil-{index:02d}.pdf"


def page_image_path(settings: Settings, batch_id: str, page: int, *, preview: bool) -> Path | None:
    """Vorschaubild einer Seite; None bei ungueltiger ID (Pfad-Guard fuer die API)."""
    if not _UUID_RE.match(batch_id) or page < 1:
        return None
    root = settings.split_thumbnail_dir.resolve()
    name = f"v{page:03d}.jpg" if preview else f"p{page:03d}.png"
    candidate = (root / batch_id / name).resolve()
    return candidate if candidate.is_relative_to(root) else None


def _safe_batch_dirs(settings: Settings, batch_id: str) -> list[Path]:
    """Ordner eines Stapels, nur wenn sie wirklich unter data/split/ bzw.
    THUMBNAIL_DIR/split/ liegen (IDs stammen aus der DB)."""
    if not _UUID_RE.match(batch_id):
        return []
    out = []
    for root in (settings.split_dir, settings.split_thumbnail_dir):
        root = root.resolve()
        candidate = (root / batch_id).resolve()
        if candidate.parent == root:
            out.append(candidate)
    return out


def remove_batch_thumbnails(settings: Settings, batch_id: str) -> None:
    """Nur die Vorschaubilder (Stapel FAILED, Quelle laeuft normal weiter)."""
    for directory in _safe_batch_dirs(settings, batch_id):
        if directory.parent == settings.split_thumbnail_dir.resolve():
            shutil.rmtree(directory, ignore_errors=True)


def remove_batch_files(settings: Settings, batch_id: str) -> None:
    """Stapel-, Teildateien und Vorschaubilder eines Stapels loeschen (nach Commit)."""
    for directory in _safe_batch_dirs(settings, batch_id):
        shutil.rmtree(directory, ignore_errors=True)


def _remove_part_files(settings: Settings, batch_id: str) -> None:
    """Liegengebliebene ``teil-*.pdf(.tmp)`` (Absturz vor parts_written)."""
    for directory in _safe_batch_dirs(settings, batch_id):
        if directory.parent != settings.split_dir.resolve() or not directory.is_dir():
            continue
        for path in directory.iterdir():
            if _PART_FILE_RE.match(path.name):
                path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def load_pages(batch: SplitBatch) -> list[dict]:
    return _load_json(batch.pages, [])


def load_proposal(batch: SplitBatch) -> dict:
    return _load_json(batch.proposal, {})


def load_final_parts(batch: SplitBatch) -> dict:
    return _load_json(batch.final_parts, {})


def trim_headers(raw_pages: str | None) -> str | None:
    """``pages``-JSON mit gekuerzten Kopfzeilen (``sig.header = []``)."""
    pages = _load_json(raw_pages, None)
    if not isinstance(pages, list):
        return raw_pages
    for page in pages:
        sig = page.get("sig") if isinstance(page, dict) else None
        if isinstance(sig, dict) and sig.get("header"):
            sig["header"] = []
    return json.dumps(pages)


# ---------------------------------------------------------------------------
# Einstellungen (System -> Verarbeitung)
# ---------------------------------------------------------------------------

SETTING_AUTO_ENABLED = "split_auto_enabled"
SETTING_MIN_PAGES = "split_min_pages"
SETTING_USE_LLM = "split_use_llm"


@dataclass(frozen=True)
class SplitSettings:
    auto_enabled: bool
    min_pages: int
    use_llm: bool = True


async def load_split_settings(session: AsyncSession, settings: Settings) -> SplitSettings:
    """DB-Einstellung hat Vorrang, sonst Config-Default (SPLIT_AUTO_ENABLED,
    SPLIT_MIN_PAGES, SPLIT_USE_LLM). Unlesbare Werte fallen auf den Default zurueck."""
    raw_auto = await get_db_setting(session, SETTING_AUTO_ENABLED, "")
    raw_min = await get_db_setting(session, SETTING_MIN_PAGES, "")
    raw_llm = await get_db_setting(session, SETTING_USE_LLM, "")
    auto = settings.SPLIT_AUTO_ENABLED if raw_auto == "" else raw_auto == "true"
    use_llm = settings.SPLIT_USE_LLM if raw_llm == "" else raw_llm == "true"
    try:
        min_pages = max(2, int(raw_min)) if raw_min != "" else settings.SPLIT_MIN_PAGES
    except ValueError:
        min_pages = settings.SPLIT_MIN_PAGES
    return SplitSettings(auto_enabled=auto, min_pages=min_pages, use_llm=use_llm)


async def save_split_settings(session: AsyncSession, value: SplitSettings) -> None:
    """Schreibt alle Einstellungen (committet NICHT)."""
    await set_db_setting(session, SETTING_AUTO_ENABLED, "true" if value.auto_enabled else "false")
    await set_db_setting(session, SETTING_MIN_PAGES, str(int(value.min_pages)))
    await set_db_setting(session, SETTING_USE_LLM, "true" if value.use_llm else "false")


def format_page_ranges(pages: list[int]) -> str:
    """[3,4,5,7] -> "3-5,7"; laenger als 40 Zeichen -> "erste-letzte"."""
    ranges: list[str] = []
    start = prev = None
    for page in sorted(pages):
        if start is None:
            start = prev = page
        elif page == prev + 1:
            prev = page
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = page
    if start is not None:
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
    text = ",".join(ranges)
    if len(text) > _SOURCE_PAGES_MAX and pages:
        text = f"{min(pages)}-{max(pages)}"
    return text


def _complete(part_ocr: list[dict], pages: list[int]) -> bool:
    return len(part_ocr) == len(pages) and all(e.get("source") != "skipped" for e in part_ocr)


def part_filename(original_filename: str, index: int, count: int) -> str:
    stem = Path(original_filename).stem or "Dokument"
    return f"{stem} (Teil {index} von {count}).pdf"


async def transition(
    session: AsyncSession,
    batch_id: str,
    *,
    expected: SplitBatchStatus | tuple[SplitBatchStatus, ...],
    new: SplitBatchStatus,
    version: int | None = None,
    **values,
) -> None:
    """Atomarer Statuswechsel. rowcount 0 -> SplitConflictError.

    Terminale Zustaende kuerzen die Kopfzeilen-Hashes der Seiten (Spezifikation:
    "wird beim Erreichen eines terminalen Zustands auf [] gekuerzt")."""
    expected_statuses = expected if isinstance(expected, tuple) else (expected,)
    if new in _TRIM_HEADER_STATUSES and "pages" not in values:
        raw = (await session.execute(
            select(SplitBatch.pages).where(SplitBatch.id == batch_id)
        )).scalar_one_or_none()
        if raw:
            values["pages"] = trim_headers(raw)
    statement = (
        update(SplitBatch)
        .where(SplitBatch.id == batch_id, SplitBatch.status.in_(expected_statuses))
        .values(status=new, version=SplitBatch.version + 1, **values)
        .execution_options(synchronize_session=False)
    )
    if version is not None:
        statement = statement.where(SplitBatch.version == version)
    result = await session.execute(statement)
    if result.rowcount == 0:
        raise SplitConflictError()


async def get_batch(session: AsyncSession, batch_id: str) -> SplitBatch:
    batch = await session.get(SplitBatch, batch_id, populate_existing=True)
    if batch is None:
        raise SplitNotFoundError("Stapel nicht gefunden")
    return batch


async def _source_job(session: AsyncSession, batch: SplitBatch) -> ProcessingJob | None:
    if not batch.source_job_id:
        return None
    return await session.get(ProcessingJob, batch.source_job_id, populate_existing=True)


async def _live_email_account_id(session: AsyncSession, account_id: int | None) -> int | None:
    """E-Mail-Konto fuer Teil-/Stapel-Jobs nur, wenn es noch existiert.

    ``split_batches.email_account_id`` hat bewusst keinen FK (kein Zyklus):
    wird das Konto geloescht, waehrend der Stapel auf die Aufteilung wartet,
    bliebe die ID stehen — ein neuer Job mit dieser ID verletzte den FK
    ``processing_jobs.email_account_id`` und das Bestaetigen scheiterte
    dauerhaft. Ohne Konto werden die Teile wie ein Upload abgelegt."""
    if account_id is None:
        return None
    exists = (await session.execute(
        select(EmailAccount.id).where(EmailAccount.id == account_id)
    )).scalar_one_or_none()
    return account_id if exists is not None else None


async def part_jobs(session: AsyncSession, batch_id: str) -> list[ProcessingJob]:
    result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.split_batch_id == batch_id, ProcessingJob.split_role == SplitRole.PART.value)
        .order_by(ProcessingJob.split_part_index)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Anlegen
# ---------------------------------------------------------------------------


async def create_batch(
    session: AsyncSession,
    job: ProcessingJob,
    settings: Settings,
    *,
    page_count: int,
) -> SplitBatch:
    """Stapel fuer einen Upload-/Watch-/E-Mail-Job anlegen (Worker, Modus AUTO
    oder FORCE). Findet die automatische Pruefung nur ein Dokument, loescht
    ``split_detection_service`` den Stapel wieder.

    Quelle nach ``data/split/<id>/source.pdf`` kopieren, Stapel + Job
    committen, erst danach die Upload-Kopie loeschen.
    """
    batch_id = str(uuid.uuid4())
    dest = batch_dir(settings, batch_id) / _SOURCE_NAME
    old_path = Path(job.file_path)
    file_hash = await file_sha256(old_path)
    await asyncio.to_thread(_copy, old_path, dest)
    try:
        session.add(SplitBatch(
            id=batch_id,
            origin=SplitOrigin.AUTO,
            status=SplitBatchStatus.DETECTING,
            source_job_id=job.id,
            email_account_id=job.email_account_id,
            job_source=str(getattr(job.source, "value", job.source)),
            original_filename=job.original_filename,
            file_path=str(dest),
            file_hash=file_hash,
            page_count=page_count,
        ))
        await session.flush()
        job.split_batch_id = batch_id
        job.split_role = SplitRole.SOURCE.value
        job.file_path = str(dest)
        await session.commit()
    except BaseException:
        remove_batch_files(settings, batch_id)
        raise
    if old_path.resolve() != dest.resolve():
        await asyncio.to_thread(remove_job_file, old_path, settings)
    logger.info("Stapel %s fuer Job %s angelegt (%d Seiten)", batch_id, job.id, page_count)
    return await get_batch(session, batch_id)


def _copy(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(dest))


async def active_batch_for_document(session: AsyncSession, document_id: str) -> SplitBatch | None:
    result = await session.execute(
        select(SplitBatch).where(
            SplitBatch.source_document_id == document_id,
            SplitBatch.status.in_(ACTIVE_BATCH_STATUSES),
        )
    )
    return result.scalars().first()


async def request_document_split(
    session: AsyncSession,
    document: Document,
    settings: Settings,
) -> tuple[SplitBatch, ProcessingJob]:
    """"Dokument aufteilen": Stapel (MANUAL) + Stapel-Job mit einer Kopie der
    Archivdatei anlegen. Die Erkennung (Seitentext, Vorschaubilder) laeuft im
    Queue-Worker; das Original bleibt bis zur Archivierung aller Teile aktiv.

    Raises:
        SplitValidationError (422), SplitConflictError (409).
    """
    if document.status == DocumentStatus.DELETED:
        raise SplitValidationError("Gelöschte Dokumente können nicht aufgeteilt werden.")
    if not is_pdf_type(document.file_type):
        raise SplitValidationError("Nur PDF-Dokumente können aufgeteilt werden.")
    source = Path(document.file_path).resolve()
    if not source.is_relative_to(Path(settings.ARCHIVE_DIR).resolve()) or not source.is_file():
        raise SplitValidationError("Die Datei des Dokuments ist nicht mehr vorhanden.")
    if await active_batch_for_document(session, document.id) is not None:
        raise SplitConflictError("Für dieses Dokument läuft bereits eine Aufteilung.")
    try:
        count = await asyncio.to_thread(pdf_split_service.page_count, source)
    except PdfSplitError as exc:
        raise SplitValidationError("Die Datei kann nicht aufgeteilt werden.") from exc
    if count < 2:
        raise SplitValidationError("Das Dokument hat nur eine Seite.")
    if count > settings.SPLIT_MAX_PAGES:
        raise SplitValidationError(
            f"Zu viele Seiten für die Aufteilung ({count}, höchstens {settings.SPLIT_MAX_PAGES})."
        )

    batch_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    dest = batch_dir(settings, batch_id) / _SOURCE_NAME
    await asyncio.to_thread(_copy, source, dest)
    try:
        batch = SplitBatch(
            id=batch_id,
            origin=SplitOrigin.MANUAL,
            status=SplitBatchStatus.DETECTING,
            source_job_id=job_id,
            source_document_id=document.id,
            job_source=JobSource.SPLIT_REQUEST.value,
            original_filename=document.original_filename,
            file_path=str(dest),
            file_hash=document.file_hash,
            page_count=count,
        )
        session.add(batch)
        await session.flush()
        # Doppelklick / zwei Geraete: die Vorabpruefung oben ist nicht atomar.
        # Nach dem eigenen INSERT haelt diese Transaktion die SQLite-Schreib-
        # sperre — ein paralleler Aufruf sieht hier den schon committeten Stapel.
        others = (await session.execute(
            select(func.count(SplitBatch.id)).where(
                SplitBatch.source_document_id == document.id,
                SplitBatch.status.in_(ACTIVE_BATCH_STATUSES),
                SplitBatch.id != batch_id,
            )
        )).scalar_one()
        if others:
            raise SplitConflictError("Für dieses Dokument läuft bereits eine Aufteilung.")
        job = ProcessingJob(
            id=job_id,
            original_filename=document.original_filename,
            stored_filename=generate_stored_filename(document.original_filename),
            file_path=str(dest),
            file_type="pdf",
            file_size_bytes=dest.stat().st_size,
            source=JobSource.SPLIT_REQUEST,
            status=JobStatus.PENDING,
            split_batch_id=batch_id,
            split_role=SplitRole.SOURCE.value,
            split_mode=SplitMode.FORCE.value,
        )
        session.add(job)
        await session.commit()
    except BaseException:
        await session.rollback()
        remove_batch_files(settings, batch_id)
        raise
    logger.info("Aufteilung von Dokument %s angefordert (Stapel %s, %d Seiten)", document.id, batch_id, count)
    return batch, job


# ---------------------------------------------------------------------------
# Bestaetigen / Behalten / Verwerfen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartSpec:
    index: int
    pages: tuple[int, ...]


def validate_parts(
    parts: list[list[int]],
    excluded: list[int],
    pages: list[dict],
    page_count: int,
    max_parts: int,
) -> list[PartSpec]:
    """Prueft eine Aufteilung (Seiten 1-basiert). Raises SplitValidationError.

    - jede Seite gehoert genau einem Teil oder ist ausgeschlossen
    - Seiten je Teil aufsteigend und zusammenhaengend (Ausschluesse erlaubt)
    - Teile in Seitenreihenfolge, jeder mit mindestens einer Nicht-Leerseite
    - hoechstens ``max_parts`` Teile, mindestens eine Seite insgesamt
    """
    if not parts or not any(parts):
        raise SplitValidationError("Mindestens eine Seite muss übernommen werden.")
    if len(parts) > max_parts:
        raise SplitValidationError(f"Höchstens {max_parts} Teile pro Stapel.")
    excluded_set = set(excluded)
    if len(excluded_set) != len(excluded):
        raise SplitValidationError("Eine Seite ist mehrfach ausgeschlossen.")
    seen: set[int] = set()
    for part in parts:
        if not part:
            raise SplitValidationError("Ein Teil enthält keine Seite.")
        for page in part:
            if not isinstance(page, int) or not 1 <= page <= page_count:
                raise SplitValidationError(f"Seite {page} gibt es in diesem Stapel nicht.")
            if page in seen or page in excluded_set:
                raise SplitValidationError(f"Seite {page} ist mehreren Teilen zugeordnet.")
            seen.add(page)
    for page in excluded_set:
        if not isinstance(page, int) or not 1 <= page <= page_count:
            raise SplitValidationError(f"Seite {page} gibt es in diesem Stapel nicht.")
    missing = set(range(1, page_count + 1)) - seen - excluded_set
    if missing:
        raise SplitValidationError(
            f"Seite {min(missing)} ist keinem Teil zugeordnet und nicht ausgeschlossen."
        )

    blank = {p.get("n") for p in pages if p.get("blank")}
    previous_last = 0
    out: list[PartSpec] = []
    for index, part in enumerate(parts, start=1):
        if list(part) != sorted(part):
            raise SplitValidationError(f"Die Seiten von Teil {index} sind nicht aufsteigend.")
        if part[0] < previous_last:
            raise SplitValidationError("Die Teile müssen in Seitenreihenfolge angegeben werden.")
        gaps = set(range(part[0], part[-1] + 1)) - set(part)
        if gaps - excluded_set:
            raise SplitValidationError(f"Teil {index} ist nicht zusammenhängend.")
        if all(page in blank for page in part):
            raise SplitValidationError(f"Teil {index} besteht nur aus Leerseiten.")
        previous_last = part[-1]
        out.append(PartSpec(index=index, pages=tuple(part)))
    return out


def _write_parts(settings: Settings, batch: SplitBatch, parts: list[PartSpec]) -> None:
    source = Path(batch.file_path)
    for part in parts:
        pdf_split_service.write_part(source, list(part.pages), part_path(settings, batch.id, part.index))


async def confirm(
    session: AsyncSession,
    batch_id: str,
    *,
    version: int,
    parts: list[list[int]],
    excluded: list[int],
    settings: Settings,
) -> list[str]:
    """Aufteilung bestaetigen: Teildateien schreiben, Teil-Jobs anlegen.

    PROPOSED -> SPLITTING (parts_written=false) wird zuerst committet; danach
    entstehen die Teildateien (tmp+verify+replace), dann in einer zweiten
    Transaktion parts_written=true + Teil-Jobs + Stapel-Job SPLIT. Scheitert
    das Schreiben, geht der Stapel zurueck auf PROPOSED.

    Returns: IDs der Teil-Jobs.
    """
    batch = await get_batch(session, batch_id)
    if batch.status != SplitBatchStatus.PROPOSED or batch.version != version:
        raise SplitConflictError()
    specs = validate_parts(parts, excluded, load_pages(batch), batch.page_count, settings.SPLIT_MAX_PARTS)

    if not Path(batch.file_path).is_file():
        job = await _source_job(session, batch)
        if job is not None:
            job.error_code = JOB_ERROR_SPLIT_SOURCE_MISSING
        batch.error_message = SplitSourceMissingError().message
        await session.commit()
        raise SplitSourceMissingError()

    final = {
        "parts": [{"index": s.index, "pages": list(s.pages)} for s in specs],
        "excluded": sorted(set(excluded)),
        "parts_written": False,
    }
    await transition(
        session, batch_id,
        expected=SplitBatchStatus.PROPOSED, new=SplitBatchStatus.SPLITTING, version=version,
        final_parts=json.dumps(final), confirmed_at=_now(), error_message=None,
    )
    await session.commit()

    batch = await get_batch(session, batch_id)
    try:
        await asyncio.to_thread(_write_parts, settings, batch, specs)
    except BaseException as exc:
        await _revert_to_proposed(session, batch_id, settings, "Die Teile konnten nicht geschrieben werden.")
        if isinstance(exc, PdfSplitError):
            raise SplitValidationError("Die Datei kann nicht aufgeteilt werden.") from exc
        raise

    try:
        job_ids = await create_part_jobs(session, batch_id, settings)
    except BaseException:
        # z.B. "database is locked": ohne Teil-Jobs nicht bis zum Neustart in
        # SPLITTING haengen lassen — der Benutzer kann erneut bestaetigen.
        await _revert_to_proposed(session, batch_id, settings, "Die Teile konnten nicht angelegt werden.")
        raise
    logger.info("Stapel %s in %d Teile aufgeteilt", batch_id, len(specs))
    return job_ids


async def _revert_to_proposed(session: AsyncSession, batch_id: str, settings: Settings, message: str) -> None:
    """SPLITTING -> PROPOSED nach einem Fehler beim Bestaetigen, solange keine
    Teil-Jobs angelegt sind (parts_written=false); Teildateien entfernen."""
    await session.rollback()
    batch = await get_batch(session, batch_id)
    if batch.status != SplitBatchStatus.SPLITTING or load_final_parts(batch).get("parts_written"):
        return  # Teil-Jobs existieren — die Teildateien gehoeren jetzt ihnen
    await asyncio.to_thread(_remove_part_files, settings, batch_id)
    await transition(
        session, batch_id,
        expected=SplitBatchStatus.SPLITTING, new=SplitBatchStatus.PROPOSED, error_message=message,
    )
    await session.commit()


async def create_part_jobs(session: AsyncSession, batch_id: str, settings: Settings) -> list[str]:
    """parts_written=true, fehlende Teil-Jobs anlegen (idempotent ueber den
    Unique-Index (split_batch_id, split_part_index)), Stapel-Job -> SPLIT.
    Committet. Returns: IDs aller Teil-Jobs in Teil-Reihenfolge."""
    batch = await get_batch(session, batch_id)
    final = load_final_parts(batch)
    parts = final.get("parts", [])
    source_job = await _source_job(session, batch)
    page_ocr = {
        entry.get("page"): entry
        for entry in _load_json(source_job.page_ocr if source_job else None, [])
        if isinstance(entry, dict)
    }
    existing = {job.split_part_index: job for job in await part_jobs(session, batch_id)}
    email_account_id = await _live_email_account_id(session, batch.email_account_id)

    created: list[ProcessingJob] = []
    for part in parts:
        index = int(part["index"])
        if index in existing:
            continue
        pages = [int(p) for p in part["pages"]]
        path = part_path(settings, batch_id, index)
        name = part_filename(batch.original_filename, index, len(parts))
        part_ocr = []
        for local, page in enumerate(pages, start=1):
            entry = page_ocr.get(page)
            if entry is not None:
                part_ocr.append({**entry, "page": local, "src_page": page})
        job = ProcessingJob(
            original_filename=name,
            stored_filename=generate_stored_filename(name),
            file_path=str(path),
            file_type="pdf",
            file_size_bytes=path.stat().st_size if path.exists() else 0,
            source=batch.job_source,
            status=JobStatus.PENDING,
            email_account_id=email_account_id,
            split_batch_id=batch_id,
            split_role=SplitRole.PART.value,
            split_part_index=index,
            page_from=min(pages),
            page_to=max(pages),
            split_mode=SplitMode.OFF.value,
            # Nur wenn JEDE Seite erkannt wurde — sonst (Zeitbudget der
            # Erkennung erschoepft) liest die normale OCR den Teil.
            page_ocr=json.dumps(part_ocr, ensure_ascii=False) if _complete(part_ocr, pages) else None,
        )
        session.add(job)
        created.append(job)

    final["parts_written"] = True
    batch.final_parts = json.dumps(final)
    if source_job is not None and source_job.status != JobStatus.SPLIT:
        source_job.status = JobStatus.SPLIT
        # AUTO: der Seitentext bleibt bis zum Ende der Aufbewahrungsfrist
        # (cleanup_expired) am Stapel-Job — nach "Aufteilung rückgängig" und
        # erneutem Bestaetigen wird so keine Seite ein zweites Mal erkannt.
        # MANUAL braucht ihn nicht (rückgängig = Original wieder aktiv).
        if batch.origin == SplitOrigin.MANUAL:
            source_job.page_ocr = None
        source_job.processing_started_at = None
        source_job.error_code = None
        source_job.error_message = None
    await session.commit()

    by_index = {**existing, **{job.split_part_index: job for job in created}}
    return [by_index[i].id for i in sorted(by_index)]


async def keep_whole(session: AsyncSession, batch_id: str, *, version: int, settings: Settings) -> None:
    """"Als ein Dokument behalten".

    AUTO: Stapel KEPT_WHOLE, Job zurueck auf PENDING (split_mode OFF) — er
    laeuft mit dem schon erkannten Seitentext durch die normale Pipeline.
    MANUAL: das Dokument existiert bereits — Stapel KEPT_WHOLE, Stapel-Job
    und Kopie werden entfernt, das Original bleibt unveraendert.
    """
    batch = await get_batch(session, batch_id)
    if batch.status != SplitBatchStatus.PROPOSED or batch.version != version:
        raise SplitConflictError()
    await transition(
        session, batch_id,
        expected=SplitBatchStatus.PROPOSED, new=SplitBatchStatus.KEPT_WHOLE, version=version,
        finished_at=_now(),
    )
    job = await _source_job(session, batch)
    if batch.origin == SplitOrigin.MANUAL:
        if job is not None:
            await session.delete(job)
        await session.commit()
        await asyncio.to_thread(remove_batch_files, settings, batch_id)
        return
    if job is not None:
        job.status = JobStatus.PENDING
        job.split_mode = SplitMode.OFF.value
        job.processing_started_at = None
        job.error_message = None
        job.error_code = None
        job.retry_count = 0
    await session.commit()


async def discard(session: AsyncSession, batch_id: str, *, version: int, settings: Settings) -> None:
    """Verwerfen: Stapel DISCARDED, Stapel-Job + Quelle + Vorschaubilder weg.
    Bei MANUAL bleibt das Originaldokument unveraendert."""
    batch = await get_batch(session, batch_id)
    if batch.status != SplitBatchStatus.PROPOSED or batch.version != version:
        raise SplitConflictError()
    await transition(
        session, batch_id,
        expected=SplitBatchStatus.PROPOSED, new=SplitBatchStatus.DISCARDED, version=version,
        finished_at=_now(),
    )
    job = await _source_job(session, batch)
    if job is not None:
        await session.delete(job)
    await session.commit()
    await asyncio.to_thread(remove_batch_files, settings, batch_id)


async def retry_failed_parts(session: AsyncSession, batch_id: str) -> list[str]:
    """Fehlgeschlagene Teil-Jobs (keine Duplikate) neu einreihen; Stapel
    DONE_WITH_ERRORS -> SPLITTING. Returns: IDs der neu eingereihten Jobs."""
    batch = await get_batch(session, batch_id)
    if batch.status != SplitBatchStatus.DONE_WITH_ERRORS:
        raise SplitConflictError("Dieser Stapel hat keine fehlgeschlagenen Teile.")
    retry = [
        job for job in await part_jobs(session, batch_id)
        if job.status == JobStatus.FAILED and job.error_code != JOB_ERROR_DUPLICATE
    ]
    missing = [job for job in retry if not Path(job.file_path).is_file()]
    if not retry or missing:
        raise SplitConflictError("Die fehlgeschlagenen Teile können nicht erneut verarbeitet werden.")
    await transition(
        session, batch_id,
        expected=SplitBatchStatus.DONE_WITH_ERRORS, new=SplitBatchStatus.SPLITTING, finished_at=None,
    )
    for job in retry:
        job.status = JobStatus.PENDING
        job.retry_count = 0
        job.error_message = None
        job.error_code = None
        job.processing_started_at = None
    await session.commit()
    return [job.id for job in retry]


# ---------------------------------------------------------------------------
# Rueckgaengig
# ---------------------------------------------------------------------------

_UNDO_STATUSES = (SplitBatchStatus.DONE, SplitBatchStatus.DONE_WITH_ERRORS)


async def active_part_documents(session: AsyncSession, batch_id: str) -> list[Document]:
    """Nicht geloeschte Teildokumente eines Stapels."""
    return list((await session.execute(
        select(Document)
        .where(Document.split_batch_id == batch_id, Document.status != DocumentStatus.DELETED)
        .order_by(Document.split_part_index)
    )).scalars().all())


async def _split_audit_status(session: AsyncSession, document_id: str, batch_id: str) -> str | None:
    """Status des Originals vor der Aufteilung (aus dem AuditLog-Eintrag SPLIT
    dieses Stapels) — None, wenn das Original nicht durch ihn geloescht wurde."""
    rows = (await session.execute(
        select(AuditLog.details)
        .where(AuditLog.document_id == document_id, AuditLog.action == AuditAction.SPLIT)
        .order_by(AuditLog.id.desc())
    )).scalars().all()
    for details in rows:
        data = _load_json(details, {})
        if data.get("split_batch_id") == batch_id:
            return str(data.get("previous_status") or DocumentStatus.ACTIVE.value)
    return None


async def undo_blocker(session: AsyncSession, batch: SplitBatch) -> str | None:
    """Warum "Aufteilung rückgängig" nicht moeglich ist (deutscher Text) — None,
    wenn es geht.

    Gesperrt, sobald ein Teil vom Benutzer bearbeitet wurde (AuditLog UPDATED,
    TAG_ADDED/REMOVED, REVIEWED an einem Teil): diese Arbeit ginge sonst
    verloren. AUTO braucht die noch aufbewahrte Stapeldatei (sie wird wieder
    zum Vorschlag), MANUAL das Originaldokument.
    """
    if batch.status not in _UNDO_STATUSES:
        return "Nur eine abgeschlossene Aufteilung kann rückgängig gemacht werden."
    # DONE_WITH_ERRORS: ein Teil kann ueber POST /api/jobs/{id}/retry wieder
    # PENDING/PROCESSING sein — undo loeschte sonst den laufenden Teil-Job.
    if any(job.status not in _PART_TERMINAL for job in await part_jobs(session, batch.id)):
        return "Ein Teil wird gerade verarbeitet – bitte warten, bis er fertig ist."
    parts = await active_part_documents(session, batch.id)
    if parts:
        edited = (await session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.document_id.in_([d.id for d in parts]),
                AuditLog.action.in_(_USER_EDIT_ACTIONS),
            )
        )).scalar_one()
        if edited:
            return (
                "Ein Teil wurde inzwischen bearbeitet – die Aufteilung kann nicht mehr "
                "rückgängig gemacht werden."
            )
    if batch.origin == SplitOrigin.MANUAL:
        original = (
            await session.get(Document, batch.source_document_id) if batch.source_document_id else None
        )
        if original is None:
            return "Das Originaldokument ist nicht mehr vorhanden."
        if original.status == DocumentStatus.DELETED:
            if await _split_audit_status(session, original.id, batch.id) is None:
                return "Das Originaldokument wurde inzwischen gelöscht."
            duplicate = await find_active_duplicate(original.file_hash, session)
            if duplicate is not None and duplicate.id != original.id:
                return "Das Originaldokument wurde inzwischen erneut archiviert."
        return None
    if not Path(batch.file_path).is_file():
        return (
            "Die Stapeldatei wird nicht mehr aufbewahrt – die Aufteilung kann nicht mehr "
            "rückgängig gemacht werden."
        )
    return None


def _proposal_from_final(proposal: dict, final: dict) -> dict:
    """"Letzte Aufteilung als Vorschlag": Schnitte genau dort, wo zuletzt
    geschnitten wurde; alle anderen Uebergaenge "kein Schnitt"."""
    parts = final.get("parts") or []
    cut_after = {max(int(p) for p in part["pages"]) for part in parts[:-1] if part.get("pages")}
    for boundary in proposal.get("boundaries") or []:
        page = boundary.get("after_page")
        boundary["decision"] = "cut" if page in cut_after else "none"
        if page in cut_after:
            boundary["reasons"] = [*boundary.get("reasons", []), "Bei der letzten Aufteilung geschnitten"]
    return proposal


@dataclass(frozen=True)
class UndoResult:
    removed_document_ids: list[str]
    restored_document_id: str | None


async def undo(session: AsyncSession, batch_id: str, *, version: int, settings: Settings) -> UndoResult:
    """"Aufteilung rückgängig" (DONE / DONE_WITH_ERRORS).

    Teildokumente werden soft-deleted (AuditLog SPLIT_UNDONE), die Teil-Jobs
    entfernt (sonst blockierte der Unique-Index eine erneute Bestaetigung).
    MANUAL: Original wieder aktiv (vorheriger Status), Stapel UNDONE, Kopie
    und Vorschaubilder weg. AUTO: Stapel wieder PROPOSED mit der letzten
    Aufteilung als Vorschlag, Stapel-Job wieder AWAITING_SPLIT. Die Teile
    werden bei erneuter Bestaetigung neu erkannt (ihr Seitentext wurde nach
    der Archivierung verworfen).

    Committet. Der Aufrufer entfernt danach die Vektoren der Teile und
    vektorisiert das Original neu (``finish_undo``).

    Raises: SplitConflictError (409) mit Begruendung.
    """
    batch = await get_batch(session, batch_id)
    if batch.version != version:
        raise SplitConflictError()
    reason = await undo_blocker(session, batch)
    if reason:
        raise SplitConflictError(reason)
    from app.services.search_service import reindex_document_fts, remove_document_from_index

    current = SplitBatchStatus(batch.status)
    parts = await active_part_documents(session, batch_id)
    for document in parts:
        document.status = DocumentStatus.DELETED
        session.add(AuditLog(
            document_id=document.id,
            action=AuditAction.SPLIT_UNDONE,
            details=json.dumps({"split_batch_id": batch_id}),
        ))
        await remove_document_from_index(session, document.id)
    for job in await part_jobs(session, batch_id):
        await session.delete(job)
    source_job = await _source_job(session, batch)

    restored_id = None
    if batch.origin == SplitOrigin.MANUAL:
        original = await session.get(Document, batch.source_document_id)
        if original is not None and original.status == DocumentStatus.DELETED:
            previous = await _split_audit_status(session, original.id, batch_id)
            restored = DocumentStatus(previous or DocumentStatus.ACTIVE.value)
            original.status = DocumentStatus.ACTIVE if restored == DocumentStatus.DELETED else restored
            restored_id = original.id
        if original is not None:
            session.add(AuditLog(
                document_id=original.id,
                action=AuditAction.SPLIT_UNDONE,
                details=json.dumps(
                    {"split_batch_id": batch_id, "parts": [d.id for d in parts]}, ensure_ascii=False,
                ),
            ))
        if source_job is not None:
            await session.delete(source_job)
        await session.flush()
        await transition(
            session, batch_id, expected=current, new=SplitBatchStatus.UNDONE, version=version,
            finished_at=_now(),
        )
        if restored_id:
            await reindex_document_fts(session, restored_id)
    else:
        final = load_final_parts(batch)
        proposal = _proposal_from_final(load_proposal(batch), final)
        final["parts_written"] = False
        await session.flush()
        await transition(
            session, batch_id, expected=current, new=SplitBatchStatus.PROPOSED, version=version,
            proposal=json.dumps(proposal), final_parts=json.dumps(final),
            confirmed_at=None, finished_at=None, error_message=None,
        )
        if source_job is None:
            # Datensatz wurde entfernt (DELETE /api/jobs bei SPLIT): neu anlegen,
            # sonst haette der Vorschlag keinen Job fuer "Als ein Dokument behalten".
            source = Path(batch.file_path)
            source_job = ProcessingJob(
                original_filename=batch.original_filename,
                stored_filename=generate_stored_filename(batch.original_filename),
                file_path=str(source),
                file_type="pdf",
                file_size_bytes=source.stat().st_size,
                source=batch.job_source,
                email_account_id=await _live_email_account_id(session, batch.email_account_id),
                status=JobStatus.AWAITING_SPLIT,
                split_batch_id=batch_id,
                split_role=SplitRole.SOURCE.value,
                split_mode=SplitMode.AUTO.value,
            )
            session.add(source_job)
            await session.flush()
            await session.execute(
                update(SplitBatch).where(SplitBatch.id == batch_id)
                .values(source_job_id=source_job.id)
                .execution_options(synchronize_session=False)
            )
        source_job.status = JobStatus.AWAITING_SPLIT
        source_job.document_id = None
        source_job.error_code = None
        source_job.error_message = None
        source_job.processing_started_at = None
    await session.commit()

    if batch.origin == SplitOrigin.MANUAL:
        await asyncio.to_thread(remove_batch_files, settings, batch_id)
    else:
        await asyncio.to_thread(_remove_part_files, settings, batch_id)
    logger.info(
        "Aufteilung von Stapel %s rueckgaengig gemacht (%d Teil(e) entfernt)", batch_id, len(parts),
    )
    return UndoResult(removed_document_ids=[d.id for d in parts], restored_document_id=restored_id)


async def finish_undo(result: UndoResult, settings: Settings, session_factory) -> None:
    """Nach dem Commit (best-effort): Vektoren der Teile entfernen, Original neu
    vektorisieren."""
    for document_id in result.removed_document_ids:
        await delete_original_vectors(document_id, settings)
    if result.restored_document_id:
        try:
            from app.services.vectorize_service import revectorize_document_safe

            await revectorize_document_safe(result.restored_document_id, settings, session_factory)
        except Exception:
            logger.warning(
                "Original %s konnte nicht neu vektorisiert werden", result.restored_document_id, exc_info=True,
            )


# ---------------------------------------------------------------------------
# Aufbewahrung (SPLIT_KEEP_SOURCE_DAYS)
# ---------------------------------------------------------------------------


def _still_needed(job: ProcessingJob | None) -> bool:
    """Braucht der Stapel-Job seine Datei unter data/split/ noch? (Wartet,
    laeuft, oder ist fehlgeschlagen und kann erneut versucht werden.)"""
    if job is None:
        return False
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.AWAITING_SPLIT):
        return True
    return job.status == JobStatus.FAILED and job.source != JobSource.SPLIT_REQUEST


async def cleanup_expired(session: AsyncSession, settings: Settings, *, now: datetime | None = None) -> int:
    """Taeglicher Wartungslauf: Stapeldatei, Teil-Reste und Vorschaubilder
    abgeschlossener Stapel nach SPLIT_KEEP_SOURCE_DAYS loeschen (DONE,
    KEPT_WHOLE, FAILED; DISCARDED/UNDONE raeumen sofort auf, hier nur Reste).

    Dabei wird auch der aufbewahrte Seitentext am Stapel-Job verworfen.
    Stapel in DETECTING/PROPOSED/SPLITTING (geschuetzte Quellen) und
    DONE_WITH_ERRORS (fehlgeschlagene Teile brauchen ihre Datei fuer
    "Wiederholen") werden nie angefasst, ebenso Dateien, die ein Stapel-Job
    noch braucht. Danach ist "rückgängig" fuer AUTO-Stapel nicht mehr
    moeglich. Zum Schluss werden verwaiste Stapel-Ordner entfernt
    (``remove_orphan_batch_dirs``). Returns: Anzahl aufgeraeumter Stapel
    (ohne verwaiste Ordner).
    """
    cutoff = (now or _now()) - timedelta(days=settings.SPLIT_KEEP_SOURCE_DAYS)
    batches = (await session.execute(
        select(SplitBatch).where(
            SplitBatch.status.in_(_CLEANUP_STATUSES),
            SplitBatch.finished_at.is_not(None),
            SplitBatch.finished_at < cutoff,
        )
    )).scalars().all()
    cleaned = 0
    for batch in batches:
        job = await _source_job(session, batch)
        if _still_needed(job):
            continue
        has_text = job is not None and job.page_ocr is not None
        if not has_text and not any(d.exists() for d in _safe_batch_dirs(settings, batch.id)):
            continue
        # Atomar beanspruchen (Status unveraendert, version+1), bevor Dateien
        # verschwinden: ein gleichzeitiges "rückgängig" (prueft die Version)
        # scheitert dann mit 409 — oder hat den Stapel schon geaendert, dann
        # wird hier nichts geloescht.
        claimed = await session.execute(
            update(SplitBatch)
            .where(SplitBatch.id == batch.id, SplitBatch.status == batch.status, SplitBatch.version == batch.version)
            .values(version=SplitBatch.version + 1)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount == 0:
            continue  # inzwischen geaendert (0 Zeilen: nichts zurueckzurollen)
        if has_text:
            # Seitentext fuer ein erneutes Aufteilen nach "rückgängig" — ohne
            # Stapeldatei nutzlos, und keine dauerhafte zweite Textkopie.
            job.page_ocr = None
        await session.commit()
        await asyncio.to_thread(remove_batch_files, settings, batch.id)
        cleaned += 1
    if cleaned:
        logger.info("Aufbewahrungsfrist abgelaufen: Dateien von %d Stapel(n) entfernt", cleaned)
    await remove_orphan_batch_dirs(session, settings, now=now)
    return cleaned


# Mindestalter verwaister Stapel-Ordner: create_batch/request_document_split
# kopieren die Quelle VOR dem Commit — ein juengerer Ordner ohne Zeile kann
# gerade entstehen.
_ORPHAN_MIN_AGE = timedelta(days=1)


def _orphan_dirs(settings: Settings, keep: set[str], cutoff: float) -> list[Path]:
    out: list[Path] = []
    for root in (settings.split_dir, settings.split_thumbnail_dir):
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if (
                entry.is_dir()
                and not entry.is_symlink()
                and _UUID_RE.match(entry.name)
                and entry.name not in keep
                and entry.stat().st_mtime < cutoff
            ):
                out.append(entry)
    return out


async def remove_orphan_batch_dirs(
    session: AsyncSession, settings: Settings, *, now: datetime | None = None,
) -> int:
    """Ordner unter ``data/split/`` bzw. ``THUMBNAIL_DIR/split/`` ohne
    Stapel-Zeile entfernen (Absturz zwischen Kopieren und Commit, oder
    zwischen dem Loeschen einer Stapel-Zeile und ihrer Vorschaubilder).

    Nie angefasst: Ordner bestehender Stapel, Ordner, auf die noch ein Job
    zeigt (AUTO "ein Dokument": Stapel-Zeile geloescht, die Datei liegt bis
    zur Archivierung unter ``data/split/<id>/``), und Ordner juenger als
    ``_ORPHAN_MIN_AGE``. Returns: Anzahl entfernter Ordner."""
    keep = set((await session.execute(select(SplitBatch.id))).scalars().all())
    # Ueber den Ordnernamen, nicht den vollen Pfad: gespeicherte Pfade sind
    # absolut und koennen auf einen frueheren Datenordner zeigen (verschoben,
    # Docker -> Native). Lieber einen Ordner zu viel behalten als loeschen.
    for file_path in (await session.execute(
        select(ProcessingJob.file_path).where(ProcessingJob.split_batch_id.is_(None))
    )).scalars().all():
        parent_name = PureWindowsPath(file_path).parent.name  # trennt / und \
        if _UUID_RE.match(parent_name):
            keep.add(parent_name)
    cutoff = ((now or _now()) - _ORPHAN_MIN_AGE).timestamp()
    orphans = await asyncio.to_thread(_orphan_dirs, settings, keep, cutoff)
    for directory in orphans:
        await asyncio.to_thread(shutil.rmtree, directory, True)
    if orphans:
        logger.warning("%d verwaiste(r) Stapel-Ordner entfernt", len(orphans))
    return len(orphans)


async def run_split_cleanup(session_factory, settings: Settings) -> None:
    """Background-Task: einmal beim Start, danach taeglich ``cleanup_expired``."""
    while True:
        try:
            async with session_factory() as session:
                await cleanup_expired(session, settings)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Fehler beim Aufraeumen abgeschlossener Stapel")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Worker-Hooks
# ---------------------------------------------------------------------------


async def manual_tag_names(session: AsyncSession, document_id: str) -> list[str]:
    """Vom Benutzer vergebene Tags eines Dokuments: selbst angelegte Tags
    (is_auto_generated=False) und Tags mit einem TAG_ADDED-Eintrag."""
    tags = (await session.execute(
        select(Tag.name, Tag.is_auto_generated)
        .join(DocumentTag, DocumentTag.tag_id == Tag.id)
        .where(DocumentTag.document_id == document_id)
    )).all()
    added: set[str] = set()
    for (details,) in (await session.execute(
        select(AuditLog.details).where(
            AuditLog.document_id == document_id, AuditLog.action == AuditAction.TAG_ADDED,
        )
    )).all():
        tag = _load_json(details, {}).get("tag") if details else None
        if isinstance(tag, str):
            added.add(tag.strip().lower())
    return sorted(name for name, auto in tags if not auto or name in added)


@dataclass(frozen=True)
class PartContext:
    provenance: SplitProvenance
    forced_scope_id: str | None


async def part_context(session: AsyncSession, job: ProcessingJob) -> PartContext | None:
    """Herkunft + Uebernahmen fuer die Archivierung eines Teil-Jobs.

    MANUAL: Ablagebereich und manuelle Tags des Originals (Owner-Entscheidung
    3). Fehlt der Stapel oder das Original, wird ohne Uebernahme archiviert.
    """
    if job.split_role != SplitRole.PART.value or not job.split_batch_id:
        return None
    batch = await session.get(SplitBatch, job.split_batch_id)
    if batch is None:
        return None
    parts = load_final_parts(batch).get("parts", [])
    part = next((p for p in parts if p.get("index") == job.split_part_index), None)
    pages = [int(p) for p in part["pages"]] if part else list(
        range(job.page_from or 1, (job.page_to or job.page_from or 1) + 1)
    )
    scope_id = None
    tags: list[str] = []
    if batch.origin == SplitOrigin.MANUAL and batch.source_document_id:
        original = await session.get(Document, batch.source_document_id)
        if original is not None:
            scope_id = original.filing_scope_id
            tags = await manual_tag_names(session, original.id)
    return PartContext(
        provenance=SplitProvenance(
            batch_id=batch.id,
            part_index=job.split_part_index or 1,
            part_count=len(parts) or 1,
            source_pages=format_page_ranges(pages),
            source_filename=batch.original_filename,
            inherited_tags=tuple(tags),
        ),
        forced_scope_id=scope_id,
    )


async def on_part_finished(session: AsyncSession, batch_id: str | None) -> str | None:
    """Nach jedem terminalen Teil-Job: sind alle Teile fertig, wird der Stapel
    DONE (alle archiviert oder Duplikat) bzw. DONE_WITH_ERRORS. Bei MANUAL +
    DONE wird das Original soft-deleted (AuditAction.SPLIT mit den Teil-IDs).

    Auch aus DONE_WITH_ERRORS: ein ueber die Jobliste wiederholter Teil
    schliesst den Stapel ab. Ein entfernter Teil-Job (DELETE /api/jobs) zaehlt
    als Fehler — seine Seiten fehlen im Archiv, das Original bleibt aktiv.

    Committet NICHT (Aufrufer). Returns: ID des soft-geloeschten Originals
    (der Aufrufer entfernt dessen Vektoren nach dem Commit) oder None.
    """
    if not batch_id:
        return None
    batch = await session.get(SplitBatch, batch_id, populate_existing=True)
    current = SplitBatchStatus(batch.status) if batch is not None else None
    if current not in (SplitBatchStatus.SPLITTING, SplitBatchStatus.DONE_WITH_ERRORS):
        return None
    expected = len(load_final_parts(batch).get("parts", []))
    jobs = await part_jobs(session, batch_id)
    if any(job.status not in _PART_TERMINAL for job in jobs):
        return None
    failed = [j for j in jobs if j.status == JobStatus.FAILED and j.error_code != JOB_ERROR_DUPLICATE]
    # Teil-Jobs entstehen in EINER Transaktion mit parts_written — fehlt einer,
    # wurde er entfernt.
    missing = len(jobs) < expected
    new_status = SplitBatchStatus.DONE_WITH_ERRORS if failed or missing else SplitBatchStatus.DONE
    if new_status == current:
        return None
    await transition(session, batch_id, expected=current, new=new_status, finished_at=_now())
    logger.info("Stapel %s abgeschlossen: %s", batch_id, new_status.value)
    if new_status != SplitBatchStatus.DONE or batch.origin != SplitOrigin.MANUAL:
        return None
    original = (
        await session.get(Document, batch.source_document_id) if batch.source_document_id else None
    )
    if original is None or original.status == DocumentStatus.DELETED:
        return None
    previous_status = str(getattr(original.status, "value", original.status))
    original.status = DocumentStatus.DELETED
    session.add(AuditLog(
        document_id=original.id,
        action=AuditAction.SPLIT,
        details=json.dumps(
            {
                "split_batch_id": batch_id,
                "parts": [job.document_id for job in jobs if job.document_id],
                # "Aufteilung rueckgaengig" stellt diesen Status wieder her
                "previous_status": previous_status,
            },
            ensure_ascii=False,
        ),
    ))
    from app.services.search_service import remove_document_from_index

    await remove_document_from_index(session, original.id)
    return original.id


async def on_job_failed(session: AsyncSession, job: ProcessingJob) -> str | None:
    """Ein Job ist endgueltig FAILED: Teil -> on_part_finished; Stapel-Job in
    der Erkennung -> Stapel FAILED. Committet NICHT."""
    if job.split_role == SplitRole.PART.value:
        return await on_part_finished(session, job.split_batch_id)
    if job.split_role == SplitRole.SOURCE.value and job.split_batch_id:
        try:
            await transition(
                session, job.split_batch_id,
                expected=SplitBatchStatus.DETECTING, new=SplitBatchStatus.FAILED,
                error_message=job.error_message, finished_at=_now(),
            )
        except SplitConflictError:
            pass
    return None


async def delete_original_vectors(document_id: str | None, settings: Settings) -> None:
    """Vektoren eines per Aufteilung soft-geloeschten Originals (best-effort,
    nach dem Commit)."""
    if not document_id:
        return
    try:
        from app.services.vectorize_service import delete_document_vectors

        await delete_document_vectors(document_id, settings)
    except Exception:
        logger.warning("Vektoren konnten nicht geloescht werden fuer %s", document_id, exc_info=True)


async def find_split_duplicate(
    session: AsyncSession, file_hash: str, *, exclude_batch_id: str | None,
) -> SplitBatch | None:
    """Stapel mit diesem Hash, der schon aufgeteilt ist bzw. auf die
    Aufteilung wartet (der eigene Stapel zaehlt nicht)."""
    query = select(SplitBatch).where(
        SplitBatch.file_hash == file_hash,
        SplitBatch.status.in_(DUPLICATE_BATCH_STATUSES),
    )
    if exclude_batch_id:
        query = query.where(SplitBatch.id != exclude_batch_id)
    return (await session.execute(query.limit(1))).scalars().first()


def split_duplicate_message(batch: SplitBatch) -> str:
    if batch.status == SplitBatchStatus.PROPOSED:
        return f"Dieser Stapel („{batch.original_filename}“) wartet bereits auf die Aufteilung."
    return f"Dieser Stapel („{batch.original_filename}“) wurde bereits aufgeteilt."


async def protected_source_paths(session: AsyncSession) -> set[str]:
    """Dateien aktiver Stapel (DETECTING/PROPOSED/SPLITTING) — Aufraeumer
    (Duplikat-Bereinigung, Job-Verwerfen) ueberspringen sie."""
    rows = (await session.execute(
        select(SplitBatch.file_path).where(SplitBatch.status.in_(ACTIVE_BATCH_STATUSES))
    )).scalars().all()
    return {str(Path(p).resolve()) for p in rows}


def is_protected(path: str | Path, protected: set[str]) -> bool:
    return str(Path(path).resolve()) in protected


# ---------------------------------------------------------------------------
# Recovery beim Start
# ---------------------------------------------------------------------------


async def recover_interrupted_batches(session: AsyncSession, settings: Settings) -> int:
    """Nach ``recover_interrupted_jobs`` aufrufen (PROCESSING ist dann PENDING).

    - DETECTING ohne PENDING/PROCESSING-Stapel-Job -> FAILED
    - SPLITTING ohne parts_written -> PROPOSED, liegengebliebene Teildateien weg
    - SPLITTING mit parts_written -> fehlende Teil-Jobs anlegen; sind alle
      Teile schon fertig, den Stapel abschliessen
    Returns: Anzahl behandelter Stapel.
    """
    handled = 0
    batches = (await session.execute(
        select(SplitBatch).where(
            SplitBatch.status.in_((SplitBatchStatus.DETECTING, SplitBatchStatus.SPLITTING))
        )
    )).scalars().all()
    for batch in batches:
        if batch.status == SplitBatchStatus.DETECTING:
            job = await _source_job(session, batch)
            if job is not None and job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
                continue
            await transition(
                session, batch.id, expected=SplitBatchStatus.DETECTING, new=SplitBatchStatus.FAILED,
                error_message="Die Erkennung wurde abgebrochen.", finished_at=_now(),
            )
            await session.commit()
        elif not load_final_parts(batch).get("parts_written"):
            await asyncio.to_thread(_remove_part_files, settings, batch.id)
            await transition(
                session, batch.id, expected=SplitBatchStatus.SPLITTING, new=SplitBatchStatus.PROPOSED,
                error_message="Die Aufteilung wurde durch einen Neustart unterbrochen.",
            )
            await session.commit()
        else:
            await create_part_jobs(session, batch.id, settings)
            original_id = await on_part_finished(session, batch.id)
            await session.commit()
            await delete_original_vectors(original_id, settings)
        handled += 1
    if handled:
        logger.warning("%d unterbrochene(r) Stapel nach Neustart behandelt", handled)
    return handled

