"""Normalisierung fachlicher Werte (Enums, Steuerkategorie, Waehrung).

Single Source fuer LLM-Analyse, Archivierung, Benutzer-Updates und RAG-Texte:
- ``enum_value`` liefert den gespeicherten Wert statt ``str(Enum)`` — seit
  Python 3.12 ergibt ``f"{TaxCategory.WERBUNGSKOSTEN}"`` sonst
  "TaxCategory.WERBUNGSKOSTEN".
- ``normalize_tax_category`` / ``normalize_currency`` fangen typische
  LLM-Ausreisser ab ("A | B", "€", "Euro"), die sonst ungefiltert in der DB
  landen (und z.B. ``Intl.NumberFormat`` im Frontend crashen lassen).
"""

import enum
import re

from app.models.document import DocumentType, TaxCategory

VALID_DOCUMENT_TYPES: frozenset[str] = frozenset(t.value for t in DocumentType)
VALID_TAX_CATEGORIES: frozenset[str] = frozenset(c.value for c in TaxCategory)

# Deutsche Anzeigenamen der Dokumenttypen — identisch zu
# frontend/src/constants/documentTypes.js (Antwortvorschlaege in Rueckfragen).
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "RECHNUNG": "Rechnung",
    "QUITTUNG": "Quittung",
    "KAUFVERTRAG": "Kaufvertrag",
    "GARANTIESCHEIN": "Garantieschein",
    "VERSICHERUNGSPOLICE": "Versicherungspolice",
    "KONTOAUSZUG": "Kontoauszug",
    "LOHNABRECHNUNG": "Lohnabrechnung",
    "STEUERBESCHEID": "Steuerbescheid",
    "MIETVERTRAG": "Mietvertrag",
    "HANDWERKER_RECHNUNG": "Handwerkerrechnung",
    "ARZTRECHNUNG": "Arztrechnung",
    "REZEPT": "Rezept",
    "AMTLICHES_SCHREIBEN": "Amtliches Schreiben",
    "BEDIENUNGSANLEITUNG": "Bedienungsanleitung",
    "SONSTIGES": "Sonstiges",
}

_CURRENCY_ALIASES = {
    "€": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "$": "USD",
    "US$": "USD",
    "DOLLAR": "USD",
    "£": "GBP",
    "PFUND": "GBP",
    "FR.": "CHF",
    "SFR": "CHF",
    "SFR.": "CHF",
    "FRANKEN": "CHF",
}
_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
DEFAULT_CURRENCY = "EUR"


def enum_value(value) -> str | None:
    """Gespeicherter Wert eines Enums (oder der Wert selbst), ``None`` bleibt ``None``."""
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def document_type_from_label(value: str | None) -> str | None:
    """Mappt Enum-Wert ODER deutschen Anzeigenamen ("Arztrechnung") auf den Enum-Wert."""
    if not value:
        return None
    text = value.strip()
    if text.upper() in VALID_DOCUMENT_TYPES:
        return text.upper()
    lowered = text.lower()
    for key, label in DOCUMENT_TYPE_LABELS.items():
        if label.lower() == lowered:
            return key
    return None


def normalize_tax_category(value) -> str | None:
    """Erster gueltiger Wert eines ggf. per "|" zusammengesetzten LLM-Werts.

    "Foo | Werbungskosten" -> "Werbungskosten"; nichts Gueltiges -> ``None``.
    Schutz gegen Prompt-Injection-induzierte Manipulation: nur Whitelist-Werte.
    """
    if value is None:
        return None
    if isinstance(value, TaxCategory):
        return value.value
    if not isinstance(value, str):
        return None
    for part in value.split("|"):
        candidate = part.strip()
        if candidate in VALID_TAX_CATEGORIES:
            return candidate
    return None


def normalize_currency(value) -> str | None:
    """ISO-4217-Code aus LLM-/Benutzer-Werten; ``None`` bleibt ``None``.

    Bekannte Symbole/Namen ("€", "Euro", "$") werden gemappt, dreistellige
    Codes uebernommen, alles andere faellt auf EUR zurueck.
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    text = _CURRENCY_ALIASES.get(text, text)
    if _ISO_CURRENCY_RE.match(text):
        return text
    return DEFAULT_CURRENCY
