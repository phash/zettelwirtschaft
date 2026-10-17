"""Steuerpaket-Export: ZIP mit kategorisierten Dokumenten, Übersicht-PDF und CSV.

Formate richten sich an deutsche Empfänger (Steuerberater, deutsches Excel):
Beträge "1.234,50", CSV ohne Tausenderpunkt ("1234,50", Trenner ';'), Datum
TT.MM.JJJJ. Summen enthalten nur EUR-Belege — Fremdwährungen werden nicht
umgerechnet, sondern als Warnung aufgelistet.
"""

import asyncio
import csv
import io
import logging
import os
import tempfile
import zipfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.normalize import DOCUMENT_TYPE_LABELS, enum_value, normalize_currency
from app.core.text_utils import transliterate_ascii
from app.models.document import Document, DocumentStatus, ReviewStatus, TaxCategory

logger = logging.getLogger(__name__)

EXPORT_CURRENCY = "EUR"
MISSING_FILE_NOTE = "Datei fehlt"
MISSING_FILES_LIST_NAME = "FEHLENDE_DATEIEN.txt"
_CENT = Decimal("0.01")

TAX_CATEGORY_LABELS: dict[str, str] = {
    TaxCategory.WERBUNGSKOSTEN: "Werbungskosten",
    TaxCategory.SONDERAUSGABEN: "Sonderausgaben",
    TaxCategory.AUSSERGEWOEHNLICHE_BELASTUNGEN: "Außergewöhnliche Belastungen",
    TaxCategory.HANDWERKERLEISTUNGEN: "Handwerkerleistungen",
    TaxCategory.HAUSHALTSNAHE_DIENSTLEISTUNGEN: "Haushaltsnahe Dienstleistungen",
    TaxCategory.VORSORGEAUFWENDUNGEN: "Vorsorgeaufwendungen",
    TaxCategory.KEINE: "Sonstige steuerrelevante Belege",
}

class EmptyTaxExportError(Exception):
    """Keine steuerrelevanten Dokumente im gewählten Jahr/Bereich."""


# --- Formatierung -----------------------------------------------------------


def format_amount_de(value: Decimal, thousands: bool = True) -> str:
    """Decimal -> deutsches Zahlenformat ('1.234,50'; CSV: '1234,50')."""
    quantized = Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)
    text = f"{quantized:,.2f}" if thousands else f"{quantized:.2f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_date_de(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def count_label(count: int) -> str:
    return f"{count} Beleg" if count == 1 else f"{count} Belege"


def export_currency(currency: str | None) -> str:
    return normalize_currency(currency) or EXPORT_CURRENCY


def category_label(category: str) -> str:
    return TAX_CATEGORY_LABELS.get(category, str(category))


def document_type_label(document_type: str | None) -> str:
    value = enum_value(document_type)
    if not value:
        return ""
    return DOCUMENT_TYPE_LABELS.get(value, value)


def _safe_filename(text: str) -> str:
    """Bereinigt Text für Dateinamen (Umlaute -> ae/oe/ue/ss)."""
    if not text:
        return ""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ")
    result = "".join(c if c in keep else "_" for c in transliterate_ascii(text))
    return "_".join(result.replace("_", " ").split())[:80]


def _category_folder_name(idx: int, category: str) -> str:
    return f"{idx:02d}_{_safe_filename(category_label(category))}"


def _non_eur_warning(title: str, amount: Decimal | None, currency: str) -> str:
    amount_text = f"{format_amount_de(amount)} {currency}" if amount is not None else currency
    return (
        f"Dokument '{title}' lautet auf {amount_text} und ist nicht in der "
        f"EUR-Summe enthalten (keine Währungsumrechnung)."
    )


# --- Abfragen ---------------------------------------------------------------


def _tax_documents_query(year: int, filing_scope_id: str | None) -> Select:
    stmt = (
        select(Document)
        .where(Document.tax_relevant.is_(True))
        .where(Document.tax_year == year)
        .where(Document.status != DocumentStatus.DELETED)
    )
    if filing_scope_id:
        stmt = stmt.where(Document.filing_scope_id == filing_scope_id)
    return stmt


