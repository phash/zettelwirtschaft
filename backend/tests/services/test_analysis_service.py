import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services.analysis_service import (
    AnalysisResult,
    _parse_analysis_json,
    _truncate_text,
    analyze_document,
)
from app.services.ocr_service import OcrResult, PageText


class TestTruncateText:
    def test_short_text_unchanged(self):
        text = "Kurzer Text"
        assert _truncate_text(text) == text

    def test_long_text_truncated(self):
        text = "A" * 5000
        result = _truncate_text(text, max_chars=4000)
        assert len(result) < 5000
        assert "[...Text gekuerzt...]" in result
        assert result.startswith("A" * 2000)
        assert result.endswith("A" * 2000)

    def test_exact_boundary(self):
        text = "A" * 4000
        assert _truncate_text(text, max_chars=4000) == text


class TestParseAnalysisJson:
    def test_valid_json(self):
        raw = '{"document_type": "RECHNUNG", "confidence": 0.9}'
        result = _parse_analysis_json(raw)
        assert result is not None
        assert result["document_type"] == "RECHNUNG"

    def test_json_in_markdown(self):
        raw = 'Hier ist die Analyse:\n```json\n{"document_type": "RECHNUNG"}\n```\nEnde.'
        result = _parse_analysis_json(raw)
        assert result is not None
        assert result["document_type"] == "RECHNUNG"

    def test_json_with_surrounding_text(self):
        raw = 'Analyse: {"document_type": "QUITTUNG"} fertig.'
        result = _parse_analysis_json(raw)
        assert result is not None
        assert result["document_type"] == "QUITTUNG"

    def test_invalid_json(self):
        raw = "Das ist kein JSON."
        result = _parse_analysis_json(raw)
        assert result is None


class TestAnalysisResult:
    def test_defaults(self):
        result = AnalysisResult()
        assert result.document_type == "SONSTIGES"
        assert result.confidence == 0.0
        assert result.needs_review is False
        assert result.tags == []

    def test_to_dict(self):
        result = AnalysisResult(
            document_type="RECHNUNG",
            confidence=0.9,
            title="Test",
        )
        d = result.to_dict()
        assert d["document_type"] == "RECHNUNG"
        assert d["confidence"] == 0.9
        assert d["title"] == "Test"
        assert "tags" in d
        assert "warranty_info" in d


