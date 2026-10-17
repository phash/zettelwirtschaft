"""Tolerantes Datums-Parsing fuer LLM-Ausgaben und Benutzereingaben.

Das LLM liefert trotz Schema-Vorgabe gelegentlich deutsche Datumsformate
("02.06.2025", "2. Juni 2025"). Frueher akzeptierte der Archiv-Pfad nur ISO —
alles andere wurde still verworfen und das Dokument unter dem aktuellen Monat
abgelegt. Single Source fuer Archivierung, Garantie-Daten und Review-Antworten.
"""

import re
from datetime import date, datetime

_MONTHS = {
    "januar": 1, "jänner": 1, "jan": 1,
    "februar": 2, "feb": 2,
    "märz": 3, "maerz": 3, "mär": 3, "mrz": 3,
    "april": 4, "apr": 4,
    "mai": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}

_NUMERIC_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y", "%d/%m/%y")

# "2. Juni 2025", "02. Jun. 2025", "2 Juni 2025"
_MONTH_NAME_RE = re.compile(r"^(\d{1,2})\.?\s+([a-zäöü]+)\.?\s+(\d{4})$", re.IGNORECASE)


def parse_date_lenient(value: str | date | None) -> date | None:
    """Parst ISO (YYYY-MM-DD, auch mit Zeitanteil), dd.mm.yyyy, d.m.yyyy, dd.mm.yy,
    dd/mm/yyyy und "d. <Monatsname> yyyy". Gibt ``None`` zurueck, wenn nichts passt.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass

    for fmt in _NUMERIC_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    match = _MONTH_NAME_RE.match(text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day))
            except ValueError:
                return None
    return None


def add_months(start: date, months: int) -> date:
    """Addiert Monate und klemmt den Tag auf das Monatsende (31.01. + 1 -> 28./29.02.)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # Letzter Tag des Zielmonats: erster Tag des Folgemonats minus 1
    if month == 12:
        last_day = 31
    else:
        last_day = (date(year, month + 1, 1) - date(year, month, 1)).days
    return date(year, month, min(start.day, last_day))