async def get_tax_summary(session: AsyncSession, year: int, filing_scope_id: str | None = None) -> dict:
    """Berechnet die Steuer-Zusammenfassung für ein Jahr (Summen nur EUR)."""
    result = await session.execute(_tax_documents_query(year, filing_scope_id))
    docs = result.scalars().all()

    # H-ARCH-2: Aggregation mit Decimal statt float — sonst sind Summen von
    # 1000 Belegen ungenau (IEEE-754-Drift).
    categories: dict[str, dict] = {}
    warnings: list[str] = []
    non_eur: list[dict] = []
    total_amount = Decimal("0")

    for doc in docs:
        cat = doc.tax_category or TaxCategory.KEINE
        bucket = categories.setdefault(cat, {"count": 0, "total": Decimal("0")})
        bucket["count"] += 1
        currency = export_currency(doc.currency)

        if doc.amount is None:
            warnings.append(f"Dokument '{doc.title}' hat keinen Betrag.")
        elif currency == EXPORT_CURRENCY:
            bucket["total"] += doc.amount
            total_amount += doc.amount
        else:
            non_eur.append({
                "id": doc.id,
                "title": doc.title,
                "document_date": doc.document_date,
                "amount": doc.amount,
                "currency": currency,
            })
            warnings.append(_non_eur_warning(doc.title, doc.amount, currency))

        if doc.review_status == ReviewStatus.NEEDS_REVIEW:
            warnings.append(f"Dokument '{doc.title}' hat offene Rückfragen.")

    cat_summaries = [
        {
            "category": cat_key,
            "label": category_label(cat_key),
            "document_count": categories[cat_key]["count"],
            "total_amount": categories[cat_key]["total"].quantize(_CENT, rounding=ROUND_HALF_UP),
        }
        for cat_key in TaxCategory
        if cat_key in categories
    ]

    return {
        "year": year,
        "total_documents": len(docs),
        "total_amount": total_amount.quantize(_CENT, rounding=ROUND_HALF_UP),
        "categories": cat_summaries,
        "non_eur_documents": non_eur,
        "warnings": warnings,
    }


def _missing_files_sync(entries: list[tuple[str, str]]) -> list[str]:
    return [title for title, path in entries if not Path(path).is_file()]


async def validate_export(session: AsyncSession, year: int, filing_scope_id: str | None = None) -> dict:
    """Prüft, ob der Export bereit ist, und sammelt Warnungen."""
    summary = await get_tax_summary(session, year, filing_scope_id=filing_scope_id)
    result = await session.execute(
        _tax_documents_query(year, filing_scope_id).with_only_columns(Document.title, Document.file_path)
    )
    missing = await asyncio.to_thread(_missing_files_sync, [tuple(r) for r in result.all()])
    warnings = list(summary["warnings"])
    warnings.extend(
        f"Datei für Dokument '{title}' fehlt im Archiv und kann nicht exportiert werden."
        for title in missing
    )
    return {
        "year": year,
        "total_documents": summary["total_documents"],
        "warnings": warnings,
        "ready": summary["total_documents"] > 0,
    }


