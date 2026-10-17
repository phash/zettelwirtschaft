"""Steuerpaket: deutsche Formate, Fremdwaehrungen, doppelte Namen, fehlende
Dateien (Review 1.6.0)."""

import csv
import io
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.document import DocumentType, ReviewStatus, TaxCategory
from app.services.tax_export_service import (
    MISSING_FILES_LIST_NAME,
    _build_zip_sync,
    _safe_filename,
    count_label,
    format_amount_de,
    format_date_de,
    get_tax_summary,
    validate_export,
)
from tests.factories import make_document


class TestFormatting:
    @pytest.mark.parametrize("value,thousands,expected", [
        (Decimal("1234.5"), True, "1.234,50"),
        (Decimal("1234.5"), False, "1234,50"),
        (Decimal("-1234567.891"), True, "-1.234.567,89"),
        (Decimal("0"), True, "0,00"),
    ])
    def test_amount(self, value, thousands, expected):
        assert format_amount_de(value, thousands=thousands) == expected

    def test_date(self):
        assert format_date_de(date(2025, 3, 1)) == "01.03.2025"
        assert format_date_de(None) == ""

    def test_count_label_singular(self):
        assert count_label(1) == "1 Beleg"
        assert count_label(2) == "2 Belege"

    def test_safe_filename_transliterates_umlauts(self):
        assert _safe_filename("Sanitär Müller & Söhne") == "Sanitaer_Mueller_Soehne"


def _row(path: Path, **overrides) -> dict:
    data = {
        "id": uuid.uuid4().hex,
        "tax_category": TaxCategory.WERBUNGSKOSTEN,
        "document_date": date(2025, 3, 1),
        "title": "Parkschein",
        "issuer": "Parkhaus",
        "amount": Decimal("4.50"),
        "currency": "EUR",
        "file_type": "pdf",
        "file_path": str(path),
        "document_type": DocumentType.QUITTUNG,
    }
    data.update(overrides)
    return data


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    text = zf.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


class TestBuildZip:
    def test_identical_receipts_get_unique_entry_names(self, tmp_path):
        """Regression: zwei gleiche Parkscheine ueberschrieben sich beim Entpacken."""
        beleg = tmp_path / "beleg.pdf"
        beleg.write_bytes(b"%PDF-1.4 dummy")
        docs = [_row(beleg), _row(beleg)]
        target = tmp_path / "out.zip"

        _build_zip_sync(docs, 2025, include_pdf=True, include_csv=True, target=target)

        with zipfile.ZipFile(target) as zf:
            receipts = [n for n in zf.namelist() if n.endswith(".pdf") and "Uebersicht" not in n]
            assert len(receipts) == 2
            assert len(set(receipts)) == 2
            rows = _read_csv(zf, "Steuerpaket_2025/Gesamtuebersicht.csv")
        assert len({r["Dateiname"] for r in rows}) == 2
        assert {f"Steuerpaket_2025/01_Werbungskosten/{r['Dateiname']}" for r in rows} == set(receipts)

    def test_german_csv_columns(self, tmp_path):
        beleg = tmp_path / "beleg.pdf"
        beleg.write_bytes(b"%PDF-1.4 dummy")
        docs = [_row(beleg, amount=Decimal("1234.50"), tax_category=TaxCategory.AUSSERGEWOEHNLICHE_BELASTUNGEN)]
        target = tmp_path / "out.zip"

        _build_zip_sync(docs, 2025, include_pdf=False, include_csv=True, target=target)

        with zipfile.ZipFile(target) as zf:
            row = _read_csv(zf, "Steuerpaket_2025/Gesamtuebersicht.csv")[0]
            assert any(n.startswith("Steuerpaket_2025/01_Aussergewoehnliche_Belastungen/") for n in zf.namelist())
        assert row["Kategorie"] == "Außergewöhnliche Belastungen"
        assert row["Betrag"] == "1234,50"
        assert row["Währung"] == "EUR"
        assert row["Datum"] == "01.03.2025"
        assert row["Dokumenttyp"] == "Quittung"
        assert row["Hinweis"] == ""

    def test_missing_file_is_marked_and_listed(self, tmp_path):
        """Regression: fehlende Dateien wurden still uebersprungen, aber gelistet."""
        beleg = tmp_path / "beleg.pdf"
        beleg.write_bytes(b"%PDF-1.4 dummy")
        docs = [_row(beleg, title="Vorhanden"), _row(tmp_path / "weg.pdf", title="Fehlend")]
        target = tmp_path / "out.zip"

        _build_zip_sync(docs, 2025, include_pdf=True, include_csv=True, target=target)

        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
            rows = {r["Titel"]: r for r in _read_csv(zf, "Steuerpaket_2025/Gesamtuebersicht.csv")}
            missing = zf.read(f"Steuerpaket_2025/{MISSING_FILES_LIST_NAME}").decode("utf-8-sig")
        assert rows["Fehlend"]["Hinweis"] == "Datei fehlt"
        assert rows["Vorhanden"]["Hinweis"] == ""
        assert "Fehlend" in missing and "Vorhanden" not in missing
        assert not any(rows["Fehlend"]["Dateiname"] in n for n in names)

    def test_non_eur_and_special_characters_in_pdf(self, tmp_path):
        """PDF mit Fremdwaehrung und '&'/'<' im Titel (reportlab-Markup) baut fehlerfrei."""
        beleg = tmp_path / "beleg.pdf"
        beleg.write_bytes(b"%PDF-1.4 dummy")
        docs = [_row(beleg), _row(beleg, title="Hotel <NYC> & Co", currency="USD", amount=Decimal("120"))]
        target = tmp_path / "out.zip"

        _build_zip_sync(docs, 2025, include_pdf=True, include_csv=False, target=target)

        with zipfile.ZipFile(target) as zf:
            assert zf.read("Steuerpaket_2025/Uebersicht.pdf").startswith(b"%PDF")


