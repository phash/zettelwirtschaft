"""Antworten auf KI-Rueckfragen in Dokumentfelder uebersetzen.

Jede Antwort wird erst geparst und angewendet; ist das nicht moeglich, wirft
:func:`apply_answer` einen :class:`AnswerError` mit deutscher Meldung (-> 422)
und die Frage bleibt offen. Vorher wurden z.B. "1.234,56", "12,50 €" oder
deutsche Typ-Bezeichnungen still verworfen, die Frage aber als beantwortet
markiert.
"""

import logging
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.date_utils import parse_date_lenient
from app.core.normalize import document_type_from_label, enum_value
from app.models.document import Document, DocumentType
from app.models.filing_scope import FilingScope
from app.services.correction_service import record_correction
from app.services.filing_scope_service import create_scope, find_scope_by_name
from app.services.tax_year import ensure_tax_year

logger = logging.getLogger(__name__)

NEW_SCOPE_PREFIX = "NEU:"
TEXT_FIELDS = {"title": 500, "issuer": 500, "recipient": 500, "reference_number": 200, "summary": None}
# Numeric(12, 2): hoechstens 10 Vorkommastellen
_MAX_AMOUNT = Decimal("9999999999.99")
_CENT = Decimal("0.01")
_YES = {"ja", "j", "yes", "y", "true", "1", "wahr"}
_NO = {"nein", "n", "no", "false", "0", "falsch"}


class AnswerError(ValueError):
    """Antwort laesst sich nicht auf das Feld anwenden (Meldung fuer die UI)."""


@dataclass
class AnswerOutcome:
    # Dokumentfeld -> {"old", "new"} (fuer AuditLog UPDATED)
    changes: dict[str, dict] = field(default_factory=dict)
    # Neuer Ablagebereich — Zuweisung + Dateiumzug macht der Aufrufer
    new_scope: FilingScope | None = None


def parse_user_amount(text: str) -> Decimal:
    """Deutsche und englische Betragsangaben -> Decimal (2 Nachkommastellen).

    "1.234,56", "12,50 €", "EUR 7", "1,234.56", "-3,10" werden akzeptiert;
    das letzte Trennzeichen ist das Dezimaltrennzeichen, wenn danach 1-2
    Ziffern folgen. NaN/Infinity und Werte ausserhalb der Spalte werden
    abgelehnt.
    """
    cleaned = re.sub(r"(?i)\s*(€|eur(o)?)\s*", "", text).replace(" ", "").replace(" ", "")
    # mindestens eine Ziffer — "." oder "," allein ist kein Betrag (sonst 0,00)
    if not re.fullmatch(r"[-+]?[\d.,]*\d[\d.,]*", cleaned or ""):
        raise AnswerError(f"„{text}“ ist kein gültiger Betrag (Beispiel: 1.234,56).")
    sign = "-" if cleaned.startswith("-") else ""
    digits = cleaned.lstrip("+-")
    last_sep = max(digits.rfind(","), digits.rfind("."))
    if last_sep != -1 and 1 <= len(digits) - last_sep - 1 <= 2:
        integer, fraction = digits[:last_sep], digits[last_sep + 1:]
    else:
        integer, fraction = digits, ""
    integer = integer.replace(",", "").replace(".", "")
    try:
        value = Decimal(f"{sign}{integer or '0'}.{fraction or '0'}")
    except InvalidOperation as exc:
        raise AnswerError(f"„{text}“ ist kein gültiger Betrag (Beispiel: 1.234,56).") from exc
    if not value.is_finite() or abs(value) > _MAX_AMOUNT:
        raise AnswerError(f"„{text}“ ist kein gültiger Betrag.")
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def parse_yes_no(text: str) -> bool:
    value = text.strip().lower()
    if value in _YES:
        return True
    if value in _NO:
        return False
    raise AnswerError("Bitte mit „Ja“ oder „Nein“ antworten.")


def parse_document_type(text: str) -> DocumentType:
    """Enum-Wert/-Name oder deutscher Anzeigename (beliebige Schreibweise)."""
    value = document_type_from_label(text)
    if value is None:
        raise AnswerError(f"„{text}“ ist kein bekannter Dokumenttyp.")
    return DocumentType(value)