class TestAnalyzeDocument:
    async def test_full_pipeline_success(self, test_settings: Settings, tmp_path: Path):
        """Vollstaendige Pipeline: OCR + LLM-Analyse erfolgreich."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"dummy")

        ocr_result = OcrResult(
            full_text="Rechnung Nr. 2024-001\nFirma Muster GmbH\nBetrag: 119,00 EUR",
            pages=[PageText(page_number=1, text="...", confidence=0.95)],
            average_confidence=0.95,
            page_count=1,
        )

        llm_response = json.dumps({
            "document_type": "RECHNUNG",
            "confidence": 0.92,
            "title": "Rechnung Nr. 2024-001",
            "sender": "Firma Muster GmbH",
            "recipient": None,
            "document_date": "2024-01-15",
            "amount": 119.00,
            "currency": "EUR",
            "reference_number": "2024-001",
            "tags": ["rechnung"],
            "summary": "Rechnung ueber 119 EUR",
            "tax_relevant": False,
            "tax_category": None,
            "tax_year": None,
            "warranty_info": {"has_warranty": False},
            "needs_review": False,
            "review_questions": [],
        })

        with patch(
            "app.services.analysis_service.extract_text",
            new_callable=AsyncMock,
            return_value=ocr_result,
        ), patch(
            "app.services.analysis_service.call_llm",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            ocr, analysis = await analyze_document(test_file, "pdf", test_settings)

        assert ocr is not None
        assert ocr.full_text == ocr_result.full_text
        assert analysis is not None
        assert analysis.document_type == "RECHNUNG"
        assert analysis.confidence == 0.92
        assert analysis.needs_review is False

    async def test_ocr_fails_returns_review(self, test_settings: Settings, tmp_path: Path):
        """OCR-Fehler fuehrt zu NEEDS_REVIEW."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"dummy")

        with patch(
            "app.services.analysis_service.extract_text",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ocr, analysis = await analyze_document(test_file, "pdf", test_settings)

        assert ocr is None
        assert analysis is not None
        assert analysis.needs_review is True
        assert len(analysis.review_questions) > 0

    async def test_llm_fails_returns_review(self, test_settings: Settings, tmp_path: Path):
        """LLM-Ausfall fuehrt zu NEEDS_REVIEW mit OCR-Text."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"dummy")

        ocr_result = OcrResult(
            full_text="Irgendein Text",
            pages=[PageText(page_number=1, text="Irgendein Text", confidence=0.9)],
            average_confidence=0.9,
            page_count=1,
        )

        with patch(
            "app.services.analysis_service.extract_text",
            new_callable=AsyncMock,
            return_value=ocr_result,
        ), patch(
            "app.services.analysis_service.call_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ocr, analysis = await analyze_document(test_file, "pdf", test_settings)

        assert ocr is not None
        assert ocr.full_text == "Irgendein Text"
        assert analysis is not None
        assert analysis.needs_review is True

    async def test_low_confidence_triggers_review(self, test_settings: Settings, tmp_path: Path):
        """Niedrige Konfidenz (<0.7) fuehrt zu NEEDS_REVIEW."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"dummy")
        test_settings.CONFIDENCE_THRESHOLD = 0.7

        ocr_result = OcrResult(
            full_text="Unleserlicher Text...",
            pages=[PageText(page_number=1, text="...", confidence=0.4)],
            average_confidence=0.4,
            page_count=1,
        )

        llm_response = json.dumps({
            "document_type": "SONSTIGES",
            "confidence": 0.3,
            "title": "Unbekanntes Dokument",
            "sender": None,
            "recipient": None,
            "document_date": None,
            "amount": None,
            "currency": None,
            "reference_number": None,
            "tags": [],
            "summary": "Nicht lesbar",
            "tax_relevant": False,
            "tax_category": None,
            "tax_year": None,
            "warranty_info": None,
            "needs_review": False,
            "review_questions": [],
        })

        with patch(
            "app.services.analysis_service.extract_text",
            new_callable=AsyncMock,
            return_value=ocr_result,
        ), patch(
            "app.services.analysis_service.call_llm",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            ocr, analysis = await analyze_document(test_file, "pdf", test_settings)

        assert analysis is not None
        assert analysis.needs_review is True
        assert analysis.confidence == 0.3

    async def test_invalid_document_type_becomes_sonstiges(
        self, test_settings: Settings, tmp_path: Path
    ):
        """Unbekannter Dokumenttyp wird zu SONSTIGES."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"dummy")

        ocr_result = OcrResult(
            full_text="Ein Text",
            pages=[PageText(page_number=1, text="Ein Text", confidence=0.9)],
            average_confidence=0.9,
            page_count=1,
        )

        llm_response = json.dumps({
            "document_type": "UNBEKANNT",
            "confidence": 0.8,
            "title": "Test",
        })

        with patch(
            "app.services.analysis_service.extract_text",
            new_callable=AsyncMock,
            return_value=ocr_result,
        ), patch(
            "app.services.analysis_service.call_llm",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            ocr, analysis = await analyze_document(test_file, "pdf", test_settings)

        assert analysis is not None
        assert analysis.document_type == "SONSTIGES"

    async def test_combined_fail_falls_back_to_sequential(
        self, test_settings: Settings, tmp_path: Path
    ):
        """Bei fehlgeschlagener kombinierter Analyse wird sequentiell versucht."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"dummy")

        ocr_result = OcrResult(
            full_text="Rechnung Text",
            pages=[PageText(page_number=1, text="Rechnung Text", confidence=0.9)],
            average_confidence=0.9,
            page_count=1,
        )

        # Erster Aufruf (kombiniert) gibt unparsbares JSON zurueck,
        # danach geben sequentielle Aufrufe gueltige Ergebnisse
        call_count = 0

        async def mock_call_llm(prompt, settings, system_prompt=None, schema=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Kombinierter Prompt schlaegt fehl
                return "Das ist kein gueltiges JSON"
            elif call_count == 2:
                # Klassifikation
                return json.dumps({"document_type": "RECHNUNG", "confidence": 0.85})
            elif call_count == 3:
                # Metadaten
                return json.dumps({
                    "title": "Eine Rechnung",
                    "sender": "Test GmbH",
                    "tags": ["rechnung"],
                })
            elif call_count == 4:
                # Steuer
                return json.dumps({"tax_relevant": False})
            elif call_count == 5:
                # Garantie
                return json.dumps({"has_warranty": False})
            return None

        with patch(
            "app.services.analysis_service.extract_text",
            new_callable=AsyncMock,
            return_value=ocr_result,
        ), patch(
            "app.services.analysis_service.call_llm",
            side_effect=mock_call_llm,
        ):
            ocr, analysis = await analyze_document(test_file, "pdf", test_settings)

        assert analysis is not None
        assert analysis.document_type == "RECHNUNG"
        assert analysis.title == "Eine Rechnung"
        assert analysis.needs_review is True  # Sequentiell setzt immer needs_review


class TestFewShotCorrections:
    """Few-Shot-Pipeline aus CorrectionMappings."""

    def test_format_empty_returns_empty(self):
        from app.services.analysis_service import _format_correction_examples
        assert _format_correction_examples(None) == ""
        assert _format_correction_examples([]) == ""

    def test_format_includes_field_and_count(self):
        from app.services.analysis_service import _format_correction_examples
        out = _format_correction_examples([
            {"field": "sender", "original": "BSH GmbH", "corrected": "Bosch", "count": 5},
        ])
        assert "sender" in out
        assert "BSH GmbH" in out
        assert "Bosch" in out
        assert "5x" in out

    @pytest.mark.asyncio
    async def test_load_examples_returns_empty_for_none_session(self):
        from app.services.analysis_service import _load_correction_examples
        result = await _load_correction_examples(None)
        assert result == []

    @pytest.mark.asyncio
    async def test_combined_analysis_passes_corrections(self, test_settings):
        """Stellt sicher dass corrections im Prompt landen."""
        from app.services.analysis_service import _try_combined_analysis

        captured_prompt = {}

        async def fake_call_llm(prompt, settings, system_prompt=None, schema=None):
            captured_prompt["text"] = prompt
            return '{"document_type": "RECHNUNG", "confidence": 0.9, "needs_review": false}'

        corrections = [{"field": "sender", "original": "X", "corrected": "Y", "count": 3}]
        with patch("app.services.analysis_service.call_llm", side_effect=fake_call_llm):
            result = await _try_combined_analysis("Test OCR", test_settings, corrections=corrections)

        assert result is not None
        assert result.document_type == "RECHNUNG"
        # Korrekturen muessen im Prompt enthalten sein
        assert "sender" in captured_prompt["text"]
        assert "Y" in captured_prompt["text"]


class TestHybridSearch:
    """RRF-Logik in rag_service."""

    def test_rrf_boosts_chunks_in_fts_results(self):
        from app.services.rag_service import _apply_rrf
        chunks = [
            {"doc_id": "doc-A", "text": "a"},  # vec_rank=0
            {"doc_id": "doc-B", "text": "b"},  # vec_rank=1
            {"doc_id": "doc-C", "text": "c"},  # vec_rank=2
        ]
        # FTS5 reiht doc-C ganz oben — sollte trotz schlechtem vec_rank profitieren
        result = _apply_rrf(chunks, fts_doc_ids=["doc-C"], target_k=3)
        assert len(result) == 3
        # doc-A ist immer noch vorne (1/60 + 0 vs 1/62 + 1/60),
        # aber doc-C ueberholt doc-B durch FTS-Boost
        c_idx = next(i for i, c in enumerate(result) if c["doc_id"] == "doc-C")
        b_idx = next(i for i, c in enumerate(result) if c["doc_id"] == "doc-B")
        assert c_idx < b_idx

    def test_rrf_without_fts_ids_passes_through(self):
        from app.services.rag_service import _apply_rrf
        chunks = [{"doc_id": "doc-A", "text": "a"}, {"doc_id": "doc-B", "text": "b"}]
        result = _apply_rrf(chunks, fts_doc_ids=[], target_k=10)
        assert result == chunks  # unveraendert


class TestVerifierPass:
    """Tests fuer den optionalen LLM-Verifier (analysis_service._verify_analysis)."""

    @pytest.mark.asyncio
    async def test_empty_issues_returns_empty(self, test_settings):
        from app.services.analysis_service import _verify_analysis, AnalysisResult
        analysis = AnalysisResult(
            document_type="RECHNUNG", confidence=0.4, sender="Test GmbH",
            amount=99.0, currency="EUR",
        )
        with patch("app.services.analysis_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = '{"issues": []}'
            issues = await _verify_analysis("Rechnung Text", analysis, test_settings)
        assert issues == []

    @pytest.mark.asyncio
    async def test_issues_returned_as_strings(self, test_settings):
        from app.services.analysis_service import _verify_analysis, AnalysisResult
        analysis = AnalysisResult(document_type="RECHNUNG", confidence=0.3)
        with patch("app.services.analysis_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = (
                '{"issues": ["Aussteller fehlt — bitte ergaenzen", '
                '"Datum nicht erkannt"]}'
            )
            issues = await _verify_analysis("OCR-Text", analysis, test_settings)
        assert len(issues) == 2
        assert "Aussteller" in issues[0]

    @pytest.mark.asyncio
    async def test_issues_capped_at_5(self, test_settings):
        from app.services.analysis_service import _verify_analysis, AnalysisResult
        analysis = AnalysisResult(document_type="RECHNUNG", confidence=0.3)
        with patch("app.services.analysis_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = '{"issues": ["i1","i2","i3","i4","i5","i6","i7"]}'
            issues = await _verify_analysis("OCR-Text", analysis, test_settings)
        assert len(issues) == 5

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self, test_settings):
        from app.services.analysis_service import _verify_analysis, AnalysisResult
        analysis = AnalysisResult(document_type="RECHNUNG", confidence=0.3)
        with patch("app.services.analysis_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = None
            issues = await _verify_analysis("OCR-Text", analysis, test_settings)
        assert issues == []

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self, test_settings):
        from app.services.analysis_service import _verify_analysis, AnalysisResult
        analysis = AnalysisResult(document_type="RECHNUNG", confidence=0.3)
        with patch("app.services.analysis_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = "kein json"
            issues = await _verify_analysis("OCR-Text", analysis, test_settings)
        assert issues == []

    @pytest.mark.asyncio
    async def test_none_fields_rendered_as_text(self, test_settings):
        """H-07: None-Felder duerfen nicht als String 'None' im Prompt landen."""
        from app.services.analysis_service import _verify_analysis, AnalysisResult
        analysis = AnalysisResult(
            document_type="RECHNUNG", confidence=0.3,
            sender=None, amount=None, currency=None,  # alle None
        )
        captured = {}

        async def fake_llm(prompt, settings, system_prompt=None, schema=None):
            captured["prompt"] = prompt
            return '{"issues": []}'

        with patch("app.services.analysis_service.call_llm", side_effect=fake_llm):
            await _verify_analysis("OCR-Text", analysis, test_settings)

        assert "(nicht erkannt)" in captured["prompt"]
        # Kein literales "None None" mehr
        assert "None None" not in captured["prompt"]


class TestRateLimitTrustedIP:
    """Tests fuer trusted_client_ip (NEW-002 X-Real-IP-Defense)."""

    def _request(self, host, headers=None):
        from unittest.mock import MagicMock
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = host
        req.headers = headers or {}
        return req

    def test_real_ip_trusted_when_from_docker_network(self):
        from app.core.rate_limit import trusted_client_ip
        req = self._request("172.18.0.5", {"X-Real-IP": "192.168.1.42"})
        assert trusted_client_ip(req) == "192.168.1.42"

    def test_real_ip_trusted_when_from_localhost(self):
        from app.core.rate_limit import trusted_client_ip
        req = self._request("127.0.0.1", {"X-Real-IP": "192.168.1.42"})
        assert trusted_client_ip(req) == "192.168.1.42"

    def test_real_ip_ignored_when_from_external(self):
        from app.core.rate_limit import trusted_client_ip
        req = self._request("8.8.8.8", {"X-Real-IP": "spoofed-ip"})
        # Externe Connection -> X-Real-IP ignoriert, fallback auf Socket-Peer
        assert trusted_client_ip(req) == "8.8.8.8"

    def test_no_real_ip_falls_back_to_socket(self):
        from app.core.rate_limit import trusted_client_ip
        req = self._request("172.18.0.5", {})
        assert trusted_client_ip(req) == "172.18.0.5"

    def test_invalid_socket_host_handled(self):
        from app.core.rate_limit import trusted_client_ip
        req = self._request("not-an-ip", {"X-Real-IP": "10.0.0.1"})
        # Untrusted (kann nicht parsen) -> Real-IP ignoriert
        assert trusted_client_ip(req) == "not-an-ip"
