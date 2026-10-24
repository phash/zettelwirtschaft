"""Neu-Analyse eines archivierten Dokuments mit Aenderungsvorschlag (Issue #39).

Die Neu-Analyse ueberschreibt das Dokument nicht mehr direkt. Ablauf:

1. :func:`create_proposal` (Hintergrund-Vorgang ``reanalyze``) laesst die
   Haupt-Pipeline ``analyze_document`` (inkl. OCR-Sanitizer, Verifier,
   Fallbacks, Few-Shot) mit dem gespeicherten Text laufen, wendet die
   Auto-Korrekturen an und speichert das Ergebnis als
   :class:`ReanalysisProposal`. Das Dokument bleibt unveraendert. Pro Dokument
   gibt es hoechstens einen offenen Vorschlag; ein neuer ersetzt den alten.
   Aendern sich nur Titel und/oder Zusammenfassung, bleibt kein Vorschlag
   offen — das Dokument wird nur als "mit <Modell> geprueft" markiert.
2. :func:`compute_changes` vergleicht den Vorschlag zur Lesezeit mit dem
   AKTUELLEN Dokument (kein Schnappschuss alter Werte). Nur echte
   Unterschiede erscheinen. Nie angeboten werden:

   - vom Benutzer bearbeitete Felder (AuditLog UPDATED: PATCH /documents,
     beantwortete Rueckfragen, PATCH /warranties fuer die Garantie) — auch
     nicht, wenn die Bearbeitung erst nach dem Vorschlag kam;
   - Tags gegen die Entscheidung des Benutzers (manuell hinzugefuegte
     bleiben, manuell entfernte kommen nicht wieder);
   - ein Ablagebereich, den der Benutzer gesetzt hat, und ein blosser
     Rueckfall auf den Standardbereich (Bereich aus dem Vorschlag geloescht
     oder unbekannt), solange das Dokument in einem gueltigen Bereich liegt.
3. :func:`apply_proposal` uebernimmt die angenommenen Felder (pro Feld oder
   alle), :func:`discard_proposal` verwirft den Vorschlag. Beides vermerkt
   das Modell am Dokument (``analysis_model``). Uebernommene Felder gelten
   NICHT als vom Benutzer bearbeitet, abgelehnte werden nicht geschuetzt.

Faellt die KI aus, entsteht kein Vorschlag und der Vorgang endet mit einer
verstaendlichen Fehlermeldung.
"""

import enum
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.date_utils import parse_date_lenient
from app.core.normalize import DEFAULT_CURRENCY, normalize_currency
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, ReviewStatus
from app.models.reanalysis import ReanalysisProposal
from app.models.warranty_info import WARRANTY_AUDIT_PREFIX, WarrantyInfo
from app.services import vectorize_service
from app.services.analysis_service import AnalysisResult, analyze_document
from app.services.archive_service import (
    ScopeMatch,
    _build_review_question,
    _build_warranty_info,
    _get_or_create_tags,
    _match_filing_scope,
    _parse_document_type,
    _parse_tax_category,
    build_scope_question,
    coerce_decimal_amount,
    normalize_tag_names,
)
from app.services.correction_service import apply_auto_corrections
from app.services.file_relocation import (
    FileMove,
    RelocationError,
    copy_files,
    discard_copies,
    known_scope_slugs,
    plan_move,
    remove_sources,
)
from app.services.filing_scope_service import scopes_for_analysis
from app.services.ollama_client import OllamaError, OllamaErrorKind
from app.services.search_service import reindex_document_fts
from app.services.task_service import TaskContext, TaskError
from app.services.tax_year import derive_tax_year_for, ensure_tax_year

logger = logging.getLogger(__name__)

TASK_KIND = "reanalyze"

SCOPE_FIELD = "filing_scope_id"

# KI-Felder des Dokuments, die eine Neu-Analyse aendern kann (Dokumentfeld-Namen).
REANALYZED_FIELDS = (
    "document_type", "title", "issuer", "document_date", "amount", "currency",
    "summary", "tax_relevant", "tax_category", "tax_year", "recipient",
    "reference_number",
)

