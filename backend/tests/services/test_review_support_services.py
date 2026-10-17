"""Steuerjahr-Ableitung, Korrektur-Lerneffekt (auto_apply) und Benachrichtigungs-Helfer."""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.models.correction_mapping import CorrectionMapping
from app.models.notification import Notification, NotificationType
from app.services.analysis_service import AnalysisResult
from app.services.correction_service import apply_auto_corrections, record_correction
from app.services.notification_service import notify_processing_failed, notify_review_needed
from app.services.tax_year import derive_tax_year, ensure_tax_year
from tests.factories import make_document


class TestTaxYear:
    def test_prefers_document_date(self):
        assert derive_tax_year(date(2023, 12, 31), datetime(2025, 1, 2, tzinfo=timezone.utc)) == 2023

    def test_falls_back_to_created_at(self):
        assert derive_tax_year(None, datetime(2024, 3, 1, tzinfo=timezone.utc)) == 2024

    def test_clamped(self):
        assert derive_tax_year(date(1987, 1, 1), None) == 2000
        assert derive_tax_year(date(date.today().year + 5, 1, 1), None) == date.today().year + 1

    async def test_ensure_never_overwrites_and_only_when_relevant(self, db_session):
        doc = await make_document(db_session, document_date=date(2022, 4, 1), tax_relevant=False)
        ensure_tax_year(doc)
        assert doc.tax_year is None

        doc.tax_relevant = True
        ensure_tax_year(doc)
        assert doc.tax_year == 2022

        doc.tax_year = 2021
        ensure_tax_year(doc)
        assert doc.tax_year == 2021


@pytest.mark.asyncio
class TestCorrections:
    async def test_auto_apply_after_three_corrections(self, db_session):
        for _ in range(3):
            await record_correction(db_session, "issuer", "Amazon EU S.a r.l.", "Amazon")
            await db_session.flush()
        mapping = (await db_session.execute(select(CorrectionMapping))).scalar_one()
        assert mapping.occurrence_count == 3
        assert mapping.auto_apply is True

    async def test_same_value_is_not_recorded(self, db_session):
        await record_correction(db_session, "issuer", "Amazon", " amazon ")
        await db_session.flush()
        assert (await db_session.execute(select(CorrectionMapping))).first() is None

    async def test_apply_auto_corrections(self, db_session):
        db_session.add_all([
            CorrectionMapping(field="issuer", original_value="Amazon EU S.a r.l.", corrected_value="Amazon",
                              occurrence_count=3, auto_apply=True),
            CorrectionMapping(field="filing_scope", original_value="Privat", corrected_value="Praxis",
                              occurrence_count=4, auto_apply=True),
            CorrectionMapping(field="title", original_value="Rechnung", corrected_value="Stromrechnung",
                              occurrence_count=2, auto_apply=False),
        ])
        await db_session.flush()
        analysis = AnalysisResult(
            sender="amazon eu  s.a r.l.", title="Rechnung", filing_scope="Privat", filing_scope_confidence=0.4,
        )

        applied = await apply_auto_corrections(db_session, analysis)

        assert sorted(applied) == ["filing_scope", "issuer"]
        assert analysis.sender == "Amazon"
        assert analysis.title == "Rechnung"  # nur 2x korrigiert -> kein auto_apply
        assert analysis.filing_scope == "Praxis"
        assert analysis.filing_scope_confidence == 1.0
        # Idempotent
        assert await apply_auto_corrections(db_session, analysis) == []

    async def test_no_auto_apply_on_failed_analysis(self, db_session):
        db_session.add(CorrectionMapping(field="title", original_value="X", corrected_value="Y",
                                         occurrence_count=5, auto_apply=True))
        await db_session.flush()
        analysis = AnalysisResult(title="X", llm_error="unreachable")
        assert await apply_auto_corrections(db_session, analysis) == []
        assert analysis.title == "X"


@pytest.mark.asyncio
class TestNotificationHelpers:
    async def test_review_needed_and_failed(self, db_session):
        doc = await make_document(db_session, title="Stromrechnung")
        notify_review_needed(db_session, doc.id, doc.title, open_questions=2)
        notify_processing_failed(db_session, "scan.pdf", "x" * 2000)
        await db_session.commit()

        rows = {n.type: n for n in (await db_session.execute(select(Notification))).scalars().all()}
        review = rows[NotificationType.REVIEW_NEEDED]
        assert review.title == "Bitte prüfen: Stromrechnung"
        assert "2 Rückfragen" in review.message
        assert review.document_id == doc.id
        failed = rows[NotificationType.SYSTEM]
        assert failed.title == "Verarbeitung fehlgeschlagen: scan.pdf"
        assert len(failed.message) < 700


def test_processing_done_removed():
    assert "PROCESSING_DONE" not in NotificationType.__members__
