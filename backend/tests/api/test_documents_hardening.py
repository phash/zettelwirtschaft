"""Regressionstests fuer die Dokument-API: Validierung, Soft-Delete, Tags, FTS,
Re-Vektorisierung, Datei-/Thumbnail-Auslieferung und Statistik."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import DocumentStatus
from app.models.filing_scope import FilingScope
from app.models.warranty_info import WarrantyInfo, WarrantyType
from tests.factories import make_document


@pytest.fixture
def revectorize():
    with patch(
        "app.services.vectorize_service.revectorize_document_safe",
        new_callable=AsyncMock,
        return_value=1,
    ) as mock:
        yield mock


async def _fts_tags(db: AsyncSession, doc_id: str) -> str | None:
    row = (await db.execute(
        text("SELECT tags FROM documents_fts WHERE doc_id = :id"), {"id": doc_id}
    )).first()
    return row[0] if row else None


class TestPatchValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"title": None},
            {"title": ""},
            {"title": "x" * 501},
            {"document_type": None},
            {"currency": None},
            {"currency": "EURO-LONG"},
            {"tax_relevant": None},
            {"amount": 1e30},
            {"amount": 1.234},
            {"tax_year": -3},
            {"tax_year": 3000},
        ],
    )
    async def test_invalid_values_rejected(self, client, db_session: AsyncSession, payload, revectorize):
        doc = await make_document(db_session, commit=True)
        resp = await client.patch(f"/api/documents/{doc.id}", json=payload)
        assert resp.status_code == 422, resp.text

    async def test_unknown_filing_scope_is_422(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True)
        resp = await client.patch(f"/api/documents/{doc.id}", json={"filing_scope_id": "gibt-es-nicht"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Ablagebereich nicht gefunden"

    async def test_blank_strings_clear_optional_fields(self, client, db_session: AsyncSession, revectorize):
        from decimal import Decimal

        doc = await make_document(
            db_session, commit=True, amount=Decimal("12.50"), document_date=date(2024, 1, 1),
        )
        resp = await client.patch(
            f"/api/documents/{doc.id}", json={"amount": "", "document_date": "", "issuer": " "},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["amount"] is None
        assert data["document_date"] is None
        assert data["issuer"] is None

    async def test_currency_normalized_and_negative_amount_allowed(
        self, client, db_session: AsyncSession, revectorize,
    ):
        doc = await make_document(db_session, commit=True)
        resp = await client.patch(f"/api/documents/{doc.id}", json={"currency": " chf ", "amount": -19.99})
        assert resp.status_code == 200, resp.text
        assert resp.json()["currency"] == "CHF"
        assert resp.json()["amount"] == -19.99


class TestRevectorizeAfterEdit:
    async def test_scope_change_moves_file_and_updates_scope_metadata(
        self, client, db_session: AsyncSession, revectorize, test_settings,
    ):
        """Ablagebereich per PATCH: Datei wandert in den Ordner des neuen Bereichs
        (wie bei Review-Antwort/Re-Analyse), Chroma bekommt nur neue Metadaten."""
        privat = FilingScope(name="Privat", slug="privat", keywords="[]")
        praxis = FilingScope(name="Praxis", slug="praxis", keywords="[]")
        db_session.add_all([privat, praxis])
        await db_session.flush()
        source = Path(test_settings.ARCHIVE_DIR) / "privat" / "2024" / "03" / "RECHNUNG" / "abc_a.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"%PDF-1.4 test")
        doc = await make_document(
            db_session, commit=True, file_path=str(source), filing_scope_id=privat.id,
        )

        with patch(
            "app.services.vectorize_service.update_scope_metadata", new_callable=AsyncMock, return_value=1,
        ) as scope_meta:
            resp = await client.patch(f"/api/documents/{doc.id}", json={"filing_scope_id": praxis.id})

        assert resp.status_code == 200, resp.text
        target = Path(test_settings.ARCHIVE_DIR) / "praxis" / "2024" / "03" / "RECHNUNG" / "abc_a.pdf"
        assert target.is_file()
        assert not source.exists()
        await db_session.refresh(doc)
        assert Path(doc.file_path).resolve() == target.resolve()
        scope_meta.assert_awaited_once_with([doc.id], praxis.id, test_settings)
        revectorize.assert_not_awaited()
        assert (await client.get(f"/api/documents/{doc.id}/file")).status_code == 200

    async def test_marking_tax_relevant_derives_year_for_tax_views(
        self, client, db_session: AsyncSession, revectorize,
    ):
        doc = await make_document(db_session, commit=True, document_date=date(2023, 7, 1))

        resp = await client.patch(f"/api/documents/{doc.id}", json={"tax_relevant": True})

        assert resp.status_code == 200, resp.text
        assert resp.json()["tax_year"] == 2023
        assert 2023 in (await client.get("/api/tax/years")).json()["years"]

    async def test_non_vector_field_does_not_revectorize(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True)
        resp = await client.patch(f"/api/documents/{doc.id}", json={"tax_year": 2024})
        assert resp.status_code == 200
        revectorize.assert_not_awaited()

    async def test_tag_changes_revectorize(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True)
        add = await client.post(f"/api/documents/{doc.id}/tags", json={"name": "Strom"})
        tag_id = add.json()["tags"][0]["id"]
        await client.delete(f"/api/documents/{doc.id}/tags/{tag_id}")
        assert revectorize.await_count == 2


class TestTagsAndFts:
    async def test_manual_tags_update_fts(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True)

        add = await client.post(f"/api/documents/{doc.id}/tags", json={"name": "Nebenkosten"})
        assert add.status_code == 200
        assert "nebenkosten" in (await _fts_tags(db_session, doc.id) or "")

        tag_id = add.json()["tags"][0]["id"]
        resp = await client.delete(f"/api/documents/{doc.id}/tags/{tag_id}")
        assert resp.status_code == 200
        assert "nebenkosten" not in (await _fts_tags(db_session, doc.id) or "")

    async def test_title_edit_reindexes_fts(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True)
        await client.patch(f"/api/documents/{doc.id}", json={"title": "Wasserrechnung"})
        row = (await db_session.execute(
            text("SELECT title FROM documents_fts WHERE doc_id = :id"), {"id": doc.id}
        )).first()
        assert row[0] == "Wasserrechnung"

    @pytest.mark.parametrize("name", ["a/b", "c#d", "50%", "frage?"])
    async def test_remove_special_tag_by_id(self, client, db_session: AsyncSession, name, revectorize):
        doc = await make_document(db_session, commit=True)
        add = await client.post(f"/api/documents/{doc.id}/tags", json={"name": name})
        tag_id = add.json()["tags"][0]["id"]

        resp = await client.delete(f"/api/documents/{doc.id}/tags/{tag_id}")

        assert resp.status_code == 200
        detail = await client.get(f"/api/documents/{doc.id}")
        assert detail.json()["tags"] == []

    async def test_numeric_tag_name_is_unambiguous(self, client, db_session: AsyncSession, revectorize):
        """Nur die ID-Route: ein Tag namens "2024" wird nicht mit der Tag-ID 2024
        verwechselt, und ein Name im Pfad wird nicht mehr als Name gedeutet."""
        doc = await make_document(db_session, commit=True)
        add = await client.post(f"/api/documents/{doc.id}/tags", json={"name": "2024"})
        tag_id = add.json()["tags"][0]["id"]

        assert (await client.delete(f"/api/documents/{doc.id}/tags/2024")).status_code == 404
        assert (await client.delete(f"/api/documents/{doc.id}/tags/{tag_id}")).status_code == 200
        assert (await client.get(f"/api/documents/{doc.id}")).json()["tags"] == []

    async def test_remove_unassigned_tag_is_404(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True)
        other = await make_document(db_session, commit=True)
        add = await client.post(f"/api/documents/{other.id}/tags", json={"name": "fremd"})
        tag_id = add.json()["tags"][0]["id"]

        assert (await client.delete(f"/api/documents/{doc.id}/tags/{tag_id}")).status_code == 404
        assert (await client.delete(f"/api/documents/{doc.id}/tags/999999")).status_code == 404


class TestSoftDeletedDocuments:
    async def test_deleted_document_unreachable(self, client, db_session: AsyncSession, revectorize):
        doc = await make_document(db_session, commit=True, status=DocumentStatus.DELETED)

        assert (await client.get(f"/api/documents/{doc.id}")).status_code == 404
        assert (await client.patch(f"/api/documents/{doc.id}", json={"title": "X"})).status_code == 404
        assert (await client.get(f"/api/documents/{doc.id}/file")).status_code == 404
        assert (await client.get(f"/api/documents/{doc.id}/thumbnail")).status_code == 404
        assert (await client.post(f"/api/documents/{doc.id}/tags", json={"name": "a"})).status_code == 404
        assert (await client.delete(f"/api/documents/{doc.id}/tags/1")).status_code == 404

    async def test_double_delete_is_idempotent(self, client, db_session: AsyncSession):
        doc = await make_document(db_session, commit=True)
        with patch("app.services.vectorize_service.delete_document_vectors", new_callable=AsyncMock):
            assert (await client.delete(f"/api/documents/{doc.id}")).status_code == 204
            assert (await client.delete(f"/api/documents/{doc.id}")).status_code == 204

        audits = (await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc.id, AuditLog.action == AuditAction.DELETED)
        )).scalars().all()
        assert len(audits) == 1


class TestFileAndThumbnail:
    async def test_file_served_from_archive(self, client, db_session: AsyncSession, test_settings):
        archived = Path(test_settings.ARCHIVE_DIR) / "privat" / "2024" / "01" / "RECHNUNG" / "x_a.pdf"
        archived.parent.mkdir(parents=True)
        archived.write_bytes(b"%PDF-1.4 inhalt")
        doc = await make_document(db_session, commit=True, file_path=str(archived), original_filename="a.pdf")

        resp = await client.get(f"/api/documents/{doc.id}/file")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == b"%PDF-1.4 inhalt"

    async def test_file_outside_archive_or_missing_is_404(
        self, client, db_session: AsyncSession, tmp_path: Path,
    ):
        outside = tmp_path / "geheim.pdf"
        outside.write_bytes(b"%PDF")
        doc = await make_document(db_session, commit=True, file_path=str(outside))
        missing = await make_document(db_session, commit=True, file_path="/archive/fehlt.pdf")

        assert (await client.get(f"/api/documents/{doc.id}/file")).status_code == 404
        assert (await client.get(f"/api/documents/{missing.id}/file")).status_code == 404

    async def test_job_thumbnail_fallback(self, client, test_settings):
        job_id = "0f0e5d2c-1b2a-4c3d-8e9f-001122334455"
        (Path(test_settings.THUMBNAIL_DIR) / f"{job_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        resp = await client.get(f"/api/documents/{job_id}/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    @pytest.mark.parametrize("raw_id", ["..%5Cgeheim", "..%5C..%5Cgeheim", "C:%5Cgeheim", "%2E%2E%5Cgeheim"])
    async def test_thumbnail_traversal_blocked(self, client, test_settings, raw_id):
        # Ziel-PNG direkt neben THUMBNAIL_DIR — darf nie ausgeliefert werden.
        (Path(test_settings.THUMBNAIL_DIR).parent / "geheim.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        resp = await client.get(f"/api/documents/{raw_id}/thumbnail")
        assert resp.status_code == 404


class TestResponsesHideServerPaths:
    async def test_no_paths_or_hash_in_document_responses(self, client, db_session: AsyncSession):
        doc = await make_document(db_session, commit=True, thumbnail_path="C:/Users/max/thumb.png")

        detail = (await client.get(f"/api/documents/{doc.id}")).json()
        items = (await client.get("/api/documents")).json()["items"]

        for payload in [detail, *items]:
            assert not [k for k in payload if k.endswith("_path")]
            assert "file_hash" not in payload


class TestListValidation:
    @pytest.mark.parametrize(
        "query",
        ["date_from=gestern", "date_to=2024-13-01", "status=BOGUS", "document_type=RECHNUNG,FOO"],
    )
    async def test_invalid_filters_are_422(self, client, query):
        assert (await client.get(f"/api/documents?{query}")).status_code == 422

    async def test_pagination_is_stable_for_equal_sort_keys(self, client, db_session: AsyncSession):
        created = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
        ids = {(await make_document(db_session, created_at=created)).id for _ in range(5)}
        await db_session.commit()

        seen = []
        for page in (1, 2, 3):
            resp = await client.get(f"/api/documents?page={page}&page_size=2")
            seen.extend(item["id"] for item in resp.json()["items"])
        assert sorted(seen) == sorted(ids)


class TestDashboardWarranties:
    async def test_deleted_document_warranty_not_counted(self, client, db_session: AsyncSession):
        today = date.today()
        active = await make_document(db_session)
        deleted = await make_document(db_session, status=DocumentStatus.DELETED)
        for doc in (active, deleted):
            db_session.add(WarrantyInfo(
                document_id=doc.id, product_name="Gerät",
                purchase_date=today - timedelta(days=700),
                warranty_end_date=today + timedelta(days=5),
                warranty_type=WarrantyType.LEGAL,
            ))
        await db_session.commit()

        assert (await client.get("/api/stats")).json()["expiring_warranties_30d"] == 1