# Alle Felder eines Vorschlags, in Anzeige-Reihenfolge.
PROPOSAL_FIELDS: tuple[str, ...] = REANALYZED_FIELDS + (SCOPE_FIELD, "tags", "warranty", "ocr_text")

# Umformulierte Texte allein machen keinen Vorschlag (Owner-Entscheidung).
TEXT_ONLY_FIELDS = frozenset({"title", "summary"})

# Verglichene/angezeigte Garantiefelder (wie _sync_warranty sie setzt).
WARRANTY_FIELDS = (
    "product_name", "purchase_date", "warranty_end_date", "warranty_type",
    "warranty_duration_months", "retailer",
)

DOCUMENT_GONE_MESSAGE = "Das Dokument existiert nicht mehr oder wurde gelöscht."
NO_OCR_TEXT_MESSAGE = "Kein OCR-Text vorhanden, Re-Analyse nicht möglich."
NO_PROPOSAL_MESSAGE = "Für dieses Dokument liegt kein offener Änderungsvorschlag vor."
MISSING_FILE_MESSAGE = (
    "Die Datei des Dokuments fehlt im Archiv, deshalb kann der Ablagebereich nicht "
    "gewechselt werden. Es wurde nichts geändert."
)


class ReanalysisLLMError(TaskError):
    """Die KI-Analyse ist ausgefallen; ``kind`` nennt die Fehlerart."""

    def __init__(self, message: str, kind: OllamaErrorKind):
        super().__init__(message)
        self.kind = kind


