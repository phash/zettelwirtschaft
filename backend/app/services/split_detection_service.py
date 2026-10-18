"""Aufteilungs-Stufe im Queue-Worker (Spezifikation 12).

Laeuft zwischen Duplikat-Check und Thumbnail/Analyse — immer im einzigen
Queue-Worker (kein zweiter OCR-Pfad parallel, Heartbeat/Retry/Pause greifen).

Geeignet sind PDF-Jobs ohne vorab gesetzten Text (kein E-Mail-Body), die
kein Teil-Job sind:

- FORCE (Upload-Option "vor der Analyse aufteilen", "Dokument aufteilen"):
  ab 2 Seiten, der Stapel wartet immer im Editor (AWAITING_SPLIT).
- AUTO (Upload, Watch-Ordner, E-Mail-Anhang): wenn die automatische Pruefung
  aktiv ist und das PDF mindestens ``split_min_pages`` Seiten hat. Findet die
  Heuristik keinen Schnitt und keinen unsicheren Uebergang, wird der Stapel
  wieder geloescht und der Job laeuft mit dem schon erkannten Seitentext
  normal weiter; sonst wartet er im Editor ("im Zweifel Pruefung").

Pro Seite: Geometrie, Tinte/Leerseite, Text (digital bzw. OCR 300 dpi,
Leerseiten ohne OCR), Randcode, Vorschaubilder und die Merkmale fuer die
Heuristik (``split_heuristics``, kein Volltext am Stapel). Der Seitentext
landet in ``ProcessingJob.page_ocr`` und wird fuer die Teile
wiederverwendet — keine Seite wird zweimal per OCR gelesen.

Danach optional ein KI-Pass (``split_llm_service``, hoechstens ein
LLM-Aufruf je Stapel) fuer unsichere Uebergaenge; faellt er aus, bleibt das
heuristische Ergebnis.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.file_utils import is_pdf_type
from app.models.processing_job import JobSource, JobStatus, ProcessingJob, SplitMode, SplitRole
from app.models.split_batch import SplitBatch, SplitBatchStatus, SplitOrigin
from app.services import ocr_service, pdf_split_service, split_llm_service, split_service
from app.services.notification_service import notify_split_needed, notify_system
from app.services.ocr_service import PageText
from app.services.pdf_split_service import PageGeom, PdfSplitError
from app.services.split_heuristics import (  # noqa: F401 — re-exportiert (Spezifikation)
    boilerplate_lines,
    detect_address_block,
    detect_duplex,
    extract_features,
    parse_counter,
    parse_margin_code,
    parse_refs,
    score_boundaries,
)
from app.services.split_llm_service import llm_refine  # noqa: F401 — re-exportiert (Spezifikation)

logger = logging.getLogger(__name__)

# h1: Heuristik (Merkmale + Scoring, split_weights). Beim Aendern von Gewichten
# oder Merkmalen hochzaehlen — gespeicherte Vorschlaege bleiben auswertbar.
DETECTOR_VERSION = "h1"
CHECKPOINT_EVERY = 5
_THUMB_DPI = 50
_PREVIEW_DPI = 100
_PREVIEW_WIDTH = 1000
_CANNOT_SPLIT = "Datei kann nicht aufgeteilt werden"
# Randcode-OCR nur, wenn im linken Randstreifen ueberhaupt etwas steht.
_MARGIN_MIN_INK = 0.005
# Senkrechter Randcode: zuerst von unten nach oben gelesen (270°), sonst 90°.
_MARGIN_ANGLES = (270, 90)


class SplitNotPossibleError(Exception):
    """"Dokument aufteilen" ist fuer diese Datei nicht moeglich (nicht wiederholbar)."""


@dataclass
class SplitDecision:
    # SKIP: normale Pipeline (ggf. mit ``pages``), AWAITING: Stapel wartet im
    # Editor, PAUSED: Queue pausiert — Job zurueck auf PENDING, Erkennung setzt
    # am Checkpoint fort.
    kind: Literal["SKIP", "AWAITING", "PAUSED"]
    batch_id: str | None = None
    pages: tuple[list[PageText], int] | None = None


def _pages_from_entries(entries) -> tuple[list[PageText], int] | None:
    """``page_ocr``-Eintraege -> (Seiten mit Text, Seitenzahl). None, wenn
    leer oder eine Seite ohne Texterkennung (Zeitbudget) dabei ist."""
    if not isinstance(entries, list) or not entries:
        return None
    if any(isinstance(entry, dict) and entry.get("source") == "skipped" for entry in entries):
        return None  # Zeitbudget: Seiten ohne Texterkennung -> normale OCR
    pages = [
        PageText(
            page_number=int(entry.get("page", index)),
            text=str(entry.get("text") or ""),
            confidence=float(entry.get("confidence") or 0.0),
        )
        for index, entry in enumerate(entries, start=1)
        if isinstance(entry, dict) and (entry.get("text") or "").strip()
    ]
    return pages, len(entries)


def load_page_ocr(job: ProcessingJob) -> tuple[list[PageText], int] | None:
    """Seitentext aus ``job.page_ocr`` (Teil-Jobs, "Als ein Dokument behalten")."""
    if not job.page_ocr:
        return None
    try:
        entries = json.loads(job.page_ocr)
    except (TypeError, ValueError):
        logger.warning("page_ocr von Job %s nicht lesbar — normale OCR", job.id)
        return None
    return _pages_from_entries(entries)


async def reusable_page_ocr(
    job: ProcessingJob, session: AsyncSession,
) -> tuple[list[PageText], int] | None:
    """Seitentext fuer die normale Pipeline — nur wenn er ALLE Seiten abdeckt:
    Teil-Jobs (``create_part_jobs`` setzt ihn nur vollstaendig) und "Als ein
    Dokument behalten" (Stapel KEPT_WHOLE). Der Checkpoint einer abgebrochenen
    Erkennung (Stapel FAILED) enthaelt nur die ersten Seiten -> normale OCR."""
    if job.split_role == SplitRole.PART.value:
        return load_page_ocr(job)
    if job.split_role != SplitRole.SOURCE.value or not job.split_batch_id:
        return None
    batch = await session.get(SplitBatch, job.split_batch_id)
    if batch is None or batch.status != SplitBatchStatus.KEPT_WHOLE:
        return None
    pages = load_page_ocr(job)
    if pages is None or pages[1] != batch.page_count:
        return None
    return pages


def _is_candidate(job: ProcessingJob) -> bool:
    return (
        is_pdf_type(job.file_type)
        and job.split_role != SplitRole.PART.value
        and job.split_mode != SplitMode.OFF.value
        # E-Mail-Body-Jobs bringen ihren Text mit — nie aufteilen.
        and job.ocr_text is None
    )


async def maybe_detect(
    job: ProcessingJob,
    settings: Settings,
    session: AsyncSession,
    *,
    should_pause: Callable[[], bool] = lambda: False,
) -> SplitDecision:
    """Prueft, ob der Job in die Aufteilungs-Stufe gehoert, und fuehrt sie aus.

    Raises:
        SplitNotPossibleError: "Dokument aufteilen" auf einer Datei, die sich
            nicht aufteilen laesst (der Worker markiert den Job FAILED).
    """
    if not _is_candidate(job):
        return SplitDecision("SKIP")
    forced = job.split_mode == SplitMode.FORCE.value

    batch = (
        await session.get(SplitBatch, job.split_batch_id, populate_existing=True)
        if job.split_batch_id else None
    )
    if job.source == JobSource.SPLIT_REQUEST and (batch is None or batch.status != SplitBatchStatus.DETECTING):
        # "Dokument aufteilen" nach abgebrochener Erkennung (Stapel FAILED):
        # die Kopie des aktiven Originals nie als eigenes Dokument analysieren.
        raise SplitNotPossibleError(
            "Die Aufteilung wurde abgebrochen – bitte am Dokument erneut „Dokument aufteilen“ wählen."
        )
    if batch is not None and batch.status != SplitBatchStatus.DETECTING:
        return SplitDecision("SKIP")

    if batch is None:
        config = None
        if not forced:
            config = await split_service.load_split_settings(session, settings)
            if not config.auto_enabled:
                return SplitDecision("SKIP")
        try:
            count = await asyncio.to_thread(pdf_split_service.page_count, Path(job.file_path))
        except PdfSplitError as exc:
            logger.warning("Job %s: %s (%s)", job.id, _CANNOT_SPLIT, exc)
            if forced:
                await _give_up(session, job, f"„{job.original_filename}“: {_CANNOT_SPLIT}.")
            # AUTO: verschluesselt/defekt — laeuft wie bisher normal weiter
            return SplitDecision("SKIP")
        if config is not None and count < config.min_pages:
            return SplitDecision("SKIP")
        if count < 2:
            await _give_up(session, job, None)
            return SplitDecision("SKIP")
        if count > settings.SPLIT_MAX_PAGES:
            await _give_up(
                session, job,
                (
                    f"„{job.original_filename}“ hat {count} Seiten – zu viele für die "
                    + ("Aufteilung" if forced else "automatische Aufteilung")
                    + f" (höchstens {settings.SPLIT_MAX_PAGES}). Die Datei wird als ein Dokument verarbeitet."
                ),
                title=None if forced else "Zu viele Seiten für die automatische Aufteilung",
            )
            return SplitDecision("SKIP")
        batch = await split_service.create_batch(session, job, settings, page_count=count)

    try:
        return await run_detection(job, batch, settings, session, should_pause=should_pause)
    except PdfSplitError as exc:
        logger.warning("Erkennung fuer Stapel %s fehlgeschlagen: %s", batch.id, exc)
        await split_service.transition(
            session, batch.id, expected=SplitBatchStatus.DETECTING, new=SplitBatchStatus.FAILED,
            error_message=_CANNOT_SPLIT, finished_at=datetime.now(timezone.utc),
        )
        if batch.origin == SplitOrigin.MANUAL:
            await session.commit()
            raise SplitNotPossibleError(f"{_CANNOT_SPLIT}.") from exc
        await _give_up(
            session, job,
            f"„{job.original_filename}“: {_CANNOT_SPLIT}." if job.split_mode == SplitMode.FORCE.value else None,
        )
        await asyncio.to_thread(split_service.remove_batch_thumbnails, settings, batch.id)
        return SplitDecision("SKIP")


async def _give_up(
    session: AsyncSession, job: ProcessingJob, message: str | None, *, title: str | None = None,
) -> None:
    """Keine Aufteilung: normale Pipeline (split_mode OFF), optional Hinweis.
    Fuer "Dokument aufteilen" (Original existiert schon) ist das ein Fehler."""
    if job.source == JobSource.SPLIT_REQUEST:
        raise SplitNotPossibleError(f"{_CANNOT_SPLIT}.")
    job.split_mode = SplitMode.OFF.value
    if message:
        notify_system(session, title or "Aufteilung nicht möglich", message)
    await session.commit()


def _session_factory(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(session.bind, class_=AsyncSession, expire_on_commit=False)


async def _heartbeat(factory: async_sessionmaker[AsyncSession], job_id: str) -> None:
    async with factory() as s:
        await s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(processing_started_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        await s.commit()


async def _checkpoint(
    factory: async_sessionmaker[AsyncSession],
    batch_id: str,
    job_id: str,
    pages: list[dict],
    page_ocr: list[dict],
) -> None:
    """Fortschritt sichern (eigene kurze Transaktion): Merkmale + pages_done
    am Stapel, Seitentext am Job — beides in einem Commit."""
    async with factory() as s:
        await s.execute(
            update(SplitBatch)
            .where(SplitBatch.id == batch_id)
            .values(pages=json.dumps(pages), pages_done=len(pages))
            .execution_options(synchronize_session=False)
        )
        await s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(page_ocr=json.dumps(page_ocr, ensure_ascii=False))
            .execution_options(synchronize_session=False)
        )
        await s.commit()


def _save_thumbnail(image, path: Path, width: int) -> None:
    thumb = image.copy()
    thumb.thumbnail((width, width * 4))
    path.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(path, format="PNG", optimize=True)


def _save_preview(image, path: Path) -> None:
    preview = image.convert("L") if image.mode not in ("L", "RGB") else image.copy()
    preview.thumbnail((_PREVIEW_WIDTH, _PREVIEW_WIDTH * 4))
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path, format="JPEG", quality=80, optimize=True)


def _read_margin_code(image) -> str | None:
    """Text des Randcodes (erste Drehung, die einen Code ``/i/n/`` liefert)."""
    for angle in _MARGIN_ANGLES:
        text = ocr_service.ocr_rotated_strip(image, pdf_split_service.LEFT_STRIP, angle)
        if parse_margin_code(text):
            return text
    return None


def analyze_page(
    path: Path,
    n: int,
    geom: PageGeom,
    digital_text: str | None,
    thumb_dir: Path,
    settings: Settings,
    ocr_allowed: bool = True,
) -> tuple[dict, dict]:
    """Eine Seite (synchron, im Thread): Tinte, Leerseite, Text, Randcode,
    Merkmale, Vorschaubilder.

    Immer nur eine Seite gerendert (50 dpi fuer Tinte/Raster, 300 dpi nur fuer
    die OCR) — Speicherbedarf wie der normale OCR-Pfad.

    ``ocr_allowed=False`` (Zeitbudget erschoepft): keine OCR, ``source="skipped"`` —
    der Teil mit dieser Seite wird spaeter normal erkannt, Merkmale fehlen.

    Returns: (Merkmale fuer ``SplitBatch.pages``, Eintrag fuer ``page_ocr``).
    """
    small = pdf_split_service.render_page(path, n, dpi=_THUMB_DPI)
    ink, top_ink = pdf_split_service.page_ink_stats(small)
    margin_ink = pdf_split_service.left_strip_ink(small)
    blank = ink < settings.SPLIT_BLANK_INK
    _save_thumbnail(small, thumb_dir / f"p{n:03d}.png", settings.SPLIT_THUMBNAIL_WIDTH)
    del small

    digital = (digital_text or "").strip()
    preview_image = None
    margin_text = None
    if len(digital) >= ocr_service.MIN_DIGITAL_PAGE_CHARS:
        text, confidence, source = digital, 1.0, "digital"
    elif blank:
        # Leerseiten: keine Inhalts-OCR (spart ~0,5 s je Seite)
        text, confidence, source = digital, (1.0 if digital else 0.0), "none"
    elif not ocr_allowed:
        text, confidence, source = "", 0.0, "skipped"
    else:
        preview_image = ocr_service.render_pdf_page_for_ocr(path, n)
        scanned = (
            # Zeilen ganz im linken Randstreifen (Druckerei-Code) sind Muell
            ocr_service.ocr_page_image(
                preview_image, n, settings.OCR_LANGUAGES, exclude_left=pdf_split_service.LEFT_STRIP,
            )
            if preview_image is not None else None
        )
        if scanned is not None and len(scanned.text) > len(digital):
            text, confidence, source = scanned.text, scanned.confidence, "ocr"
        elif digital:
            text, confidence, source = digital, 1.0, "digital"
        else:
            text, confidence, source = "", 0.0, "ocr"
        if preview_image is not None and margin_ink >= _MARGIN_MIN_INK:
            margin_text = _read_margin_code(preview_image)

    if preview_image is None:
        preview_image = pdf_split_service.render_page(path, n, dpi=_PREVIEW_DPI, max_px=1400)
    _save_preview(preview_image, thumb_dir / f"v{n:03d}.jpg")
    del preview_image

    features = {
        "n": n,
        "w": geom.w,
        "h": geom.h,
        "rot": geom.rot,
        "ink": ink,
        "top_ink": top_ink,
        "blank": blank,
        "source": source,
        "conf": round(confidence, 3),
        "sig": None if source == "skipped" else extract_features(text, margin_text),
    }
    return features, {"page": n, "text": text, "confidence": round(confidence, 4), "source": source}


def build_proposal(pages: list[dict], partial_from: int | None) -> dict:
    """Vorschlag aus den Seitenmerkmalen: Uebergaenge mit Entscheidung und
    Gruenden, Duplex, Art der Leerseiten. Ab ``partial_from`` keine Schnitte."""
    duplex, duplex_conf = detect_duplex(pages)
    boundaries, blank_kinds = score_boundaries(pages, duplex_conf=duplex_conf, partial_from=partial_from)
    return {
        "boundaries": boundaries,
        "duplex": duplex,
        "duplex_conf": duplex_conf,
        "partial_from": partial_from,
        "blank_kinds": {str(n): kind for n, kind in blank_kinds.items()},
    }


def proposal_counts(proposal: dict) -> tuple[int, int]:
    """(Schnitte, unsichere Uebergaenge)."""
    boundaries = proposal.get("boundaries") or []
    return (
        sum(1 for b in boundaries if b.get("decision") == "cut"),
        sum(1 for b in boundaries if b.get("decision") == "uncertain"),
    )


async def run_detection(
    job: ProcessingJob,
    batch: SplitBatch,
    settings: Settings,
    session: AsyncSession,
    *,
    should_pause: Callable[[], bool] = lambda: False,
) -> SplitDecision:
    """Seiten ab dem Checkpoint ``pages_done`` erkennen, dann bewerten.

    Ergebnis: Stapel PROPOSED + Job AWAITING_SPLIT (eine Transaktion) — oder
    bei AUTO ohne Schnitt und ohne unsicheren Uebergang: Stapel geloescht, Job
    laeuft normal weiter (``SplitDecision("SKIP", pages=...)``).

    Keine Schreibtransaktion ueber die OCR hinweg: Heartbeat pro Seite und
    Checkpoint alle CHECKPOINT_EVERY Seiten laufen in eigenen kurzen Sessions.
    """
    started = time.monotonic()
    factory = _session_factory(session)
    path = Path(batch.file_path)
    done = batch.pages_done or 0
    pages = split_service.load_pages(batch)[:done]
    page_ocr = (json.loads(job.page_ocr) if job.page_ocr else [])[:done]
    if len(pages) != done or len(page_ocr) != done:
        # Inkonsistenter Checkpoint (sollte nicht vorkommen) -> von vorn.
        pages, page_ocr, done = [], [], 0

    geometry = await asyncio.to_thread(pdf_split_service.pdf_page_geometry, path)
    if len(geometry) != batch.page_count:
        raise PdfSplitError("Seitenzahl hat sich geändert")
    digital = await asyncio.to_thread(ocr_service.read_pdf_text_pages, path) or []
    thumb_dir = split_service.batch_thumbnail_dir(settings, batch.id)

    partial_from = next((p["n"] for p in pages if p.get("source") == "skipped"), None)
    deadline = started + settings.SPLIT_TIME_BUDGET_S - (batch.detection_ms or 0) / 1000
    for n in range(done + 1, batch.page_count + 1):
        if partial_from is None and time.monotonic() > deadline:
            # SPLIT_TIME_BUDGET_S: ab hier keine Texterkennung mehr; der
            # Editor markiert den Bereich als "ohne Texterkennung geprueft".
            partial_from = n
            logger.warning("Stapel %s: Zeitbudget erschoepft, ab Seite %d ohne Texterkennung", batch.id, n)
        features, entry = await asyncio.to_thread(
            analyze_page, path, n, geometry[n - 1],
            digital[n - 1] if n - 1 < len(digital) else None, thumb_dir, settings,
            partial_from is None,
        )
        pages.append(features)
        page_ocr.append(entry)
        await _heartbeat(factory, job.id)
        if n % CHECKPOINT_EVERY == 0 or n == batch.page_count:
            await _checkpoint(factory, batch.id, job.id, pages, page_ocr)
            if n < batch.page_count and should_pause():
                logger.info("Queue pausiert — Erkennung von Stapel %s ruht nach Seite %d", batch.id, n)
                job.status = JobStatus.PENDING
                job.processing_started_at = None
                job.page_ocr = json.dumps(page_ocr, ensure_ascii=False)
                await session.commit()
                return SplitDecision("PAUSED", batch.id)

    proposal = build_proposal(pages, partial_from)
    llm_used = False
    config = await split_service.load_split_settings(session, settings)
    if config.use_llm:
        llm_used = await split_llm_service.llm_refine(
            proposal, pages, page_ocr, settings, forced=job.split_mode == SplitMode.FORCE.value,
        )
        await _heartbeat(factory, job.id)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    detection_ms = (batch.detection_ms or 0) + elapsed_ms
    cuts, uncertain = proposal_counts(proposal)

    if job.split_mode == SplitMode.AUTO.value and not cuts and not uncertain:
        return await _single_document(job, batch, settings, session, page_ocr, partial_from, detection_ms)

    await split_service.transition(
        session, batch.id,
        expected=SplitBatchStatus.DETECTING, new=SplitBatchStatus.PROPOSED,
        pages=json.dumps(pages), pages_done=len(pages), proposal=json.dumps(proposal),
        detector_version=f"{DETECTOR_VERSION}+{split_llm_service.LLM_VERSION}" if llm_used else DETECTOR_VERSION,
        llm_used=llm_used,
        detection_ms=detection_ms,
    )
    job.status = JobStatus.AWAITING_SPLIT
    job.processing_started_at = None
    job.page_ocr = json.dumps(page_ocr, ensure_ascii=False)
    notify_split_needed(session, job.original_filename, parts=cuts + 1, uncertain=uncertain > 0)
    await session.commit()
    logger.info(
        "Stapel %s erkannt: %d Seiten in %d ms, %d Schnitt(e), %d unsicher — wartet auf die Aufteilung",
        batch.id, batch.page_count, elapsed_ms, cuts, uncertain,
    )
    return SplitDecision("AWAITING", batch.id)


async def _single_document(
    job: ProcessingJob,
    batch: SplitBatch,
    settings: Settings,
    session: AsyncSession,
    page_ocr: list[dict],
    partial_from: int | None,
    detection_ms: int,
) -> SplitDecision:
    """AUTO, nur ein Dokument erkannt: Stapel loeschen (kein Ballast fuer
    normale Mehrseiter), der Job laeuft mit dem erkannten Seitentext weiter.

    Die Datei bleibt unter ``data/split/<id>/source.pdf`` (der Job zeigt
    dorthin); nach der Archivierung entfernt der Worker sie samt Ordner.
    """
    batch_id = batch.id
    job.split_batch_id = None
    job.split_role = None
    job.split_mode = SplitMode.OFF.value
    job.page_ocr = None
    await session.flush()
    # Keine dauerhafte zweite Textkopie: der Text geht direkt in die Analyse.
    # Explizit per UPDATE — die Checkpoints schrieben page_ocr in eigenen
    # Sessions, das Objekt hier kennt diesen Stand nicht.
    await session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job.id)
        .values(page_ocr=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(SplitBatch)
        .where(SplitBatch.id == batch_id, SplitBatch.status == SplitBatchStatus.DETECTING)
        .execution_options(synchronize_session=False)
    )
    session.expunge(batch)
    if partial_from is not None:
        notify_system(
            session,
            "Aufteilung nicht vollständig geprüft",
            f"„{job.original_filename}“: Aufteilung nicht vollständig geprüft – bei Bedarf "
            "„Dokument aufteilen“ nutzen.",
        )
    await session.commit()
    await asyncio.to_thread(split_service.remove_batch_thumbnails, settings, batch_id)
    logger.info(
        "Job %s: ein Dokument erkannt (%d Seiten, %d ms) — normale Verarbeitung",
        job.id, len(page_ocr), detection_ms,
    )
    return SplitDecision("SKIP", pages=_pages_from_entries(page_ocr))
