"""API-Erweiterungen der Etappe 2 (Spezifikation 12): Einstellungen der
automatischen Pruefung, Review-Statistik mit Stapeln, Vorschlagsdetails."""

import json
import uuid

from app.models.split_batch import SplitBatch, SplitBatchStatus, SplitOrigin


def _batch_row(status: SplitBatchStatus, **kw) -> SplitBatch:
    pages = [
        {"n": n, "w": 591.0, "h": 840.0, "rot": 0, "blank": n == 3, "source": "none" if n == 3 else "ocr",
         "conf": 0.8, "ink": 0.03, "sig": {"counter": [n, 4]} if n != 3 else None}
        for n in range(1, 5)
    ]
    proposal = {
        "boundaries": [
            {"after_page": 1, "score": 3.1, "score_without_counter": -2.0, "p": 0.957, "decision": "cut",
             "reasons": ["Seitenzähler beginnt neu: Seite 1"], "llm": False},
            {"after_page": 2, "score": -0.5, "score_without_counter": -0.5, "p": 0.38, "decision": "uncertain",
             "reasons": ["Anschrift auf Seite 3"], "llm": False},
            {"after_page": 3, "score": -8.0, "score_without_counter": -4.0, "p": 0.0003, "decision": "none",
             "reasons": [], "llm": False},
        ],
        "duplex": False, "duplex_conf": 0.0, "partial_from": None, "blank_kinds": {"3": "back"},
    }
    return SplitBatch(
        id=str(uuid.uuid4()), origin=SplitOrigin.AUTO, status=status, source_job_id=None,
        job_source="WATCH_FOLDER", original_filename="stapel.pdf", file_path="x", file_hash="h" * 64,
        page_count=4, pages_done=4, pages=json.dumps(pages), proposal=json.dumps(proposal), **kw,
    )


async def _add(factory, *batches):
    async with factory() as session:
        session.add_all(batches)
        await session.commit()


async def test_split_settings_roundtrip(client, test_settings):
    response = await client.get("/api/system/split-settings")
    assert response.status_code == 200
    assert response.json() == {
        "auto_enabled": True, "min_pages": 3, "use_llm": True, "max_pages": test_settings.SPLIT_MAX_PAGES,
    }

    response = await client.put("/api/system/split-settings", json={"auto_enabled": False, "min_pages": 5})
    assert response.status_code == 200, response.text
    assert (await client.get("/api/system/split-settings")).json()["min_pages"] == 5
    assert (await client.get("/api/system/split-settings")).json()["auto_enabled"] is False


async def test_split_settings_llm_switch(client, test_settings):
    body = {"auto_enabled": True, "min_pages": 3, "use_llm": False}
    response = await client.put("/api/system/split-settings", json=body)
    assert response.status_code == 200 and response.json()["use_llm"] is False
    # Ohne use_llm (aeltere Clients): bleibt unveraendert
    response = await client.put("/api/system/split-settings", json={"auto_enabled": True, "min_pages": 4})
    assert response.json()["use_llm"] is False
    assert (await client.get("/api/system/split-settings")).json()["use_llm"] is False
    test_settings.SPLIT_USE_LLM = False
    await client.put("/api/system/split-settings", json={**body, "use_llm": True})
    assert (await client.get("/api/system/split-settings")).json()["use_llm"] is True  # DB vor Config


async def test_split_settings_validation(client, test_settings):
    assert (await client.put("/api/system/split-settings", json={"auto_enabled": True, "min_pages": 1})).status_code == 422
    too_many = test_settings.SPLIT_MAX_PAGES + 1
    response = await client.put("/api/system/split-settings", json={"auto_enabled": True, "min_pages": too_many})
    assert response.status_code == 422
    assert "höchstens" in response.json()["detail"]


async def test_review_stats_count_proposed_batches(client, test_session_factory):
    await _add(
        test_session_factory,
        _batch_row(SplitBatchStatus.PROPOSED), _batch_row(SplitBatchStatus.PROPOSED),
        _batch_row(SplitBatchStatus.DETECTING), _batch_row(SplitBatchStatus.DONE),
    )
    stats = (await client.get("/api/review/stats")).json()
    assert stats["split_batches"] == 2


async def test_list_and_detail_carry_proposal(client, test_session_factory):
    batch = _batch_row(SplitBatchStatus.PROPOSED)
    await _add(test_session_factory, batch)

    items = (await client.get("/api/split", params={"status": "PROPOSED"})).json()["items"]
    assert [(i["id"], i["proposed_parts"], i["uncertain"]) for i in items] == [(batch.id, 2, 1)]

    detail = (await client.get(f"/api/split/{batch.id}")).json()
    assert [b["decision"] for b in detail["boundaries"]] == ["cut", "uncertain", "none"]
    assert detail["boundaries"][0]["score"] == 3.1
    assert "score_without_counter" not in detail["boundaries"][0]  # interner Wert
    assert [p["blank_kind"] for p in detail["pages"]] == [None, None, "back", None]
    assert detail["pages"][0]["counter"] == [1, 4]