class ProposalConflict(Exception):
    """Vorschlag kann so nicht uebernommen werden (nichts wurde geaendert)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProposalNotFound(LookupError):
    """Kein offener Vorschlag (oder Dokument unbekannt/geloescht)."""


@dataclass
class FieldChange:
    """Ein Unterschied Vorschlag <-> Dokument, JSON-faehig (alt -> neu)."""

    field: str
    old: Any
    new: Any


@dataclass
class ProposalOutcome:
    status: Literal["proposed", "unchanged"]
    changes: list[FieldChange]
    ocr_note: str | None = None


@dataclass
class _PlannedChange:
    change: FieldChange
    value: Any  # Wert, den apply_proposal setzt (Python-Typ, nicht JSON)


@dataclass
class _Plan:
    analysis: AnalysisResult
    changes: list[_PlannedChange]
    scopes: list[dict]
    scope_match: ScopeMatch | None


@dataclass
class _TextSource:
    """Text fuer die Analyse und ggf. neu erkannter OCR-Text fuer den Vorschlag."""

    analysis_text: str
    new_ocr_text: str | None = None
    new_ocr_confidence: float | None = None
    ocr_note: str | None = None


async def load_document_for_review(session: AsyncSession, document_id: str) -> Document | None:
    """Dokument laden — ``None`` fuer unbekannte UND geloeschte Dokumente."""
    result = await session.execute(
        select(Document)
        .where(Document.id == document_id)
        .where(Document.status != DocumentStatus.DELETED)
    )
    return result.scalar_one_or_none()


async def get_open_proposal(session: AsyncSession, document_id: str) -> ReanalysisProposal | None:
    """Offener Vorschlag eines Dokuments; geloeschte Dokumente haben keinen."""
    result = await session.execute(
        select(ReanalysisProposal)
        .join(Document, Document.id == ReanalysisProposal.document_id)
        .where(ReanalysisProposal.document_id == document_id)
        .where(Document.status != DocumentStatus.DELETED)
    )
    return result.scalar_one_or_none()


async def user_edited_fields(session: AsyncSession, document_id: str) -> set[str]:
    """Feldnamen, die der Benutzer je bearbeitet hat (AuditLog UPDATED)."""
    result = await session.execute(
        select(AuditLog.details)
        .where(AuditLog.document_id == document_id)
        .where(AuditLog.action == AuditAction.UPDATED)
    )
    fields: set[str] = set()
    for (details,) in result.all():
        try:
            data = json.loads(details or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            fields.update(data.keys())
    return fields


async def _user_tag_decisions(session: AsyncSession, document_id: str) -> tuple[set[str], set[str]]:
    """(manuell hinzugefuegt, manuell entfernt) — jeweils nach letztem Ereignis."""
    result = await session.execute(
        select(AuditLog.action, AuditLog.details)
        .where(AuditLog.document_id == document_id)
        .where(AuditLog.action.in_([AuditAction.TAG_ADDED, AuditAction.TAG_REMOVED]))
        .order_by(AuditLog.id)
    )
    last: dict[str, str] = {}
    for action, details in result.all():
        try:
            name = str(json.loads(details or "{}").get("tag") or "").strip().lower()
        except (TypeError, ValueError, AttributeError):
            continue
        if name:
            last[name] = action
    added = {n for n, a in last.items() if a == AuditAction.TAG_ADDED}
    removed = {n for n, a in last.items() if a == AuditAction.TAG_REMOVED}
    return added, removed


def _llm_failure(analysis: AnalysisResult | None, settings: Settings) -> ReanalysisLLMError:
    kind = OllamaErrorKind.INVALID_RESPONSE
    if analysis is not None and analysis.llm_error:
        try:
            kind = OllamaErrorKind(analysis.llm_error)
        except ValueError:
            pass
    reason = OllamaError(kind, model=settings.OLLAMA_MODEL).user_message
    return ReanalysisLLMError(
        f"Die Re-Analyse ist fehlgeschlagen: {reason} Das Dokument wurde nicht verändert.", kind,
    )


def _analysis_values(analysis: AnalysisResult, original_filename: str) -> dict:
    """KI-Ergebnis als Dokumentfeld-Werte (gleiche Helfer wie archive_document)."""
    return {
        "document_type": _parse_document_type(analysis.document_type),
        "title": analysis.title or original_filename,
        "issuer": analysis.sender,
        "document_date": parse_date_lenient(analysis.document_date),
        "amount": coerce_decimal_amount(analysis.amount),
        "currency": normalize_currency(analysis.currency) or DEFAULT_CURRENCY,
        "summary": analysis.summary,
        "tax_relevant": bool(analysis.tax_relevant),
        "tax_category": _parse_tax_category(analysis.tax_category),
        "tax_year": analysis.tax_year,
        "recipient": analysis.recipient,
        "reference_number": analysis.reference_number,
    }


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _json_value(value: Any) -> Any:
    """Feldwert JSON-faehig: Enum-Wert, Datum ISO, Decimal als String."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _comparable(field: str, value: Any) -> Any:
    """Vergleichswert: Leerstring == None, Enum == gespeicherter Wert."""
    if field == "tax_relevant":
        return bool(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, str):
        return value.strip() or None
    return value


def _scope_ref(scope_id: str | None, scopes_by_id: dict[str, dict]) -> dict | None:
    if scope_id is None:
        return None
    return {"id": scope_id, "name": scopes_by_id.get(scope_id, {}).get("name")}


def _warranty_dict(warranty: WarrantyInfo | None) -> dict | None:
    if warranty is None:
        return None
    return {field: _json_value(getattr(warranty, field)) for field in WARRANTY_FIELDS}


def _target_tag_names(
    current: set[str], new_names: list[str], manual_added: set[str], manual_removed: set[str],
) -> set[str]:
    """Tags nach der Neu-Analyse: neue KI-Tags (ohne manuell entfernte) plus
    vorhandene, die der Benutzer selbst hinzugefuegt hat."""
    wanted = {name for name in normalize_tag_names(new_names) if name not in manual_removed}
    return wanted | {name for name in current if name in manual_added}


