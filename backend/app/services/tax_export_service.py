"""Steuerpaket-Export: ZIP mit kategorisierten Dokumenten, Uebersicht-PDF und CSV."""

import csv
import io
import logging
import zipfile
from datetime import date
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.document import Document, DocumentStatus, TaxCategory

logger = logging.getLogger(__name__)

TAX_CATEGORY_LABELS: dict[str, str] = {
    TaxCategory.WERBUNGSKOSTEN: "Werbungskosten",
    TaxCategory.SONDERAUSGABEN: "Sonderausgaben",
    TaxCategory.AUSSERGEWOEHNLICHE_BELASTUNGEN: "Aussergewoehnliche Belastungen",
    TaxCategory.HANDWERKERLEISTUNGEN: "Handwerkerleistungen",
    TaxCategory.HAUSHALTSNAHE_DIENSTLEISTUNGEN: "Haushaltsnahe Dienstleistungen",
    TaxCategory.VORSORGEAUFWENDUNGEN: "Vorsorgeaufwendungen",
    TaxCategory.KEINE: "Sonstige steuerrelevante Belege",
}


def _safe_filename(text: str) -> str:
    """Bereinigt Text fuer Dateinamen."""
    if not text:
        return ""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ")
    result = "".join(c if c in keep else "_" for c in text)
    return "_".join(result.split())[:80]


def _category_folder_name(idx: int, category: str) -> str:
    label = TAX_CATEGORY_LABELS.get(category, category)
    return f"{idx:02d}_{_safe_filename(label)}"


async def get_tax_summary(session: AsyncSession, year: int, filing_scope_id: str | None = None) -> dict:
    """Berechnet Steuer-Zusammenfassung fuer ein Jahr."""
    stmt = (
        select(Document)
        .where(Document.tax_relevant.is_(True))
        .where(Document.tax_year == year)
        .where(Document.status != DocumentStatus.DELETED)
    )
    if filing_scope_id:
        stmt = stmt.where(Document.filing_scope_id == filing_scope_id)
    result = await session.execute(stmt)
    docs = result.scalars().all()

    categories: dict[str, dict] = {}
    warnings: list[str] = []

    for doc in docs:
        cat = doc.tax_category or TaxCategory.KEINE
        if cat not in categories:
            categories[cat] = {"count": 0, "total": 0.0}
        categories[cat]["count"] += 1
        if doc.amount is not None:
            categories[cat]["total"] += float(doc.amount)

        # Warnungen
        if doc.amount is None:
            warnings.append(f"Dokument '{doc.title}' hat keinen Betrag.")
        if doc.review_status == "NEEDS_REVIEW":
            warnings.append(f"Dokument '{doc.title}' hat offene Rueckfragen.")

    cat_summaries = []
    for cat_key in TaxCategory:
        if cat_key in categories:
            cat_summaries.append({
                "category": cat_key,
                "label": TAX_CATEGORY_LABELS.get(cat_key, cat_key),
                "document_count": categories[cat_key]["count"],
                "total_amount": round(categories[cat_key]["total"], 2),
            })

    return {
        "year": year,
        "total_documents": len(docs),
        "total_amount": round(sum(float(d.amount or 0) for d in docs), 2),
        "categories": cat_summaries,
        "warnings": warnings,
    }


async def validate_export(session: AsyncSession, year: int, filing_scope_id: str | None = None) -> dict:
    """Prueft ob Export bereit ist und gibt Warnungen zurueck."""
    summary = await get_tax_summary(session, year, filing_scope_id=filing_scope_id)
    return {
        "year": year,
        "total_documents": summary["total_documents"],
        "warnings": summary["warnings"],
        "ready": summary["total_documents"] > 0,
    }


