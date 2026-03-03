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