async def _plan_changes(session: AsyncSession, doc: Document, proposal: ReanalysisProposal) -> _Plan:
    """Echte Unterschiede Vorschlag <-> aktuelles Dokument, ohne geschuetzte Felder."""
    analysis = proposal.analysis_result()
    protected = await user_edited_fields(session, doc.id)
    values = _analysis_values(analysis, doc.original_filename)

    def effective(field: str) -> Any:
        """Wert nach der Uebernahme: geschuetzte Felder behalten den Dokumentwert."""
        return getattr(doc, field) if field in protected else values[field]

    # Wie ensure_tax_year nach dem Uebernehmen — sonst erschiene ein
    # abgeleitetes Steuerjahr als Scheinaenderung "2025 -> leer".
    if values["tax_year"] is None and effective("tax_relevant"):
        values["tax_year"] = derive_tax_year_for(doc, effective("document_date"))

    planned: list[_PlannedChange] = []
    for field in REANALYZED_FIELDS:
        if field in protected:
            continue
        old, new = getattr(doc, field), values[field]
        if _comparable(field, old) != _comparable(field, new):
            planned.append(_PlannedChange(FieldChange(field, _json_value(old), _json_value(new)), new))

    scopes = await scopes_for_analysis(session)
    scope_match = None
    if scopes and SCOPE_FIELD not in protected:
        by_id = {scope["id"]: scope for scope in scopes}
        match = _match_filing_scope(analysis, scopes, ocr_text=proposal.ocr_text or doc.ocr_text)
        # Rueckfall auf den Standardbereich ist kein Grund, ein Dokument aus
        # einem gueltigen Bereich zu holen (Bereich geloescht oder unbekannt).
        fallback_only = match.source == "default" and doc.filing_scope_id in by_id
        if match.scope_id and match.scope_id != doc.filing_scope_id and not fallback_only:
            scope_match = match
            planned.append(_PlannedChange(
                FieldChange(SCOPE_FIELD, _scope_ref(doc.filing_scope_id, by_id), _scope_ref(match.scope_id, by_id)),
                match,
            ))

    manual_added, manual_removed = await _user_tag_decisions(session, doc.id)
    current_tags = {tag.name for tag in doc.tags}
    target_tags = _target_tag_names(current_tags, analysis.tags, manual_added, manual_removed)
    if target_tags != current_tags:
        planned.append(_PlannedChange(FieldChange("tags", sorted(current_tags), sorted(target_tags)), target_tags))

    if not any(key.startswith(WARRANTY_AUDIT_PREFIX) for key in protected):
        new_warranty = _build_warranty_info(analysis.warranty_info, doc.id, effective("document_date"))
        old_dict, new_dict = _warranty_dict(doc.warranty_info), _warranty_dict(new_warranty)
        if old_dict != new_dict:
            planned.append(_PlannedChange(FieldChange("warranty", old_dict, new_dict), new_warranty))

    if proposal.ocr_text is not None and proposal.ocr_text != doc.ocr_text:
        planned.append(_PlannedChange(
            FieldChange(
                "ocr_text",
                {"text": doc.ocr_text, "confidence": doc.ocr_confidence},
                {"text": proposal.ocr_text, "confidence": proposal.ocr_confidence},
            ),
            (proposal.ocr_text, proposal.ocr_confidence),
        ))

    return _Plan(analysis=analysis, changes=planned, scopes=scopes, scope_match=scope_match)


async def compute_changes(
    session: AsyncSession, doc: Document, proposal: ReanalysisProposal, settings: Settings,
) -> list[FieldChange]:
    """Aenderungen des Vorschlags gegenueber dem aktuellen Dokument (Reihenfolge = PROPOSAL_FIELDS)."""
    plan = await _plan_changes(session, doc, proposal)
    return [planned.change for planned in plan.changes]


def is_substantive(changes: list[FieldChange]) -> bool:
    """True, wenn sich mehr als nur Titel/Zusammenfassung aendert."""
    return any(change.field not in TEXT_ONLY_FIELDS for change in changes)


# ---------------------------------------------------------------------------
# Vorschlag anlegen
# ---------------------------------------------------------------------------