def _set(doc: Document, outcome: AnswerOutcome, name: str, value) -> None:
    old = getattr(doc, name)
    if old != value:
        outcome.changes[name] = {"old": str(enum_value(old)), "new": str(enum_value(value))}
        setattr(doc, name, value)


async def apply_answer(
    session: AsyncSession, doc: Document, field_name: str | None, answer: str,
    suggested_answers: str | None = None,
) -> AnswerOutcome:
    """Wendet die Antwort auf ``doc`` an (ohne Commit).

    Raises:
        AnswerError: Antwort passt nicht zum Feld — nichts wurde geaendert.
    """
    outcome = AnswerOutcome()
    if not field_name:
        return outcome

    if field_name in TEXT_FIELDS:
        max_len = TEXT_FIELDS[field_name]
        if max_len and len(answer) > max_len:
            raise AnswerError(f"Die Antwort ist zu lang (höchstens {max_len} Zeichen).")
        old_value = getattr(doc, field_name)
        _set(doc, outcome, field_name, answer)
        if old_value and field_name in outcome.changes:
            await record_correction(session, field_name, str(old_value), answer)

    elif field_name == "amount":
        _set(doc, outcome, "amount", parse_user_amount(answer))

    elif field_name == "document_date":
        parsed = parse_date_lenient(answer)
        if parsed is None:
            raise AnswerError(f"„{answer}“ ist kein gültiges Datum (Beispiel: 15.01.2024).")
        _set(doc, outcome, "document_date", parsed)

    elif field_name == "document_type":
        _set(doc, outcome, "document_type", parse_document_type(answer))

    elif field_name == "tax_relevant":
        _set(doc, outcome, "tax_relevant", parse_yes_no(answer))
        # Abgeleitetes Jahr ist keine Benutzer-Eingabe: bewusst nicht in
        # outcome.changes (AuditLog), damit eine Re-Analyse es noch aendern
        # darf — wie bei PATCH /documents.
        ensure_tax_year(doc)

    elif field_name == "filing_scope":
        outcome.new_scope = await _resolve_scope_answer(session, doc, answer, suggested_answers)

    else:
        logger.info("Rueckfrage zu unbekanntem Feld '%s' — nur als beantwortet markiert", field_name)

    return outcome


def _suggested_new_scope(suggested_answers: str | None) -> str | None:
    """Name aus der Option "NEU: <Name>" der Rueckfrage (archive_service)."""
    for option in (suggested_answers or "").split("|"):
        option = option.strip()
        if option.upper().startswith(NEW_SCOPE_PREFIX):
            return option[len(NEW_SCOPE_PREFIX):].strip() or None
    return None


async def _resolve_scope_answer(
    session: AsyncSession, doc: Document, answer: str, suggested_answers: str | None = None,
) -> FilingScope | None:
    # Die Vorschlags-Frage lautet "... Soll dieser erstellt werden?" — ein
    # getipptes "Ja"/"Nein" ist dort die natuerliche Antwort. Ein Bereich, der
    # tatsaechlich so heisst, hat Vorrang.
    value = answer.strip().lower()
    suggestion = _suggested_new_scope(suggested_answers)
    if suggestion and value in _YES | _NO and await find_scope_by_name(session, answer) is None:
        if value in _NO:
            return None
        answer = f"{NEW_SCOPE_PREFIX} {suggestion}"

    if answer.upper().startswith(NEW_SCOPE_PREFIX):
        name = answer[len(NEW_SCOPE_PREFIX):].strip()
        if not name:
            raise AnswerError("Bitte einen Namen für den neuen Ablagebereich angeben.")
        if len(name) > 200:
            raise AnswerError("Der Name ist zu lang (höchstens 200 Zeichen).")
        scope = await find_scope_by_name(session, name) or await create_scope(session, name)
    else:
        scope = await find_scope_by_name(session, answer)
        if scope is None:
            raise AnswerError(f"Ablagebereich „{answer}“ existiert nicht.")

    if scope.id == doc.filing_scope_id:
        return None
    if doc.filing_scope is not None:
        # Namen statt UUIDs: die Namen gibt das LLM aus und versteht sie im
        # Few-Shot-Prompt.
        await record_correction(session, "filing_scope", doc.filing_scope.name, scope.name)
    return scope