async def create_tax_export_zip(
    session: AsyncSession,
    year: int,
    settings: Settings,
    include_pdf: bool = True,
    include_csv: bool = True,
    filing_scope_id: str | None = None,
) -> bytes:
    """Erstellt ZIP-Datei mit allen steuerrelevanten Dokumenten eines Jahres."""
    stmt = (
        select(Document)
        .where(Document.tax_relevant.is_(True))
        .where(Document.tax_year == year)
        .where(Document.status != DocumentStatus.DELETED)
        .order_by(Document.tax_category, Document.document_date)
    )
    if filing_scope_id:
        stmt = stmt.where(Document.filing_scope_id == filing_scope_id)
    result = await session.execute(stmt)
    docs = result.scalars().all()

    # Dokumente nach Kategorie gruppieren
    by_category: dict[str, list] = {}
    for doc in docs:
        cat = doc.tax_category or TaxCategory.KEINE
        by_category.setdefault(cat, []).append(doc)

    zip_buffer = io.BytesIO()
    prefix = f"Steuerpaket_{year}"

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        cat_index = 1
        all_rows: list[dict] = []

        for cat_key in TaxCategory:
            if cat_key not in by_category:
                continue
            cat_docs = by_category[cat_key]
            folder = _category_folder_name(cat_index, cat_key)
            cat_index += 1

            for doc in cat_docs:
                # Dateiname: Datum_Beschreibung_Aussteller_Betrag.ext
                parts = []
                if doc.document_date:
                    parts.append(doc.document_date.strftime("%Y-%m-%d"))
                parts.append(_safe_filename(doc.title) or "Dokument")
                if doc.issuer:
                    parts.append(_safe_filename(doc.issuer))
                if doc.amount is not None:
                    parts.append(f"{float(doc.amount):.2f}{doc.currency}")
                fname = "_".join(parts) + f".{doc.file_type}"

                # Datei zum ZIP hinzufuegen
                file_path = Path(doc.file_path)
                if file_path.exists():
                    zf.write(file_path, f"{prefix}/{folder}/{fname}")
                else:
                    logger.warning("Datei nicht gefunden: %s", file_path)

                all_rows.append({
                    "Kategorie": TAX_CATEGORY_LABELS.get(cat_key, cat_key),
                    "Datum": str(doc.document_date or ""),
                    "Titel": doc.title,
                    "Aussteller": doc.issuer or "",
                    "Betrag": f"{float(doc.amount):.2f}" if doc.amount is not None else "",
                    "Waehrung": doc.currency,
                    "Dateiname": fname,
                    "Dokumenttyp": doc.document_type,
                })

            # CSV pro Kategorie
            if include_csv and cat_docs:
                csv_buf = io.StringIO()
                writer = csv.DictWriter(
                    csv_buf,
                    fieldnames=["Datum", "Titel", "Aussteller", "Betrag", "Waehrung", "Dokumenttyp"],
                    delimiter=";",
                )
                writer.writeheader()
                for row in all_rows[-len(cat_docs):]:
                    writer.writerow({k: row[k] for k in writer.fieldnames})
                zf.writestr(
                    f"{prefix}/{folder}/uebersicht.csv",
                    csv_buf.getvalue().encode("utf-8-sig").decode("utf-8-sig"),
                )

        # Gesamt-CSV
        if include_csv and all_rows:
            csv_buf = io.StringIO()
            writer = csv.DictWriter(
                csv_buf,
                fieldnames=["Kategorie", "Datum", "Titel", "Aussteller", "Betrag", "Waehrung", "Dokumenttyp", "Dateiname"],
                delimiter=";",
            )
            writer.writeheader()
            for row in all_rows:
                writer.writerow(row)
            csv_bytes = csv_buf.getvalue().encode("utf-8-sig")
            zf.writestr(f"{prefix}/Gesamtuebersicht.csv", csv_bytes)

        # Uebersicht-PDF
        if include_pdf:
            pdf_bytes = _create_overview_pdf(year, by_category, all_rows)
            if pdf_bytes:
                zf.writestr(f"{prefix}/Uebersicht.pdf", pdf_bytes)

    return zip_buffer.getvalue()


def _create_overview_pdf(year: int, by_category: dict, all_rows: list[dict]) -> bytes | None:
    """Erstellt Uebersicht-PDF mit reportlab."""
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
        logger.warning("reportlab nicht installiert, PDF-Erstellung uebersprungen")
        return None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    elements = []

    # Titel
    title_style = ParagraphStyle("CustomTitle", parent=styles["Title"], fontSize=18, spaceAfter=12)
    elements.append(Paragraph(f"Steuerbelege {year}", title_style))
    elements.append(Spacer(1, 0.5 * cm))

    # Zusammenfassung
    total_amount = sum(float(r["Betrag"]) for r in all_rows if r["Betrag"])
    elements.append(Paragraph(
        f"Anzahl Belege: {len(all_rows)} | Gesamtbetrag: {total_amount:,.2f} EUR",
        styles["Normal"],
    ))
    elements.append(Spacer(1, 0.5 * cm))

    # Tabelle pro Kategorie
    for cat_key in TaxCategory:
        if cat_key not in by_category:
            continue
        cat_label = TAX_CATEGORY_LABELS.get(cat_key, cat_key)
        cat_docs = by_category[cat_key]
        cat_total = sum(float(d.amount or 0) for d in cat_docs)

        elements.append(Paragraph(
            f"{cat_label} ({len(cat_docs)} Belege, {cat_total:,.2f} EUR)",
            styles["Heading2"],
        ))

        table_data = [["Datum", "Titel", "Aussteller", "Betrag"]]
        for d in cat_docs:
            table_data.append([
                str(d.document_date or "-"),
                (d.title or "-")[:40],
                (d.issuer or "-")[:30],
                f"{float(d.amount):.2f} {d.currency}" if d.amount is not None else "-",
            ])

        t = Table(table_data, colWidths=[3 * cm, 7 * cm, 4 * cm, 3 * cm])
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

    # Erstellt am
    elements.append(Spacer(1, 1 * cm))
    elements.append(Paragraph(
        f"Erstellt am {date.today().strftime('%d.%m.%Y')} mit Zettelwirtschaft",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8, textColor=colors.grey),
    ))

    doc.build(elements)
    return buf.getvalue()