async def _report(ctx: TaskContext | None, percent: int, text: str) -> None:
    if ctx is not None:
        await ctx.report_progress(percent, text)


async def _obtain_text(ocr_text: str | None, with_ocr: bool) -> _TextSource:
    """Text fuer die Analyse: der gespeicherte OCR-Text.

    Die Option "Text neu erkennen" (``with_ocr``) setzt hier an und liefert
    zusaetzlich den neu erkannten Text fuer den Vorschlag.
    """
    if not (ocr_text or "").strip():
        raise TaskError(NO_OCR_TEXT_MESSAGE)
    return _TextSource(analysis_text=ocr_text)


async def create_proposal(
    ctx: TaskContext | None,
    session_factory,
    document_id: str,
    settings: Settings,
    *,
    with_ocr: bool,
    run_id: str | None = None,
) -> ProposalOutcome:
    """Analysiert das Dokument neu und legt einen Aenderungsvorschlag an.

    Das Dokument selbst wird nicht veraendert — ausser ``analysis_model``,
    wenn sich nichts Wesentliches aendert (dann bleibt kein Vorschlag offen).
    Ein offener Vorschlag des Dokuments wird ersetzt.

    Raises:
        TaskError: Dokument geloescht/unbekannt oder kein Text vorhanden.
        ReanalysisLLMError: die KI-Analyse ist ausgefallen.
    """
    await _report(ctx, 5, "Dokument wird geladen …")
    async with session_factory() as session:
        doc = await load_document_for_review(session, document_id)
        if doc is None:
            raise TaskError(DOCUMENT_GONE_MESSAGE)
        file_path, file_type, ocr_text = doc.file_path, doc.file_type, doc.ocr_text
        filing_scopes = await scopes_for_analysis(session)

    source = await _obtain_text(ocr_text, with_ocr)

    await _report(ctx, 15, "Die KI analysiert das Dokument …")
    # Eigene Session nur zum Lesen der Few-Shot-Korrekturen — keine
    # Schreib-Transaktion ueber den LLM-Aufruf.
    async with session_factory() as session:
        _, analysis = await analyze_document(
            Path(file_path), file_type, settings, filing_scopes,
            session=session, pre_ocr_text=source.analysis_text,
        )
    if analysis is None or analysis.llm_error:
        raise _llm_failure(analysis, settings)

    await _report(ctx, 85, "Änderungsvorschlag wird erstellt …")
    async with session_factory() as session:
        doc = await load_document_for_review(session, document_id)
        if doc is None:
            raise TaskError(DOCUMENT_GONE_MESSAGE)

        await apply_auto_corrections(session, analysis)
        proposal = ReanalysisProposal(
            document_id=doc.id,
            model=settings.OLLAMA_MODEL,
            with_ocr=with_ocr,
            analysis=analysis.to_dict(),
            ocr_text=source.new_ocr_text,
            ocr_confidence=source.new_ocr_confidence,
            ocr_note=source.ocr_note,
            run_id=run_id,
        )
        changes = await compute_changes(session, doc, proposal, settings)
        await session.execute(delete(ReanalysisProposal).where(ReanalysisProposal.document_id == doc.id))
        if is_substantive(changes):
            session.add(proposal)
            outcome = ProposalOutcome("proposed", changes, source.ocr_note)
        else:
            doc.analysis_model = settings.OLLAMA_MODEL
            outcome = ProposalOutcome("unchanged", [], source.ocr_note)
        await session.commit()

    logger.info(
        "Neu-Analyse fuer Dokument %s (%s): %s, Felder=%s",
        document_id, settings.OLLAMA_MODEL, outcome.status, [c.field for c in outcome.changes],
    )
    return outcome


async def run_reanalysis(ctx: TaskContext, document_id: str, settings: Settings, with_ocr: bool = False) -> dict:
    """``work``-Funktion fuer den Hintergrund-Vorgang ``reanalyze``."""
    outcome = await create_proposal(ctx, ctx.session_factory, document_id, settings, with_ocr=with_ocr)
    message = (
        "Änderungsvorschlag erstellt." if outcome.status == "proposed"
        else "Keine Änderungen gefunden."
    )
    return {
        "message": message,
        "status": outcome.status,
        "changes": len(outcome.changes),
        "ocr_note": outcome.ocr_note,
    }