@pytest.mark.asyncio
class TestSummaryCurrencies:
    async def test_eur_total_only_with_warning(self, db_session):
        for amount, currency in ((Decimal("100.00"), "EUR"), (Decimal("50.00"), "USD"), (Decimal("10.00"), "eur")):
            await make_document(
                db_session, title=f"Beleg {currency}", tax_relevant=True, tax_year=2025,
                tax_category=TaxCategory.WERBUNGSKOSTEN, amount=amount, currency=currency,
            )
        await db_session.commit()

        summary = await get_tax_summary(db_session, 2025)
        assert summary["total_amount"] == Decimal("110.00")
        assert summary["categories"][0]["total_amount"] == Decimal("110.00")
        assert [d["currency"] for d in summary["non_eur_documents"]] == ["USD"]
        assert any("Beleg USD" in w and "nicht in der EUR-Summe" in w for w in summary["warnings"])

    async def test_labels_and_review_warning_with_umlauts(self, db_session):
        await make_document(
            db_session, title="Arzt", tax_relevant=True, tax_year=2025, amount=Decimal("1"),
            tax_category=TaxCategory.AUSSERGEWOEHNLICHE_BELASTUNGEN, review_status=ReviewStatus.NEEDS_REVIEW,
        )
        await db_session.commit()

        summary = await get_tax_summary(db_session, 2025)
        assert summary["categories"][0]["label"] == "Außergewöhnliche Belastungen"
        assert "Dokument 'Arzt' hat offene Rückfragen." in summary["warnings"]

    async def test_validate_warns_about_missing_files(self, db_session, tmp_path):
        present = tmp_path / "da.pdf"
        present.write_bytes(b"x")
        await make_document(db_session, title="Da", tax_relevant=True, tax_year=2025, file_path=str(present))
        await make_document(db_session, title="Weg", tax_relevant=True, tax_year=2025, file_path=str(tmp_path / "weg.pdf"))
        await db_session.commit()

        result = await validate_export(db_session, 2025)
        missing = [w for w in result["warnings"] if "fehlt im Archiv" in w]
        assert missing == ["Datei für Dokument 'Weg' fehlt im Archiv und kann nicht exportiert werden."]


@pytest.mark.asyncio
class TestExportApi:
    async def test_export_streams_zip_from_temp_file(self, client, db_session, tmp_path, monkeypatch):
        beleg = tmp_path / "beleg.pdf"
        beleg.write_bytes(b"%PDF-1.4 dummy")
        for _ in range(2):
            await make_document(
                db_session, title="Parkschein", tax_relevant=True, tax_year=2025,
                tax_category=TaxCategory.WERBUNGSKOSTEN, amount=Decimal("4.50"), file_path=str(beleg),
                document_date=date(2025, 3, 1),
            )
        await db_session.commit()

        created: list[Path] = []
        import app.services.tax_export_service as svc
        real_mkstemp = svc.tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            created.append(Path(name))
            return fd, name

        monkeypatch.setattr(svc.tempfile, "mkstemp", tracking_mkstemp)

        resp = await client.post("/api/tax/export", json={"year": 2025})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert 'filename="Steuerpaket_2025.zip"' in resp.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            receipts = [n for n in zf.namelist() if n.endswith(".pdf") and "Uebersicht" not in n]
        assert len(set(receipts)) == 2
        # Temporaere ZIP-Datei wurde nach dem Ausliefern geloescht
        assert created and not any(p.exists() for p in created)

    async def test_marking_tax_relevant_via_review_shows_year(self, client, db_session):
        """Steuerjahr wird abgeleitet, wenn tax_relevant ohne Jahr gesetzt wird."""
        from app.models.review_question import ReviewQuestion

        doc = await make_document(db_session, document_date=date(2023, 11, 2), amount=Decimal("20"))
        db_session.add(ReviewQuestion(document_id=doc.id, question="Steuerrelevant?", field_affected="tax_relevant"))
        await db_session.commit()
        q_id = (await client.get(f"/api/review/documents/{doc.id}")).json()["questions"][0]["id"]

        resp = await client.post(f"/api/review/questions/{q_id}/answer", json={"answer": "ja"})
        assert resp.status_code == 200
        assert (await client.get("/api/tax/years")).json()["years"] == [2023]
        validation = (await client.get("/api/tax/validate/2023")).json()
        assert validation["ready"] is True
