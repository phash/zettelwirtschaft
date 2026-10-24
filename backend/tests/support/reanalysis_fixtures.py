"""Gemeinsame Fixtures der Neu-Analyse-Tests (gefaelschte Haupt-Pipeline).

Als Plugin in ``tests/conftest.py`` registriert (``pytest_plugins``), damit
Testmodule die Fixtures nicht aus anderen Testmodulen importieren muessen.
``make_analysis`` ist eine normale Hilfsfunktion (kein Fixture).
"""

import pytest

from app.services.analysis_service import AnalysisResult


def make_analysis(**overrides) -> AnalysisResult:
    """Plausibles KI-Ergebnis einer Arztrechnung; ``overrides`` ersetzen Felder."""
    values = dict(
        document_type="ARZTRECHNUNG",
        confidence=0.91,
        title="Arztrechnung Dr. Muster",
        sender="Praxis Dr. Muster",
        recipient="Max Mustermann",
        document_date="2025-02-14",
        amount=95.5,
        currency="EUR",
        reference_number="R-2025-7",
        tags=["arzt", "gesundheit"],
        summary="Rechnung über eine Behandlung.",
        tax_relevant=True,
        tax_category="Aussergewoehnliche_Belastungen",
        tax_year=None,
        warranty_info=None,
        needs_review=False,
        review_questions=[],
    )
    values.update(overrides)
    return AnalysisResult(**values)


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Ersetzt analyze_document (Haupt-Pipeline) und zeichnet die Aufrufe auf."""
    state = {"result": make_analysis(), "calls": []}

    async def fake_analyze(file_path, file_type, settings, filing_scopes=None, session=None, pre_ocr_text=None):
        state["calls"].append({"pre_ocr_text": pre_ocr_text, "session": session, "scopes": filing_scopes})
        return None, state["result"]

    monkeypatch.setattr("app.services.reanalysis_service.analyze_document", fake_analyze)
    return state
