"""Regressionstests fuer vectorize_service: Embed-vor-Delete, Tags ohne Lazy-Load,
Scope-Metadaten, Re-Vektorisierung, Chunking-Fortschritt, Initial-Vektorisierung."""

import asyncio
import warnings
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import chromadb
import pytest

from app.models.background_task import TaskStatus
from app.models.document import Document, DocumentStatus, DocumentTag, DocumentType, Tag, TaxCategory
from app.services import vectorize_service
from app.services.ollama_client import OllamaError, OllamaErrorKind, OllamaProbe
from app.services.task_service import get_task, shutdown_tasks, wait_for_task
from app.services.vectorize_service import (
    VectorizeError,
    _build_metadata_chunk,
    chunk_text,
    get_collection_count,
    revectorize_document_safe,
    run_initial_vectorize,
    update_scope_metadata,
    vectorize_document,
)
from tests.factories import make_document


@pytest.fixture
def chroma(test_settings):
    """Echte In-Memory-Collection statt Mock — prueft die tatsaechliche Chroma-Semantik."""
    vectorize_service.reset_chroma_cache()
    client = chromadb.EphemeralClient()
    name = f"test_{id(test_settings)}"
    collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    with patch("app.services.vectorize_service._get_collection_sync", return_value=collection), \
         patch("app.services.vectorize_service._check_chromadb_reachable_async", AsyncMock(return_value=True)):
        yield collection
    client.delete_collection(name)
    vectorize_service.reset_chroma_cache()


def _probe_ok(*args, **kwargs):
    return OllamaProbe(reachable=True, models=["x:latest"], missing=[])


@pytest.fixture
def model_ready():
    """Embedding-Modell gilt als installiert (run_initial_vectorize wartet sonst)."""
    with patch("app.services.vectorize_service.probe_ollama", AsyncMock(side_effect=_probe_ok)) as probe:
        yield probe


def _fake_embed(dim: int = 3):
    async def embed(texts, settings):
        return [[float(i + 1)] * dim for i in range(len(texts))]
    return embed


async def _make_doc(session, doc_id="doc-1", ocr="Satz eins. " * 200, **kw) -> Document:
    fields = {"title": "Rechnung", "issuer": None, **kw}
    return await make_document(session, commit=True, id=doc_id, ocr_text=ocr, **fields)


class TestChunkTextProgress:
    def test_large_overlap_terminates(self):
        """(200, 150) lief frueher endlos (Start bewegte sich nicht vorwaerts)."""
        text = "Wort " * 2000
        chunks = chunk_text(text, chunk_size=200, overlap=150)
        assert 10 < len(chunks) < 200
        assert all(len(c) <= 200 for c in chunks)

    def test_overlap_equal_to_size_terminates(self):
        assert chunk_text("x" * 1000, chunk_size=100, overlap=100)


class TestMetadataChunk:
    def test_enum_values_not_enum_names(self):
        doc = SimpleNamespace(
            title="T", document_type=DocumentType.ARZTRECHNUNG, issuer=None, amount=None, currency=None,
            document_date=date(2024, 1, 1), summary=None, tax_relevant=True,
            tax_category=TaxCategory.AUSSERGEWOEHNLICHE_BELASTUNGEN,
        )
        text = _build_metadata_chunk(doc, ["arzt"])
        assert "Typ: ARZTRECHNUNG" in text
        assert "Steuerkategorie: Aussergewoehnliche_Belastungen" in text
        assert "TaxCategory." not in text and "DocumentType." not in text
        assert "Tags: arzt" in text