# ---------------------------------------------------------------------------
# Uebernehmen / Verwerfen
# ---------------------------------------------------------------------------


async def _sync_tags(session: AsyncSession, doc: Document, target: set[str]) -> None:
    """Setzt die Tags des Dokuments auf ``target`` (normalisierte Namen)."""
    current = {t.name: t for t in doc.tags}
    drop_ids = [tag.id for name, tag in current.items() if name not in target]
    if drop_ids:
        await session.execute(
            delete(DocumentTag)
            .where(DocumentTag.document_id == doc.id)
            .where(DocumentTag.tag_id.in_(drop_ids))
        )
    for tag in await _get_or_create_tags(sorted(target - current.keys()), session):
        session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))


async def _sync_warranty(session: AsyncSession, doc: Document, new: WarrantyInfo | None) -> None:
    existing = doc.warranty_info
    if existing is None:
        if new is not None:
            session.add(new)
        return
    if new is None:
        await session.delete(existing)
        return
    end_changed = existing.warranty_end_date != new.warranty_end_date
    for field in WARRANTY_FIELDS:
        setattr(existing, field, getattr(new, field))
    if end_changed:
        existing.rearm_reminders()


async def _replace_questions(session: AsyncSession, doc: Document, plan: _Plan, accepted: set[str]) -> None:
    """Rueckfragen nur fuer uebernommene Felder (bzw. Freitext ohne Feld) ersetzen
    und den Review-Status daraus neu bestimmen; REVIEWED bleibt REVIEWED."""
    analysis = plan.analysis
    replaced = set(accepted)
    if SCOPE_FIELD in accepted:
        replaced.add("filing_scope")  # field_affected der Scope-Fragen

    kept = []
    for question in doc.review_questions:
        if question.is_answered:
            continue
        if question.field_affected is None or question.field_affected in replaced:
            await session.delete(question)
        else:
            kept.append(question)

    kept_texts = {q.question for q in kept}
    new_questions = []
    for entry in analysis.review_questions or []:
        question = _build_review_question(doc.id, entry)
        if question is None or question.question in kept_texts:
            continue
        if question.field_affected is not None and question.field_affected not in replaced:
            continue
        new_questions.append(question)
    if plan.scope_match is not None and SCOPE_FIELD in accepted and not any(
        q.field_affected == "filing_scope" for q in kept
    ):
        scope_question = build_scope_question(doc.id, plan.scope_match, analysis, plan.scopes)
        if scope_question is not None:
            new_questions.append(scope_question)
    for question in new_questions:
        session.add(question)

    if doc.review_status != ReviewStatus.REVIEWED:
        needs_review = analysis.needs_review or kept or new_questions
        doc.review_status = ReviewStatus.NEEDS_REVIEW if needs_review else ReviewStatus.OK


async def _prepare_scope_move(
    session: AsyncSession, doc: Document, match: ScopeMatch, archive_root: Path,
) -> list[FileMove]:
    """Kopiert die Datei in den Zielbereich (vor jeder DB-Aenderung).

    Raises:
        ProposalConflict: Datei fehlt oder ist nicht kopierbar — nichts geaendert.
    """
    if not Path(doc.file_path).is_file():
        raise ProposalConflict(MISSING_FILE_MESSAGE)
    move = plan_move(doc, match.scope_slug, archive_root, await known_scope_slugs(session))
    moves = [move] if move else []
    try:
        await copy_files(moves)
    except RelocationError as exc:
        raise ProposalConflict(str(exc)) from exc
    return moves


