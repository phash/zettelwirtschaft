"""Aenderungsvorschlaege und Massenlauf der Neu-Analyse (Issue #39).

``POST /review/documents/{id}/reanalyze`` (api/review.py) legt Vorschlaege an;
hier werden sie angezeigt, uebernommen und verworfen — einzeln oder gesammelt.
``/reanalysis/runs`` startet und steuert den Massenlauf
(services/reanalysis_run_service.py).
"""

import logging
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import build_document_response
from app.config import Settings, get_settings
from app.database import get_db, get_session_factory
from app.schemas.background_task import TaskResponse
from app.schemas.document import DocumentResponse
from app.schemas.reanalysis import (
    FIELD_LABELS,
    ApplyRequest,
    BulkProposalRequest,
    FieldChangeResponse,
    ProposalListResponse,
    ProposalResponse,
    RunPreviewResponse,
    RunRequest,
    RunResponse,
)
from app.services.reanalysis_run_service import (
    RunConflictError,
    RunStateError,
    current_run,
    preview_run,
    run_summary,
    set_run_status,
    start_run,
)
from app.services.reanalysis_service import (
    NO_PROPOSAL_MESSAGE,
    PROPOSAL_FIELDS,
    TEXT_ONLY_FIELDS,
    FieldChange,
    ProposalConflict,
    ProposalNotFound,
    VisibleProposal,
    apply_proposal,
    compute_changes,
    discard_proposal,
    get_open_proposal,
    list_visible_proposals,
    load_document_for_review,
)
from app.services.task_service import TaskContext, start_task

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reanalysis"])

APPLY_ALL_TASK_KIND = "reanalysis_apply_all"


def _validated_field(field: str | None) -> str | None:
    if field is not None and field not in PROPOSAL_FIELDS:
        raise HTTPException(422, f"Unbekanntes Feld: {field}")
    return field


def _matches(item: VisibleProposal, field: str | None) -> bool:
    """Passt der Vorschlag zum Feldfilter? Reine Textfelder zaehlen nie als Treffer."""
    if field is None:
        return True
    return field not in TEXT_ONLY_FIELDS and any(change.field == field for change in item.changes)


def _change_response(change: FieldChange) -> FieldChangeResponse:
    return FieldChangeResponse(
        field=change.field, label=FIELD_LABELS.get(change.field, change.field),
        old=change.old, new=change.new,
    )


def _proposal_response(item: VisibleProposal) -> ProposalResponse:
    proposal = item.proposal
    return ProposalResponse(
        document_id=item.document.id,
        document_title=item.document.title,
        model=proposal.model,
        with_ocr=proposal.with_ocr,
        ocr_note=proposal.ocr_note,
        created_at=proposal.created_at,
        changes=[_change_response(change) for change in item.changes],
    )


