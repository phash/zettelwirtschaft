from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.services.file_validation_service import FileValidationError
from app.services.upload_service import process_upload


class TestProcessUpload:
    async def test_successful_upload(
        self,
        sample_pdf: Path,
        test_settings: Settings,
        db_session: AsyncSession,
    ):
        job = await process_upload(
            file_path=sample_pdf,
            original_name="rechnung.pdf",
            file_size=sample_pdf.stat().st_size,
            source=JobSource.UPLOAD,
            settings=test_settings,
            db=db_session,
        )

        assert job.status == JobStatus.PENDING
        assert job.original_filename == "rechnung.pdf"
        assert job.file_type == "pdf"
        assert job.source == JobSource.UPLOAD
        assert Path(job.file_path).exists()

    async def test_upload_creates_db_entry(
        self,
        sample_pdf: Path,
        test_settings: Settings,
        db_session: AsyncSession,
    ):
        job = await process_upload(
            file_path=sample_pdf,
            original_name="test.pdf",
            file_size=sample_pdf.stat().st_size,
            source=JobSource.UPLOAD,
            settings=test_settings,
            db=db_session,
        )
        await db_session.commit()

        result = await db_session.execute(
            select(ProcessingJob).where(ProcessingJob.id == job.id)
        )
        db_job = result.scalar_one()
        assert db_job.original_filename == "test.pdf"

    async def test_invalid_file_raises(
        self,
        sample_invalid_file: Path,
        test_settings: Settings,
        db_session: AsyncSession,
    ):
        with pytest.raises(FileValidationError):
            await process_upload(
                file_path=sample_invalid_file,
                original_name="malware.exe",
                file_size=sample_invalid_file.stat().st_size,
                source=JobSource.UPLOAD,
                settings=test_settings,
                db=db_session,
            )

    async def test_watch_folder_moves_file(
        self,
        sample_jpg: Path,
        test_settings: Settings,
        db_session: AsyncSession,
    ):
        original_path = sample_jpg
        assert original_path.exists()

        job = await process_upload(
            file_path=original_path,
            original_name="foto.jpg",
            file_size=original_path.stat().st_size,
            source=JobSource.WATCH_FOLDER,
            settings=test_settings,
            db=db_session,
        )

        # Originaldatei wurde verschoben
        assert not original_path.exists()
        # Neue Datei existiert im Upload-Dir
        assert Path(job.file_path).exists()
