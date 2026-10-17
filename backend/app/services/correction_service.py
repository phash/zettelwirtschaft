"""Lerneffekt aus Benutzer-Korrekturen (CorrectionMapping).

- :func:`record_correction` merkt sich jede Korrektur ("KI-Wert" -> "Benutzer-Wert").
  Ab 2 Vorkommen landen sie als Few-Shot-Beispiele im LLM-Prompt
  (analysis_service._load_correction_examples).
- Ab :data:`AUTO_APPLY_THRESHOLD` gleichen Korrekturen wird die Zuordnung
  ``auto_apply`` und :func:`apply_auto_corrections` ersetzt den KI-Wert nach
  der LLM-Analyse deterministisch — vor der Archivierung bzw. Uebernahme in
  der Re-Analyse.

Ablagebereich-Korrekturen werden mit Bereichs-NAMEN gespeichert (nicht mit
UUIDs): die Namen sind das, was das LLM ausgibt und im Prompt versteht.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.correction_mapping import CorrectionMapping

logger = logging.getLogger(__name__)

AUTO_APPLY_THRESHOLD = 3

# Dokumentfeld (CorrectionMapping.field) -> Attribut im AnalysisResult.
# Nur Felder, deren KI-Wert sich 1:1 ersetzen laesst; Betrag/Datum/Typ sind
# dokumentspezifisch und werden bewusst nicht automatisch korrigiert.
AUTO_APPLY_FIELDS: dict[str, str] = {
    "title": "title",
    "issuer": "sender",
    "recipient": "recipient",
    "reference_number": "reference_number",
    "summary": "summary",
    "filing_scope": "filing_scope",
}


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


async def record_correction(
    session: AsyncSession, field: str, original: str, corrected: str
) -> None:
    """Speichert oder zaehlt eine Korrektur hoch (ab 3x: auto_apply)."""
    if not original or not corrected or _normalize(original) == _normalize(corrected):
        return
    result = await session.execute(
        select(CorrectionMapping)
        .where(CorrectionMapping.field == field)
        .where(CorrectionMapping.original_value == original)
        .where(CorrectionMapping.corrected_value == corrected)
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        mapping.occurrence_count += 1
        if mapping.occurrence_count >= AUTO_APPLY_THRESHOLD:
            mapping.auto_apply = True
    else:
        session.add(CorrectionMapping(
            field=field,
            original_value=original,
            corrected_value=corrected,
        ))


async def apply_auto_corrections(session: AsyncSession, analysis) -> list[str]:
    """Wendet ``auto_apply``-Korrekturen auf ein AnalysisResult an (in place).

    Ein Feld wird ersetzt, wenn der KI-Wert (Whitespace/Gross-Klein egal) dem
    ``original_value`` einer auto_apply-Zuordnung entspricht. Bei mehreren
    Zuordnungen fuer denselben Wert gewinnt die haeufigste. Ein korrigierter
    Ablagebereich gilt als sicher (Konfidenz 1.0) — die Keyword-Zuordnung hat
    trotzdem weiter Vorrang.

    Returns:
        Liste der korrigierten Felder (Dokumentfeld-Namen).
    """
    if analysis is None or getattr(analysis, "llm_error", None):
        return []
    result = await session.execute(
        select(CorrectionMapping)
        .where(CorrectionMapping.auto_apply.is_(True))
        .where(CorrectionMapping.field.in_(AUTO_APPLY_FIELDS))
        .order_by(CorrectionMapping.occurrence_count.desc())
    )
    applied: list[str] = []
    for mapping in result.scalars().all():
        attr = AUTO_APPLY_FIELDS[mapping.field]
        if mapping.field in applied:
            continue
        current = getattr(analysis, attr, None)
        if not isinstance(current, str) or _normalize(current) != _normalize(mapping.original_value):
            continue
        setattr(analysis, attr, mapping.corrected_value)
        if mapping.field == "filing_scope":
            analysis.filing_scope_confidence = 1.0
        applied.append(mapping.field)
        logger.info(
            "Auto-Korrektur angewendet: %s '%s' -> '%s' (%dx korrigiert)",
            mapping.field, mapping.original_value, mapping.corrected_value,
            mapping.occurrence_count,
        )
    return applied