@router.get("/reanalysis/proposals", response_model=ProposalListResponse)
async def list_proposals(
    field: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> ProposalListResponse:
    """Offene Vorschlaege (neueste zuerst), optional nur mit Aenderung an ``field``.

    ``field_counts`` zaehlt ueber ALLE sichtbaren Vorschlaege (unabhaengig vom
    Filter) und ohne die reinen Textfelder Titel/Zusammenfassung.
    """
    _validated_field(field)
    visible = await list_visible_proposals(session, settings)

    field_counts: dict[str, int] = {}
    for item in visible:
        for name in {c.field for c in item.changes} - TEXT_ONLY_FIELDS:
            field_counts[name] = field_counts.get(name, 0) + 1

    matching = [item for item in visible if _matches(item, field)]
    start = (page - 1) * page_size
    return ProposalListResponse(
        items=[_proposal_response(item) for item in matching[start:start + page_size]],
        total=len(matching),
        field_counts=field_counts,
    )


@router.get("/reanalysis/proposals/{document_id}", response_model=ProposalResponse)
async def get_proposal(
    document_id: str,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> ProposalResponse:
    """Der offene Vorschlag eines Dokuments mit seinen aktuellen Aenderungen."""
    doc = await load_document_for_review(session, document_id)
    proposal = await get_open_proposal(session, document_id) if doc is not None else None
    if doc is None or proposal is None:
        raise HTTPException(404, NO_PROPOSAL_MESSAGE)
    changes = await compute_changes(session, doc, proposal, settings)
    return _proposal_response(VisibleProposal(doc, proposal, changes))


@router.post("/reanalysis/proposals/{document_id}/apply", response_model=DocumentResponse)
async def apply_document_proposal(
    document_id: str,
    body: ApplyRequest | None = None,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> DocumentResponse:
    """Uebernimmt die gewaehlten Felder (``fields=None``: alle) und erledigt den Vorschlag."""
    fields = body.fields if body is not None else None
    if fields is not None:
        if not fields:
            raise HTTPException(422, "Keine Felder ausgewählt. Zum Ablehnen den Vorschlag verwerfen.")
        for name in fields:
            _validated_field(name)
    try:
        await apply_proposal(
            session, document_id, settings,
            set(fields) if fields is not None else None,
            session_factory=session_factory,
        )
    except ProposalNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProposalConflict as exc:
        raise HTTPException(409, exc.message) from exc

    # apply_proposal aendert Tags/Garantie ueber die Junction-Tabellen: die
    # Beziehungen der Session sind veraltet und werden neu geladen.
    session.expire_all()
    doc = await load_document_for_review(session, document_id)
    return await build_document_response(session, doc, settings)


@router.post("/reanalysis/proposals/{document_id}/discard", status_code=204)
async def discard_document_proposal(
    document_id: str,
    session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Verwirft den Vorschlag; das Dokument bleibt unveraendert."""
    if not await discard_proposal(session, document_id):
        raise HTTPException(404, NO_PROPOSAL_MESSAGE)
    await session.commit()
    return Response(status_code=204)


@router.post("/reanalysis/proposals/apply-all", status_code=202, response_model=TaskResponse)
async def apply_all_proposals(
    body: BulkProposalRequest | None = None,
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
    session: AsyncSession = Depends(get_db, scope="function"),
) -> TaskResponse:
    """Uebernimmt alle sichtbaren Vorschlaege (mit ``field``: nur dieses Feld) als Hintergrund-Vorgang.

    Pro Dokument eigene Session; ein Konflikt (z.B. fehlende Datei) wird
    gezaehlt und bricht den Lauf nicht ab, ebenso unerwartete Fehler (``errors``).
    Der Feldfilter waehlt nur die Dokumente aus; uebernommen werden dann alle
    aktuellen Aenderungen des Vorschlags. Ergebnis: ``{"applied", "conflicts", "errors"}``.
    """
    field = _validated_field(body.field if body is not None else None)
    document_ids = [
        item.document.id
        for item in await list_visible_proposals(session, settings)
        if _matches(item, field)
    ]

    async def work(ctx: TaskContext) -> dict:
        applied = conflicts = errors = 0
        total = len(document_ids)
        for index, document_id in enumerate(document_ids, start=1):
            async with ctx.session_factory() as doc_session:
                try:
                    await apply_proposal(
                        doc_session, document_id, settings, session_factory=ctx.session_factory,
                    )
                    applied += 1
                except ProposalConflict:
                    conflicts += 1
                except ProposalNotFound:
                    pass  # inzwischen verworfen oder Dokument geloescht
                except Exception:
                    logger.exception("Vorschlag für Dokument %s konnte nicht übernommen werden", document_id)
                    errors += 1
            await ctx.report_progress(index * 100 // total, f"{index} von {total} Vorschlägen bearbeitet …")
        return {"applied": applied, "conflicts": conflicts, "errors": errors}

    task = await start_task(
        session_factory, APPLY_ALL_TASK_KIND, work, key="global",
        conflict_message="Es werden bereits Vorschläge übernommen.",
    )
    return TaskResponse.model_validate(task)


@router.post("/reanalysis/proposals/discard-all")
async def discard_all_proposals(
    body: BulkProposalRequest | None = None,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    """Verwirft alle sichtbaren Vorschlaege (mit ``field``: nur die mit diesem Feld)."""
    field = _validated_field(body.field if body is not None else None)
    discarded = 0
    for item in await list_visible_proposals(session, settings):
        if _matches(item, field) and await discard_proposal(session, item.document.id):
            discarded += 1
    await session.commit()
    return {"discarded": discarded}


# --- Massenlauf -----------------------------------------------------------------


@router.post("/reanalysis/runs/preview", response_model=RunPreviewResponse)
async def preview_reanalysis_run(
    body: RunRequest,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> RunPreviewResponse:
    """Wie viele Dokumente ein Lauf erfassen wuerde, welche nicht (und warum), grobe Dauer."""
    preview, _ = await preview_run(
        session, settings,
        document_ids=body.document_ids, outdated=body.outdated, with_ocr=body.with_ocr,
    )
    return RunPreviewResponse(**asdict(preview))


@router.post("/reanalysis/runs", status_code=201, response_model=RunResponse)
async def start_reanalysis_run(
    body: RunRequest,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
):
    """Startet den Massenlauf. 409 mit dem aktiven Lauf, wenn schon einer laeuft."""
    try:
        run = await start_run(
            session_factory, settings,
            document_ids=body.document_ids, outdated=body.outdated, with_ocr=body.with_ocr,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RunConflictError as exc:
        active = await run_summary(session, exc.active_run)
        return JSONResponse(
            status_code=409,
            content={"detail": exc.message, "run": active.model_dump(mode="json")},
        )
    return await run_summary(session, run)


@router.get("/reanalysis/runs/current", response_model=RunResponse)
async def get_current_reanalysis_run(
    session: AsyncSession = Depends(get_db, scope="function"),
) -> RunResponse:
    """Der aktive Lauf, sonst der zuletzt beendete; 404, wenn es noch keinen gab."""
    run = await current_run(session)
    if run is None:
        raise HTTPException(404, "Es gibt noch keinen Neu-Analyse-Lauf.")
    return await run_summary(session, run)


@router.post("/reanalysis/runs/{run_id}/{action}", response_model=RunResponse)
async def control_reanalysis_run(
    run_id: str,
    action: Literal["pause", "resume", "cancel"],
    session: AsyncSession = Depends(get_db, scope="function"),
) -> RunResponse:
    """Pausieren, Fortsetzen oder Abbrechen (offene Dokumente -> uebersprungen)."""
    try:
        run = await set_run_status(session, run_id, action)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RunStateError as exc:
        raise HTTPException(409, exc.message) from exc
    await session.commit()
    return await run_summary(session, run)
