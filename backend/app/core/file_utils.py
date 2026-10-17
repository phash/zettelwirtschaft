import re
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileTypeInfo:
    """Eigenschaften eines unterstuetzten Dateityps (Single Source fuer
    Validierung, Thumbnail, OCR und Download-MIME)."""

    magic: tuple[bytes, ...]
    mime: str
    kind: str  # "pdf" oder "image"


# Registry aller unterstuetzten Dateitypen (kanonische Extension -> Info).
FILE_TYPES: dict[str, FileTypeInfo] = {
    "pdf": FileTypeInfo((b"%PDF",), "application/pdf", "pdf"),
    "jpg": FileTypeInfo((b"\xff\xd8\xff",), "image/jpeg", "image"),
    "jpeg": FileTypeInfo((b"\xff\xd8\xff",), "image/jpeg", "image"),
    "png": FileTypeInfo((b"\x89PNG\r\n\x1a\n",), "image/png", "image"),
    "tiff": FileTypeInfo((b"II\x2a\x00", b"MM\x00\x2a"), "image/tiff", "image"),
    "bmp": FileTypeInfo((b"BM",), "image/bmp", "image"),
}

# Alternative Schreibweisen -> kanonische Extension. ".tif" bietet z.B. der
# Datei-Dialog der UploadView an; ohne Alias wurde es von der Validierung
# abgelehnt und vom Watch-Ordner uebersprungen.
FILE_TYPE_ALIASES: dict[str, str] = {"tif": "tiff"}

# Magic Bytes je Extension (inkl. Aliase) — fuer Aufrufer, die nur die
# Signaturen brauchen (z.B. E-Mail-Anhang-Erkennung).
MAGIC_BYTES: dict[str, list[bytes]] = {
    ext: list(info.magic) for ext, info in FILE_TYPES.items()
} | {alias: list(FILE_TYPES[target].magic) for alias, target in FILE_TYPE_ALIASES.items()}

# Maximale Laenge der zu pruefenden Bytes
_MAX_MAGIC_LEN = max(len(sig) for info in FILE_TYPES.values() for sig in info.magic)


def normalize_file_type(ext: str) -> str:
    """Extension/Dateityp kanonisieren: Kleinbuchstaben, ohne Punkt, Alias aufgeloest."""
    ext_lower = ext.lower().lstrip(".")
    return FILE_TYPE_ALIASES.get(ext_lower, ext_lower)


def get_file_type_info(ext: str) -> FileTypeInfo | None:
    """Registry-Eintrag fuer eine Extension (Aliase werden aufgeloest)."""
    return FILE_TYPES.get(normalize_file_type(ext))


def is_image_type(ext: str) -> bool:
    info = get_file_type_info(ext)
    return info is not None and info.kind == "image"


def is_pdf_type(ext: str) -> bool:
    info = get_file_type_info(ext)
    return info is not None and info.kind == "pdf"


def mime_type_for(ext: str) -> str:
    """MIME-Typ fuer Download/Vorschau; unbekannte Typen als application/octet-stream."""
    info = get_file_type_info(ext)
    return info.mime if info else "application/octet-stream"


def validate_magic_bytes(path: Path, ext: str) -> bool:
    """Prueft ob die Magic Bytes einer Datei zur Extension passen."""
    info = get_file_type_info(ext)
    if info is None:
        return False

    try:
        with open(path, "rb") as f:
            header = f.read(_MAX_MAGIC_LEN)
    except OSError:
        return False

    return any(header.startswith(sig) for sig in info.magic)


def sanitize_filename(name: str) -> str:
    """Bereinigt einen Dateinamen fuer sichere Speicherung."""
    # Nur Basename verwenden (Path Traversal verhindern)
    name = Path(name).name
    # Ungueltige Zeichen ersetzen
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Mehrfache Unterstriche zusammenfassen
    name = re.sub(r"_+", "_", name)
    # Fuehrende/nachfolgende Punkte und Leerzeichen entfernen
    name = name.strip(". ")
    return name or "unnamed"


def generate_stored_filename(original_name: str) -> str:
    """Generiert einen eindeutigen Dateinamen mit UUID-Prefix."""
    sanitized = sanitize_filename(original_name)
    prefix = uuid.uuid4().hex[:12]
    return f"{prefix}_{sanitized}"


def get_file_extension(name: str) -> str:
    """Gibt die Extension einer Datei in Kleinbuchstaben zurueck (ohne Punkt)."""
    ext = Path(name).suffix.lower().lstrip(".")
    return ext