class TestVectorizeDocument:
    async def test_embedding_failure_keeps_existing_vectors(self, test_settings, chroma, db_session, test_session_factory):
        doc = await _make_doc(db_session)
        with patch("app.services.vectorize_service.embed_batch", _fake_embed()):
            first = await vectorize_document(doc, test_settings, test_session_factory)
        assert chroma.count() == first > 1

        failing = AsyncMock(side_effect=OllamaError(OllamaErrorKind.UNREACHABLE, "down"))
        with patch("app.services.vectorize_service.embed_batch", failing):
            with pytest.raises(VectorizeError):
                await vectorize_document(doc, test_settings, test_session_factory)
        assert chroma.count() == first  # nichts geloescht

    async def test_shorter_text_removes_stale_tail_chunks(self, test_settings, chroma, db_session, test_session_factory):
        doc = await _make_doc(db_session)
        with patch("app.services.vectorize_service.embed_batch", _fake_embed()):
            many = await vectorize_document(doc, test_settings, test_session_factory)
            doc.ocr_text = "Kurz."
            few = await vectorize_document(doc, test_settings, test_session_factory)
        assert many > few == 2
        assert sorted(chroma.get()["ids"]) == ["doc-1_chunk_0", "doc-1_chunk_1"]

    async def test_unreachable_chroma_raises(self, test_settings):
        doc = SimpleNamespace(id="x")
        with patch("app.services.vectorize_service._check_chromadb_reachable_async", AsyncMock(return_value=False)):
            with pytest.raises(VectorizeError):
                await vectorize_document(doc, test_settings)

    async def test_fresh_document_tags_loaded_without_lazy_load(self, test_settings, chroma, test_session_factory):
        """Tags via Junction-Table: kein MissingGreenlet/RuntimeWarning, 'Tags:' im Metadaten-Chunk."""
        async with test_session_factory() as session:
            tag = Tag(name="versicherung")
            session.add(tag)
            doc = await _make_doc(session, ocr="Text.")
            session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
            await session.commit()
            fresh = await make_document(session, id="doc-2", title="Neu", issuer=None, ocr_text="Neu.")
            session.add(DocumentTag(document_id="doc-2", tag_id=tag.id))
            await session.commit()

            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                with patch("app.services.vectorize_service.embed_batch", _fake_embed()):
                    await vectorize_document(fresh, test_settings, test_session_factory)

        meta = chroma.get(ids=["doc-2_chunk_0"], include=["documents", "metadatas"])
        assert "Tags: versicherung" in meta["documents"][0]
        assert meta["metadatas"][0]["chunk_type"] == "metadata"
        assert set(meta["metadatas"][0]) <= {"doc_id", "chunk_type", "chunk_index", "filing_scope_id"}


class TestScopeAndRevectorize:
    async def test_update_scope_metadata_without_reembedding(self, test_settings, chroma, db_session, test_session_factory):
        from app.models.filing_scope import FilingScope
        db_session.add(FilingScope(id="s-1", name="Privat", slug="privat"))
        await db_session.commit()
        doc = await _make_doc(db_session, filing_scope_id="s-1")
        embed = _fake_embed()
        with patch("app.services.vectorize_service.embed_batch", embed):
            chunks = await vectorize_document(doc, test_settings, test_session_factory)

        with patch("app.services.vectorize_service.embed_batch", AsyncMock()) as no_embed:
            assert await update_scope_metadata(["doc-1"], "s-2", test_settings) == chunks
            no_embed.assert_not_awaited()
        assert chroma.get(where={"filing_scope_id": "s-2"})["ids"]

        await update_scope_metadata(["doc-1"], None, test_settings)
        assert not any("filing_scope_id" in m for m in chroma.get(include=["metadatas"])["metadatas"])

    async def test_revectorize_picks_up_edits_and_skips_deleted(self, test_settings, chroma, db_session, test_session_factory):
        doc = await _make_doc(db_session, ocr="Text.")
        with patch("app.services.vectorize_service.embed_batch", _fake_embed()):
            await vectorize_document(doc, test_settings, test_session_factory)
            doc.title = "Korrigierter Titel"
            await db_session.commit()
            assert await revectorize_document_safe("doc-1", test_settings, test_session_factory) == 2
            meta_text = chroma.get(ids=["doc-1_chunk_0"], include=["documents"])["documents"][0]
            assert "Korrigierter Titel" in meta_text

            doc.status = DocumentStatus.DELETED
            await db_session.commit()
            assert await revectorize_document_safe("doc-1", test_settings, test_session_factory) is None
            assert await revectorize_document_safe("gibt-es-nicht", test_settings, test_session_factory) is None

    async def test_revectorize_swallows_failures(self, test_settings, chroma, db_session, test_session_factory):
        await _make_doc(db_session)
        with patch("app.services.vectorize_service.embed_batch",
                   AsyncMock(side_effect=OllamaError(OllamaErrorKind.TIMEOUT, "slow"))):
            assert await revectorize_document_safe("doc-1", test_settings, test_session_factory) is None