async def apply_proposal(
    session: AsyncSession,
    document_id: str,
    settings: Settings,
    fields: set[str] | None = None,
) -> list[str]:
    """Uebernimmt die angenommenen Felder des offenen Vorschlags und committet.

    ``fields=None`` nimmt alle aktuellen Aenderungen (:func:`compute_changes`)
    an; Felder, die inzwischen keine Aenderung mehr sind (z.B. vom Benutzer
    bearbeitet), werden uebersprungen. Der Vorschlag ist danach erledigt.

    Committet selbst: die Datei eines Bereichswechsels wird vor dem Commit
    kopiert und erst danach am alten Ort geloescht (rollback-sicher).

    Returns:
        Die uebernommenen Felder (Reihenfolge = PROPOSAL_FIELDS).

    Raises:
        ProposalNotFound: kein offener Vorschlag.
        ProposalConflict: Datei fuer den Bereichswechsel fehlt — nichts geaendert.
    """
    doc = await load_document_for_review(session, document_id)
    proposal = await get_open_proposal(session, document_id) if doc is not None else None
    if proposal is None:
        raise ProposalNotFound(NO_PROPOSAL_MESSAGE)

    plan = await _plan_changes(session, doc, proposal)
    offered = [planned.change.field for planned in plan.changes]
    applied = [field for field in offered if fields is None or field in fields]
    rejected = [field for field in offered if field not in applied]
    accepted = {planned.change.field: planned.value for planned in plan.changes if planned.change.field in applied}

    archive_root = Path(settings.ARCHIVE_DIR)
    moves: list[FileMove] = []
    if SCOPE_FIELD in accepted:
        moves = await _prepare_scope_move(session, doc, accepted[SCOPE_FIELD], archive_root)

    try:
        for field in REANALYZED_FIELDS:
            if field in accepted:
                setattr(doc, field, accepted[field])
        if "tags" in accepted:
            await _sync_tags(session, doc, accepted["tags"])
        if "warranty" in accepted:
            await _sync_warranty(session, doc, accepted["warranty"])
        if SCOPE_FIELD in accepted:
            doc.filing_scope_id = accepted[SCOPE_FIELD].scope_id
            if moves:
                doc.file_path = str(moves[0].target)
        if "ocr_text" in accepted:
            doc.ocr_text, doc.ocr_confidence = accepted["ocr_text"]
        if applied:
            doc.ai_confidence = plan.analysis.confidence
            ensure_tax_year(doc)
            await _replace_questions(session, doc, plan, set(applied))

        doc.analysis_model = proposal.model
        await session.delete(proposal)
        session.add(AuditLog(
            document_id=doc.id,
            action=AuditAction.REANALYZED,
            details=json.dumps(
                {"applied": applied, "rejected": rejected, "model": proposal.model},
                ensure_ascii=False,
            ),
        ))
        if applied:
            await reindex_document_fts(session, doc.id)
        await session.commit()
    except Exception:
        await discard_copies(moves)
        await session.rollback()
        raise
    await remove_sources(moves, archive_root)

    logger.info(
        "Vorschlag fuer Dokument %s uebernommen: %s, abgelehnt: %s", document_id, applied, rejected,
    )
    if applied:
        # Post-commit, best effort (Fehler loggt der Helfer selbst). Gleiche
        # Engine wie die Session des Aufrufers.
        session_factory = async_sessionmaker(session.bind, class_=AsyncSession, expire_on_commit=False)
        await vectorize_service.revectorize_document_safe(document_id, settings, session_factory)
    return applied


async def discard_proposal(session: AsyncSession, document_id: str) -> bool:
    """Verwirft den offenen Vorschlag; das Modell gilt trotzdem als geprueft.

    Committet nicht (der Aufrufer committet). ``False``: kein offener Vorschlag.
    """
    proposal = await get_open_proposal(session, document_id)
    doc = await load_document_for_review(session, document_id) if proposal is not None else None
    if proposal is None or doc is None:
        return False
    doc.analysis_model = proposal.model
    await session.delete(proposal)
    session.add(AuditLog(
        document_id=doc.id,
        action=AuditAction.REANALYZED,
        details=json.dumps({"discarded": True}),
    ))
    return True
