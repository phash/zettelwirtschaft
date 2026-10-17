"""Gemeinsame Parser/Validatoren fuer Query-Parameter der Listen- und Such-Endpoints."""

import enum

from fastapi import HTTPException

from app.models.document import DocumentType


def empty_to_none(value):
    """BeforeValidator: leerer Query-Parameter (``?amount_min=``) gilt als nicht gesetzt."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def parse_enum_list[E: enum.Enum](value: str | None, enum_cls: type[E], label: str) -> list[E]:
    """Parst eine kommagetrennte Enum-Liste (``RECHNUNG,QUITTUNG``, ``pending,failed``).

    Gross-/Kleinschreibung egal, leere Eintraege ignoriert, Duplikate entfernt
    (Reihenfolge bleibt). Unbekannte Werte -> HTTP 422 statt stiller
    Null-Treffer bzw. still ignoriertem Filter.
    """
    if not value:
        return []
    result: list[E] = []
    invalid: list[str] = []
    for raw in value.split(","):
        name = raw.strip()
        if not name:
            continue
        try:
            member = enum_cls(name.upper())
        except ValueError:
            invalid.append(name)
            continue
        if member not in result:
            result.append(member)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unbekannter {label}: {', '.join(invalid)}",
        )
    return result


def parse_document_types(value: str | None) -> list[DocumentType]:
    """Kommagetrennte Dokumenttyp-Liste (Dokumentliste, Suche)."""
    return parse_enum_list(value, DocumentType, "Dokumenttyp")
