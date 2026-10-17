"""Steuerjahr ableiten, wenn ein Dokument steuerrelevant ist.

Alle Steuer-Abfragen (Jahresliste, Zusammenfassung, Export) filtern auf
``Document.tax_year``. Wird ``tax_relevant`` manuell gesetzt (Liste, Detail,
Rueckfrage, Re-Analyse) oder liefert das LLM kein Jahr, bliebe ``tax_year``
NULL und der Beleg tauchte nie im Steuerpaket auf.
"""

from datetime import date, datetime

from sqlalchemy import inspect

from app.models.document import Document

MIN_TAX_YEAR = 2000


def derive_tax_year(document_date: date | None, created_at: datetime | None) -> int:
    """Belegdatum-Jahr, sonst Erfassungsjahr, begrenzt auf 2000..heute+1."""
    if document_date is not None:
        year = document_date.year
    elif created_at is not None:
        year = created_at.year
    else:
        year = date.today().year
    return max(MIN_TAX_YEAR, min(year, date.today().year + 1))


def ensure_tax_year(doc: Document) -> None:
    """Setzt ``tax_year``, wenn das Dokument steuerrelevant ist und keins hat.

    Ein explizit gesetztes Jahr wird nie ueberschrieben.
    """
    if doc.tax_relevant and doc.tax_year is None:
        # created_at ist ein Server-Default: nach einem Flush ggf. noch nicht
        # geladen — kein Lazy-Load im async-Kontext (MissingGreenlet) ausloesen.
        created_at = None if "created_at" in inspect(doc).unloaded else doc.created_at
        doc.tax_year = derive_tax_year(doc.document_date, created_at)
