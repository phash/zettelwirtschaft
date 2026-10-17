"""Ablagebereiche: Validierung und Loeschen mit Archivdateien (Review 1.6.0)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.document import Document
from app.models.filing_scope import FilingScope, generate_slug
from tests.factories import make_document


class TestGenerateSlug:
    @pytest.mark.parametrize("name", ["€", "💼", "日本", "!!!"])
    def test_never_empty(self, name):
        assert generate_slug(name).startswith("bereich-")

    def test_transliteration(self):
        assert generate_slug("Ärzte & Co") == "aerzte-co"
        assert generate_slug("Café Müßig") == "cafe-muessig"


@pytest.mark.asyncio
class TestFilingScopeValidation:
    async def test_name_is_trimmed_and_blank_rejected(self, client):
        resp = await client.post("/api/filing-scopes", json={"name": "  Praxis  "})
        assert resp.status_code == 201
        assert resp.json()["name"] == "Praxis"

        resp = await client.post("/api/filing-scopes", json={"name": "   "})
        assert resp.status_code == 422

    async def test_duplicate_name_case_insensitive(self, client):
        await client.post("/api/filing-scopes", json={"name": "Ärzte"})
        resp = await client.post("/api/filing-scopes", json={"name": "ärzte"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "Ein Ablagebereich mit diesem Namen existiert bereits."

    async def test_rename_to_existing_name_case_insensitive(self, client):
        await client.post("/api/filing-scopes", json={"name": "Privat"})
        other = (await client.post("/api/filing-scopes", json={"name": "Praxis"})).json()
        resp = await client.patch(f"/api/filing-scopes/{other['id']}", json={"name": "PRIVAT"})
        assert resp.status_code == 409

    async def test_slug_collision_gets_suffix_instead_of_error(self, client):
        await client.post("/api/filing-scopes", json={"name": "Privat"})
        resp = await client.post("/api/filing-scopes", json={"name": "Privat!"})
        assert resp.status_code == 201
        assert resp.json()["slug"] == "privat-2"

    async def test_name_without_ascii_letters_gets_generated_slug(self, client):
        resp = await client.post("/api/filing-scopes", json={"name": "€"})
        assert resp.status_code == 201
        assert resp.json()["slug"].startswith("bereich-")

    @pytest.mark.parametrize("color", ["zzz", "#12345", "#GGGGGG", "red"])
    async def test_invalid_color_rejected(self, client, color):
        resp = await client.post("/api/filing-scopes", json={"name": "Farbe", "color": color})
        assert resp.status_code == 422
        create = await client.post("/api/filing-scopes", json={"name": "Farbe", "color": "#AbC123"})
        assert create.status_code == 201
        resp = await client.patch(f"/api/filing-scopes/{create.json()['id']}", json={"color": color})
        assert resp.status_code == 422

    async def test_keywords_are_cleaned(self, client):
        resp = await client.post("/api/filing-scopes", json={
            "name": "Praxis", "keywords": [" Arzt ", "arzt", "", "  ", "Zahn  Arzt"],
        })
        assert resp.status_code == 201
        assert resp.json()["keywords"] == ["Arzt", "Zahn Arzt"]

    async def test_one_letter_keyword_rejected(self, client):
        resp = await client.post("/api/filing-scopes", json={"name": "Praxis", "keywords": ["a"]})
        assert resp.status_code == 422


async def _scope(db, name, slug, is_default=False):
    scope = FilingScope(name=name, slug=slug, is_default=is_default)
    db.add(scope)
    await db.flush()
    return scope


async def _doc_with_file(db, scope_id, path: Path, *, create_file=True):
    if create_file:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 test")
    return await make_document(
        db,
        original_filename=path.name,
        stored_filename=path.name,
        file_path=str(path),
        file_size_bytes=13,
        title=path.stem,
        ocr_text="",
        filing_scope_id=scope_id,
    )


@pytest.mark.asyncio
class TestDeleteFilingScopeWithFiles:
    async def test_files_of_moved_documents_survive(self, client, db_session, test_settings):
        """Regression: rmtree des alten Slug-Ordners loeschte Dateien von
        Dokumenten, die per PATCH in einen anderen Bereich gewechselt waren."""
        archive = Path(test_settings.ARCHIVE_DIR)
        privat = await _scope(db_session, "Privat", "privat", is_default=True)
        praxis = await _scope(db_session, "Praxis", "praxis")
        arbeit = await _scope(db_session, "Arbeit", "arbeit")
        own = await _doc_with_file(db_session, praxis.id, archive / "praxis/2024/03/RECHNUNG/a_own.pdf")
        moved = await _doc_with_file(db_session, praxis.id, archive / "praxis/2024/04/RECHNUNG/b_moved.pdf")
        # Altbestand (vor 1.6 verschob PATCH/Review die Datei nicht): Dokument
        # gehoert schon zu einem anderen Bereich, die Datei liegt noch unter praxis/.
        legacy_privat = await _doc_with_file(db_session, privat.id, archive / "praxis/2023/01/RECHNUNG/c_alt.pdf")
        legacy_arbeit = await _doc_with_file(db_session, arbeit.id, archive / "praxis/2023/02/RECHNUNG/d_alt.pdf")
        await db_session.commit()

        # Bereich per PATCH wechseln (verschiebt die Datei seit 1.6 gleich mit)
        resp = await client.patch(f"/api/documents/{moved.id}", json={"filing_scope_id": privat.id})
        assert resp.status_code == 200

        resp = await client.delete(f"/api/filing-scopes/{praxis.id}")
        assert resp.status_code == 204

        for doc_id in (own.id, moved.id, legacy_privat.id, legacy_arbeit.id):
            file_resp = await client.get(f"/api/documents/{doc_id}/file")
            assert file_resp.status_code == 200, doc_id

        db_session.expire_all()
        docs = {d.id: d for d in (await db_session.execute(select(Document))).scalars().all()}
        for doc_id in (own.id, moved.id, legacy_privat.id):
            path = Path(docs[doc_id].file_path)
            assert path.is_file()
            assert path.resolve().is_relative_to((archive / "privat").resolve())
            assert docs[doc_id].filing_scope_id == privat.id
        assert docs[legacy_arbeit.id].filing_scope_id == arbeit.id
        assert Path(docs[legacy_arbeit.id].file_path).resolve() == (
            archive / "arbeit/2023/02/RECHNUNG/d_alt.pdf"
        ).resolve()
        assert (archive / "privat/2024/03/RECHNUNG/a_own.pdf").is_file()
        assert (archive / "privat/2024/04/RECHNUNG/b_moved.pdf").is_file()
        assert (archive / "privat/2023/01/RECHNUNG/c_alt.pdf").is_file()
        assert not (archive / "praxis").exists()

    async def test_foreign_files_in_old_folder_are_kept(self, client, db_session, test_settings):
        """Dateien ohne Dokument im alten Ordner bleiben; nur leere Ordner verschwinden."""
        archive = Path(test_settings.ARCHIVE_DIR)
        await _scope(db_session, "Privat", "privat", is_default=True)
        praxis = await _scope(db_session, "Praxis", "praxis")
        await db_session.commit()
        stray = archive / "praxis" / "notiz.txt"
        stray.parent.mkdir(parents=True)
        stray.write_text("bitte behalten")
        (archive / "praxis" / "leer" / "unterordner").mkdir(parents=True)

        resp = await client.delete(f"/api/filing-scopes/{praxis.id}")
        assert resp.status_code == 204
        assert stray.is_file()
        assert not (archive / "praxis" / "leer").exists()

    async def test_missing_file_only_reassigns(self, client, db_session, test_settings):
        archive = Path(test_settings.ARCHIVE_DIR)
        privat = await _scope(db_session, "Privat", "privat", is_default=True)
        praxis = await _scope(db_session, "Praxis", "praxis")
        doc = await _doc_with_file(
            db_session, praxis.id, archive / "praxis/2024/01/RECHNUNG/fehlt.pdf", create_file=False,
        )
        doc_id, privat_id = doc.id, privat.id
        await db_session.commit()

        resp = await client.delete(f"/api/filing-scopes/{praxis.id}")
        assert resp.status_code == 204
        db_session.expire_all()
        reloaded = await db_session.get(Document, doc_id)
        assert reloaded.filing_scope_id == privat_id

    async def test_empty_slug_never_touches_archive_root(self, client, db_session, test_settings):
        """Regression: Bereich mit leerem Slug -> rmtree(archive_root) loeschte alles."""
        archive = Path(test_settings.ARCHIVE_DIR)
        privat = await _scope(db_session, "Privat", "privat", is_default=True)
        broken = await _scope(db_session, "Altbestand", "")
        doc = await _doc_with_file(db_session, broken.id, archive / "2024/01/RECHNUNG/root.pdf")
        other = await _doc_with_file(db_session, privat.id, archive / "privat/2024/01/RECHNUNG/p.pdf")
        doc_id, privat_id = doc.id, privat.id
        await db_session.commit()

        resp = await client.delete(f"/api/filing-scopes/{broken.id}")
        assert resp.status_code == 204
        assert Path(doc.file_path).is_file()
        assert Path(other.file_path).is_file()
        db_session.expire_all()
        assert (await db_session.get(Document, doc_id)).filing_scope_id == privat_id

    async def test_copy_failure_aborts_without_changes(self, client, db_session, test_settings, monkeypatch):
        archive = Path(test_settings.ARCHIVE_DIR)
        await _scope(db_session, "Privat", "privat", is_default=True)
        praxis = await _scope(db_session, "Praxis", "praxis")
        ok_doc = await _doc_with_file(db_session, praxis.id, archive / "praxis/2024/01/RECHNUNG/1.pdf")
        bad_doc = await _doc_with_file(db_session, praxis.id, archive / "praxis/2024/01/RECHNUNG/2.pdf")
        praxis_id, doc_ids = praxis.id, (ok_doc.id, bad_doc.id)
        await db_session.commit()

        from app.services import file_relocation
        real_copy = file_relocation._copy_sync

        def flaky_copy(move):
            if move.document_id == doc_ids[1]:
                raise OSError("Datenträger voll")
            real_copy(move)

        monkeypatch.setattr(file_relocation, "_copy_sync", flaky_copy)

        resp = await client.delete(f"/api/filing-scopes/{praxis.id}")
        assert resp.status_code == 500
        assert "nicht gelöscht" in resp.json()["detail"]

        db_session.expire_all()
        assert await db_session.get(FilingScope, praxis_id) is not None
        for doc_id in doc_ids:
            reloaded = await db_session.get(Document, doc_id)
            assert reloaded.filing_scope_id == praxis_id
            assert Path(reloaded.file_path).is_file()
        # Die bereits gemachte Kopie wurde wieder entfernt
        assert not (archive / "privat/2024/01/RECHNUNG/1.pdf").exists()

    async def test_chroma_scope_metadata_updated(self, client, db_session, test_settings, monkeypatch):
        privat = await _scope(db_session, "Privat", "privat", is_default=True)
        praxis = await _scope(db_session, "Praxis", "praxis")
        doc = await _doc_with_file(db_session, praxis.id, Path(test_settings.ARCHIVE_DIR) / "praxis/x.pdf")
        await db_session.commit()

        calls = []

        async def fake_update(ids, scope_id, settings):
            calls.append((list(ids), scope_id))

        from app.services import vectorize_service
        monkeypatch.setattr(vectorize_service, "update_scope_metadata", fake_update)

        resp = await client.delete(f"/api/filing-scopes/{praxis.id}")
        assert resp.status_code == 204
        assert calls == [([doc.id], privat.id)]
