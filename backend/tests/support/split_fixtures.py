"""Gemeinsame Fixtures der Aufteilungs-Tests (Spezifikation 12).

Als Plugin in ``tests/conftest.py`` registriert (``pytest_plugins``), damit
Testmodule die Fixtures nicht aus anderen Testmodulen importieren muessen.

``_pipeline_mocks`` ist bewusst KEIN autouse-Fixture (sonst griffe es in der
ganzen Suite): Module, die es brauchen, aktivieren es per
``pytestmark = pytest.mark.usefixtures("_pipeline_mocks")``.
"""

from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.config import Settings
from app.services.analysis_service import AnalysisResult
from app.services.ocr_service import PageText


@pytest.fixture
def fast_settings(test_settings: Settings) -> Settings:
    test_settings.QUEUE_POLL_INTERVAL = 0
    return test_settings


class OcrMock:
    """Zaehlt OCR-Aufrufe pro Seite; ``fail_on`` wirft einmalig bei Seite n."""

    def __init__(self):
        self.calls: list[int] = []
        self.fail_on: int | None = None

    def render(self, path, n):
        return Image.new("L", (1240, 1754), 255)

    def ocr(self, image, n, languages, exclude_left=0.0):
        self.calls.append(n)
        if self.fail_on == n:
            self.fail_on = None
            raise RuntimeError("Tesseract abgestuerzt")
        return PageText(page_number=n, text=f"Inhalt der Seite {n} mit genug Text", confidence=0.5 + n / 100)


class TextOcr(OcrMock):
    def __init__(self, texts: dict[int, str]):
        super().__init__()
        self.texts = texts

    def ocr(self, image, n, languages, exclude_left=0.0):
        self.calls.append(n)
        return PageText(page_number=n, text=self.texts.get(n, f"Inhalt der Seite {n} mit genug Text"),
                        confidence=0.5 + n / 100)


@pytest.fixture
def ocr_mock():
    mock = OcrMock()
    with patch("app.services.ocr_service.render_pdf_page_for_ocr", side_effect=mock.render), \
            patch("app.services.ocr_service.ocr_page_image", side_effect=mock.ocr):
        yield mock


@pytest.fixture
def text_ocr():
    mock = TextOcr({})
    with patch("app.services.ocr_service.render_pdf_page_for_ocr", side_effect=mock.render), \
            patch("app.services.ocr_service.ocr_page_image", side_effect=mock.ocr):
        yield mock


@pytest.fixture
def _pipeline_mocks():
    """LLM, Thumbnail und Vektorisierung mocken; die normale OCR darf fuer
    aufgeteilte Teile NIE laufen (Seitentext wird wiederverwendet)."""
    analysis = AnalysisResult(document_type="RECHNUNG", confidence=0.9, title="Teil", needs_review=False)
    with patch(
        "app.services.analysis_service._try_combined_analysis",
        new_callable=AsyncMock, return_value=analysis,
    ) as llm, patch(
        "app.services.analysis_service.extract_text",
        new_callable=AsyncMock, side_effect=AssertionError("normale OCR darf nicht laufen"),
    ) as extract, patch(
        "app.services.queue_worker_service.generate_thumbnail", new_callable=AsyncMock, return_value=None,
    ), patch(
        "app.services.vectorize_service.vectorize_document", new_callable=AsyncMock, return_value=0,
    ), patch(
        "app.services.vectorize_service.delete_document_vectors", new_callable=AsyncMock,
    ), patch(
        # KI-Pass der Aufteilung: Ollama "nicht erreichbar" (Tests erreichen nie
        # einen echten Dienst); Tests zum KI-Pass setzen return_value selbst.
        "app.services.split_llm_service.call_llm", new_callable=AsyncMock, return_value=None,
    ) as split_llm:
        yield {"llm": llm, "extract": extract, "split_llm": split_llm}