class TestCollectionCount:
    def test_error_is_none_not_zero(self, test_settings):
        vectorize_service.reset_chroma_cache()
        with patch("app.services.vectorize_service._get_collection_sync", side_effect=Exception("down")):
            assert get_collection_count(test_settings) is None


class TestInitialVectorize:
    @pytest.fixture(autouse=True)
    async def _stop_tasks(self, model_ready):
        yield
        await shutdown_tasks()

    async def test_unreachable_chroma_does_not_vectorize(self, test_settings, test_session_factory):
        with patch("app.services.vectorize_service.get_collection_count", return_value=None),              patch("app.services.vectorize_service.vectorize_all_documents", AsyncMock()) as run_all:
            assert await run_initial_vectorize(test_session_factory, test_settings, delay=0, count_attempts=2) is None
        run_all.assert_not_awaited()

    async def test_empty_collection_vectorizes_as_background_task(
        self, test_settings, chroma, db_session, test_session_factory,
    ):
        await _make_doc(db_session, ocr="Text.")

        with patch("app.services.vectorize_service.embed_batch", _fake_embed()):
            task = await run_initial_vectorize(test_session_factory, test_settings, delay=0)
            assert task.kind == vectorize_service.REBUILD_TASK_KIND
            await wait_for_task(task.id, timeout=10)

        final = await get_task(test_session_factory, task.id)
        assert final.status == TaskStatus.SUCCEEDED, final.error
        assert final.result == {"total": 1, "processed": 1, "chunks": 2, "failed": 0}
        assert final.progress_text == "1 von 1 Dokumenten verarbeitet"

    async def test_skipped_when_rebuild_running(self, test_settings, test_session_factory):
        release = asyncio.Event()

        async def slow(*args, **kwargs):
            await release.wait()
            return {"total": 0, "processed": 0, "chunks": 0, "failed": 0}

        with patch("app.services.vectorize_service.get_collection_count", return_value=0),              patch("app.services.vectorize_service.vectorize_all_documents", slow):
            running = await vectorize_service.start_vector_rebuild(test_session_factory, test_settings)
            assert await run_initial_vectorize(test_session_factory, test_settings, delay=0) is None
            release.set()
            await wait_for_task(running.id, timeout=10)

    async def test_count_runs_off_event_loop(self, test_settings, test_session_factory):
        calls = []
        original = asyncio.to_thread

        async def tracking(func, *args, **kwargs):
            calls.append(func)
            return await original(func, *args, **kwargs)

        with patch("app.services.vectorize_service.asyncio.to_thread", tracking),              patch("app.services.vectorize_service.get_collection_count", return_value=5) as count:
            await run_initial_vectorize(test_session_factory, test_settings, delay=0)
        assert calls == [count]


    async def test_waits_for_embedding_model_before_rebuild(
        self, test_settings, test_session_factory, model_ready,
    ):
        """Modellwechsel: das neue Embedding-Modell wird noch gepullt. Der Rebuild
        darf erst danach starten — und auch dann, wenn in der Wartezeit ein neues
        Dokument die Collection gefuellt hat (Altbestand fehlt sonst dauerhaft)."""
        probes = iter([
            OllamaProbe(reachable=False, error="Ollama nicht erreichbar"),
            OllamaProbe(reachable=True, models=[], missing=[test_settings.EMBEDDING_MODEL]),
            _probe_ok(),
        ])
        model_ready.side_effect = lambda *a, **kw: next(probes)
        counts = iter([0, 7])  # nach der Wartezeit waere die Collection nicht mehr leer
        with patch("app.services.vectorize_service.get_collection_count", side_effect=lambda s: next(counts)),              patch("app.services.vectorize_service.start_vector_rebuild", AsyncMock(return_value=SimpleNamespace(id="t1"))) as start:
            assert await run_initial_vectorize(
                test_session_factory, test_settings, delay=0, model_poll=0,
            ) == SimpleNamespace(id="t1")
        assert model_ready.await_count == 3
        assert model_ready.await_args.kwargs["required"] == [test_settings.EMBEDDING_MODEL]
        start.assert_awaited_once()

    async def test_filled_collection_does_not_wait_for_model(
        self, test_settings, test_session_factory, model_ready,
    ):
        with patch("app.services.vectorize_service.get_collection_count", return_value=3):
            assert await run_initial_vectorize(test_session_factory, test_settings, delay=0) is None
        model_ready.assert_not_awaited()


