import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.email_relevance_service import check_email_relevance


@pytest.mark.asyncio
async def test_relevant_email(test_settings):
    mock_response = json.dumps({"relevant": True, "reason": "Rechnung als PDF-Anhang"})
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await check_email_relevance(
            sender="firma@example.com",
            subject="Ihre Rechnung Nr. 2024-001",
            body_snippet="Anbei erhalten Sie Ihre Rechnung.",
            attachment_names=["Rechnung_2024-001.pdf"],
            settings=test_settings,
        )
    assert result["relevant"] is True
    assert "reason" in result


@pytest.mark.asyncio
async def test_irrelevant_email(test_settings):
    mock_response = json.dumps({"relevant": False, "reason": "Newsletter"})
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await check_email_relevance(
            sender="news@shop.de",
            subject="Unsere Top-Angebote diese Woche",
            body_snippet="Entdecken Sie unsere neuesten Angebote...",
            attachment_names=[],
            settings=test_settings,
        )
    assert result["relevant"] is False


@pytest.mark.asyncio
async def test_llm_failure_returns_relevant(test_settings):
    """Bei LLM-Fehler: sicherheitshalber als relevant markieren."""
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=None):
        result = await check_email_relevance(
            sender="test@test.com",
            subject="Test",
            body_snippet="Test",
            attachment_names=[],
            settings=test_settings,
        )
    assert result["relevant"] is True
    assert "fehler" in result["reason"].lower() or "fallback" in result["reason"].lower()


@pytest.mark.asyncio
async def test_malformed_json_returns_relevant(test_settings):
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value="not json"):
        result = await check_email_relevance(
            sender="test@test.com",
            subject="Test",
            body_snippet="",
            attachment_names=[],
            settings=test_settings,
        )
    assert result["relevant"] is True


@pytest.mark.asyncio
async def test_string_boolean_is_not_coerced(test_settings):
    """bool("false") ist True — String-Werte duerfen nicht als Boolean gelten."""
    mock_response = json.dumps({"relevant": "false", "reason": "Newsletter"})
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await check_email_relevance(
            sender="news@shop.de", subject="Angebote", body_snippet="",
            attachment_names=[], settings=test_settings,
        )
    assert result["relevant"] is True
    assert "fallback" in result["reason"].lower()


@pytest.mark.asyncio
async def test_uses_json_schema_and_truncates_body_once(test_settings):
    mock = AsyncMock(return_value=json.dumps({"relevant": False, "reason": "x"}))
    with patch("app.services.email_relevance_service.call_llm", mock):
        await check_email_relevance(
            sender="a@b.de", subject="s", body_snippet="A" * 5000,
            attachment_names=[], settings=test_settings,
        )
    schema = mock.await_args.kwargs["schema"]
    assert schema["properties"]["relevant"] == {"type": "boolean"}
    assert schema["required"] == ["relevant", "reason"]
    prompt = mock.await_args.args[0]
    assert "A" * 1000 in prompt and "A" * 1001 not in prompt


def test_prompt_has_no_garbled_injection_sentence():
    from app.services.llm_service import load_prompt_template

    template = load_prompt_template("email_relevance.txt")
    assert "bertanken" not in template
    assert "Rolle übernehmen/wechseln" in template
