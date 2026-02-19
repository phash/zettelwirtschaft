import io
import json
import struct
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base, get_db


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    upload_dir = tmp_path / "uploads"
    watch_dir = tmp_path / "watch"
    archive_dir = tmp_path / "archive"
    thumbnail_dir = tmp_path / "thumbnails"
    for d in [upload_dir, watch_dir, archive_dir, thumbnail_dir]:
        d.mkdir()

    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        UPLOAD_DIR=str(upload_dir),
        WATCH_DIR=str(watch_dir),
        ARCHIVE_DIR=str(archive_dir),
        THUMBNAIL_DIR=str(thumbnail_dir),
        OLLAMA_BASE_URL="http://localhost:11434",
        LOG_LEVEL="DEBUG",
    )


@pytest.fixture
async def test_engine(test_settings: Settings):
    engine = create_async_engine(test_settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def test_session_factory(test_engine):
    return async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
async def db_session(test_session_factory) -> AsyncGenerator[AsyncSession]:
    async with test_session_factory() as session:
        yield session


@pytest.fixture
async def client(test_settings: Settings, test_engine, test_session_factory):
    from app.config import get_settings
    from app.main import app

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Erstellt eine minimale gueltige PDF-Datei."""
    pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\nxref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \ntrailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n115\n%%EOF"
    path = tmp_path / "test.pdf"
    path.write_bytes(pdf_content)
    return path


@pytest.fixture
def sample_jpg(tmp_path: Path) -> Path:
    """Erstellt eine minimale gueltige JPEG-Datei."""
    # Minimale JPEG: SOI + APP0 + EOI
    jpg_data = bytearray(b"\xff\xd8\xff\xe0")
    jpg_data += struct.pack(">H", 16)  # Laenge
    jpg_data += b"JFIF\x00"  # Identifier
    jpg_data += b"\x01\x01"  # Version
    jpg_data += b"\x00"  # Units
    jpg_data += struct.pack(">HH", 1, 1)  # Density
    jpg_data += b"\x00\x00"  # Thumbnail
    jpg_data += b"\xff\xd9"  # EOI
    path = tmp_path / "test.jpg"
    path.write_bytes(bytes(jpg_data))
    return path


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """Erstellt eine minimale gueltige PNG-Datei."""
    png_data = b"\x89PNG\r\n\x1a\n"  # PNG signature
    # IHDR chunk (minimal)
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    import zlib

    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    png_data += struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    png_data += struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    path = tmp_path / "test.png"
    path.write_bytes(png_data)
    return path


@pytest.fixture
def sample_invalid_file(tmp_path: Path) -> Path:
    """Erstellt eine Datei mit ungueltiger Extension."""
    path = tmp_path / "malware.exe"
    path.write_bytes(b"MZ\x90\x00fake executable content")
    return path


@pytest.fixture
def sample_pdf_with_text(tmp_path: Path) -> Path:
    """Erstellt ein PDF mit eingebettetem Text (fuer pdfplumber-Extraktion)."""
    try:
        import pdfplumber  # noqa: F401
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab nicht installiert")

    path = tmp_path / "rechnung.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(100, 750, "Rechnung Nr. 2024-001")
    c.drawString(100, 730, "Firma Muster GmbH")
    c.drawString(100, 710, "Betrag: 119,00 EUR")
    c.drawString(100, 690, "Datum: 15.01.2024")
    c.save()
    return path


@pytest.fixture
def sample_image_with_text(tmp_path: Path) -> Path:
    """Erstellt ein Bild mit Text (fuer OCR-Tests)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 10), "Rechnung Nr. 2024-001", fill="black", font=font)
    draw.text((10, 40), "Firma Muster GmbH", fill="black", font=font)
    draw.text((10, 70), "Betrag: 119,00 EUR", fill="black", font=font)
    path = tmp_path / "rechnung.png"
    img.save(str(path))
    return path


@pytest.fixture
def mock_ollama_response() -> dict:
    """Simulierte Ollama-Antwort fuer die kombinierte Analyse."""
    return {
        "model": "llama3.2",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "document_type": "RECHNUNG",
                "confidence": 0.92,
                "title": "Rechnung Nr. 2024-001",
                "sender": "Firma Muster GmbH",
                "recipient": "Max Mustermann",
                "document_date": "2024-01-15",
                "amount": 119.00,
                "currency": "EUR",
                "reference_number": "2024-001",
                "tags": ["rechnung", "dienstleistung"],
                "summary": "Rechnung der Firma Muster GmbH ueber 119 EUR.",
                "tax_relevant": False,
                "tax_category": None,
                "tax_year": None,
                "warranty_info": {
                    "has_warranty": False,
                    "product_name": None,
                    "purchase_date": None,
                    "warranty_duration_months": None,
                    "warranty_end_date": None,
                    "store_name": None,
                },
                "needs_review": False,
                "review_questions": [],
            }, ensure_ascii=False),
        },
    }