class TestEmbeddingModelSwitch:
    """Wechsel von EMBEDDING_MODEL auf einer bestehenden Installation: eigene
    Collection pro Modell -> neue Collection leer -> run_initial_vectorize baut
    den Index beim Start automatisch neu auf (echter embedded PersistentClient)."""

    @pytest.fixture(autouse=True)
    async def _stop_tasks(self, model_ready):
        vectorize_service.reset_chroma_cache()
        yield
        await shutdown_tasks()
        vectorize_service.reset_chroma_cache()

    async def test_switch_revectorizes_into_new_collection(
        self, test_settings, tmp_path, db_session, test_session_factory,
    ):
        test_settings.CHROMADB_MODE = "embedded"
        test_settings.CHROMADB_PATH = str(tmp_path / "chromadb")
        test_settings.EMBEDDING_MODEL = "bge-m3"
        await _make_doc(db_session, doc_id="doc-a", ocr="Stromrechnung der Stadtwerke. " * 5)
        await _make_doc(db_session, doc_id="doc-b", ocr="Kfz-Versicherung fuer den Golf. " * 5)

        async def run_startup(dim: int):
            with patch("app.services.vectorize_service.embed_batch", _fake_embed(dim)):
                task = await run_initial_vectorize(test_session_factory, test_settings, delay=0)
                if task is not None:
                    await wait_for_task(task.id, timeout=30)
            return task

        first = await run_startup(dim=3)
        assert first is not None
        old_count = get_collection_count(test_settings)
        assert old_count and old_count > 0
        # Neustart mit gleichem Modell: Collection gefuellt -> nichts zu tun.
        assert await run_startup(dim=3) is None

        # Modellwechsel (andere Dimension, wie bge-m3 1024 -> qwen3-embedding:4b 2560)
        test_settings.EMBEDDING_MODEL = "qwen3-embedding:4b"
        second = await run_startup(dim=5)
        assert second is not None, "Modellwechsel hat keine Neu-Vektorisierung ausgeloest"
        final = await get_task(test_session_factory, second.id)
        assert final.status == TaskStatus.SUCCEEDED, final.error
        assert final.result["total"] == 2 and final.result["failed"] == 0
        assert get_collection_count(test_settings) == final.result["chunks"]

        # Die alte Collection bleibt liegen (dokumentiert in CLAUDE.md/FAQ).
        client = vectorize_service._get_chroma_client(test_settings)
        names = {getattr(c, "name", c) for c in client.list_collections()}
        assert {"documents_bge-m3", "documents_qwen3-embedding_4b"} <= names