async def create_tax_export_zip(
    session: AsyncSession,
    year: int,
    include_pdf: bool = True,
    include_csv: bool = True,
    filing_scope_id: str | None = None,
) -> Path:
    """Erstellt das Steuerpaket als temporäre ZIP-Datei und gibt deren Pfad zurück.

    Der Aufrufer muss die Datei nach dem Ausliefern löschen. Lädt die
    Metadaten async und baut ZIP + PDF in einem Thread (ZIP_DEFLATED bei
    großen Archiven würde sonst den Event-Loop blockieren). Die ZIP-Datei
    landet direkt auf der Platte statt komplett im Speicher.

    Raises:
        EmptyTaxExportError: keine steuerrelevanten Dokumente.
    """
    stmt = _tax_documents_query(year, filing_scope_id).order_by(
        Document.tax_category, Document.document_date
    )
    docs = (await session.execute(stmt)).scalars().all()
    if not docs:
        raise EmptyTaxExportError(year)

    # Plain-Dicts — ORM-Objekte duerfen den Thread nicht verlassen
    # (Lazy-Loading + Session-Bindung).
    docs_data = [
        {
            "id": doc.id,
            "tax_category": doc.tax_category or TaxCategory.KEINE,
            "title": doc.title,
            "issuer": doc.issuer,
            "amount": doc.amount,
            "currency": export_currency(doc.currency),
            "document_date": doc.document_date,
            "file_path": doc.file_path,
            "file_type": doc.file_type,
            "document_type": doc.document_type,
        }
        for doc in docs
    ]

    fd, tmp_name = tempfile.mkstemp(prefix=f"Steuerpaket_{year}_", suffix=".zip")
    os.close(fd)
    target = Path(tmp_name)
    try:
        await asyncio.to_thread(_build_zip_sync, docs_data, year, include_pdf, include_csv, target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


# --- ZIP-Aufbau (sync, nur in to_thread) ------------------------------------


def _entry_name(doc: dict, used_names: set[str]) -> str:
    """Dateiname im ZIP: Datum_Titel_Aussteller_Betrag.ext — eindeutig pro Ordner.

    Zwei identische Belege (z.B. Parkscheine) bekaemen sonst denselben Namen
    und einer ginge beim Entpacken verloren.
    """
    parts = []
    if doc["document_date"]:
        parts.append(doc["document_date"].strftime("%Y-%m-%d"))
    parts.append(_safe_filename(doc["title"]) or "Dokument")
    if doc["issuer"]:
        parts.append(_safe_filename(doc["issuer"]))
    if doc["amount"] is not None:
        parts.append(f"{format_amount_de(doc['amount'], thousands=False)}{doc['currency']}")
    stem = "_".join(p for p in parts if p)
    ext = f".{doc['file_type']}"
    name = f"{stem}{ext}"
    if name.lower() in used_names:
        name = f"{stem}_{doc['id'][:8]}{ext}"
    used_names.add(name.lower())
    return name


def _write_csv(fieldnames: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")


_CATEGORY_CSV_FIELDS = ["Datum", "Titel", "Aussteller", "Betrag", "Währung", "Dokumenttyp", "Dateiname", "Hinweis"]
_TOTAL_CSV_FIELDS = ["Kategorie"] + _CATEGORY_CSV_FIELDS


def _build_zip_sync(
    docs_data: list[dict],
    year: int,
    include_pdf: bool,
    include_csv: bool,
    target: Path,
) -> None:
    by_category: dict[str, list[dict]] = {}
    for doc in docs_data:
        by_category.setdefault(doc["tax_category"], []).append(doc)

    prefix = f"Steuerpaket_{year}"
    all_rows: list[dict] = []
    missing: list[str] = []

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        cat_index = 1
        for cat_key in TaxCategory:
            cat_docs = by_category.get(cat_key)
            if not cat_docs:
                continue
            folder = _category_folder_name(cat_index, cat_key)
            cat_index += 1
            used_names: set[str] = set()
            cat_rows: list[dict] = []

            for doc in cat_docs:
                fname = _entry_name(doc, used_names)
                file_path = Path(doc["file_path"])
                note = ""
                if file_path.is_file():
                    zf.write(file_path, f"{prefix}/{folder}/{fname}")
                else:
                    logger.warning("Datei nicht gefunden: %s", file_path)
                    note = MISSING_FILE_NOTE
                    missing.append(f"{folder}/{fname} — {doc['title']}")

                row = {
                    "Kategorie": category_label(cat_key),
                    "Datum": format_date_de(doc["document_date"]),
                    "Titel": doc["title"],
                    "Aussteller": doc["issuer"] or "",
                    "Betrag": (
                        format_amount_de(doc["amount"], thousands=False)
                        if doc["amount"] is not None else ""
                    ),
                    "Währung": doc["currency"],
                    "Dokumenttyp": document_type_label(doc["document_type"]),
                    "Dateiname": fname,
                    "Hinweis": note,
                    # nur fuer das PDF
                    "_amount": doc["amount"],
                }
                cat_rows.append(row)
            all_rows.extend(cat_rows)

            if include_csv:
                zf.writestr(f"{prefix}/{folder}/uebersicht.csv", _write_csv(_CATEGORY_CSV_FIELDS, cat_rows))

        if include_csv and all_rows:
            zf.writestr(f"{prefix}/Gesamtuebersicht.csv", _write_csv(_TOTAL_CSV_FIELDS, all_rows))

        if missing:
            text = (
                "Folgende Belege konnten nicht exportiert werden, weil die Datei "
                "im Archiv fehlt:\r\n\r\n" + "\r\n".join(missing) + "\r\n"
            )
            zf.writestr(f"{prefix}/{MISSING_FILES_LIST_NAME}", text.encode("utf-8-sig"))

        if include_pdf:
            pdf_bytes = _create_overview_pdf(year, all_rows, missing)
            if pdf_bytes:
                zf.writestr(f"{prefix}/Uebersicht.pdf", pdf_bytes)


def _eur_total(rows: list[dict]) -> Decimal:
    return sum(
        (r["_amount"] for r in rows if r["_amount"] is not None and r["Währung"] == EXPORT_CURRENCY),
        Decimal("0"),
    )


def _create_overview_pdf(year: int, all_rows: list[dict], missing: list[str]) -> bytes | None:
    """Erstellt das Übersicht-PDF mit reportlab."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        logger.warning("reportlab nicht installiert, PDF-Erstellung übersprungen")
        return None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    warning_style = ParagraphStyle(
        "Warning", parent=styles["Normal"], textColor=colors.HexColor("#B45309"), spaceAfter=4,
    )
    elements = []

    title_style = ParagraphStyle("CustomTitle", parent=styles["Title"], fontSize=18, spaceAfter=12)
    elements.append(Paragraph(f"Steuerbelege {year}", title_style))
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph(
        f"Anzahl: {count_label(len(all_rows))} | "
        f"Gesamtbetrag: {format_amount_de(_eur_total(all_rows))} {EXPORT_CURRENCY}",
        styles["Normal"],
    ))

    non_eur = [r for r in all_rows if r["Währung"] != EXPORT_CURRENCY and r["_amount"] is not None]
    if non_eur:
        listed = ", ".join(
            escape(f"{r['Titel']} ({format_amount_de(r['_amount'])} {r['Währung']})") for r in non_eur
        )
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(Paragraph(
            "Hinweis: Folgende Belege lauten nicht auf EUR und sind in den Summen "
            f"nicht enthalten (keine Währungsumrechnung): {listed}",
            warning_style,
        ))
    if missing:
        elements.append(Paragraph(
            f"Hinweis: Für {count_label(len(missing))} fehlt die Datei im Archiv "
            f"(siehe {MISSING_FILES_LIST_NAME}).",
            warning_style,
        ))
    elements.append(Spacer(1, 0.5 * cm))

    rows_by_category: dict[str, list[dict]] = {}
    for row in all_rows:
        rows_by_category.setdefault(row["Kategorie"], []).append(row)

    for cat_label, cat_rows in rows_by_category.items():
        elements.append(Paragraph(
            escape(
                f"{cat_label} ({count_label(len(cat_rows))}, "
                f"{format_amount_de(_eur_total(cat_rows))} {EXPORT_CURRENCY})"
            ),
            styles["Heading2"],
        ))

        table_data = [["Datum", "Titel", "Aussteller", "Betrag"]]
        for r in cat_rows:
            amount = (
                f"{format_amount_de(r['_amount'])} {r['Währung']}" if r["_amount"] is not None else "-"
            )
            title = (r["Titel"] or "-")[:40]
            if r["Hinweis"]:
                title = f"{title[:28]} ({r['Hinweis']})"
            table_data.append([
                r["Datum"] or "-",
                title,
                (r["Aussteller"] or "-")[:30],
                amount,
            ])

        t = Table(table_data, colWidths=[2.5 * cm, 7.5 * cm, 4 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4F46E5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.5 * cm))

    elements.append(Spacer(1, 1 * cm))
    elements.append(Paragraph(
        f"Erstellt am {date.today().strftime('%d.%m.%Y')} mit Zettelwirtschaft",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8, textColor=colors.grey),
    ))

    doc.build(elements)
    return buf.getvalue()
